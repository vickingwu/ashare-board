#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""编排器：发现 etl/panels/ 下的全部面板，逐个构建，写 data/<id>.json 与 data/manifest.json。

用法：
    python etl/build_data.py                 # 构建全部面板
    python etl/build_data.py --only coverage # 只构建指定面板（逗号分隔）
    python etl/build_data.py --verbose       # 打印 DEBUG（每个 HTTP 请求）

约定：
* 面板模块导出 PANEL_ID / TITLE / 可选 STATIC / build() -> {asOf, sources, notes, data}
* 面板可导出 COMPACT_JSON = True，输出不缩进，用于大文件（默认 indent=1）
* 任一面板失败不影响其它面板；失败面板的旧 JSON 保留，manifest 里 ok=false + error
* 第二轮：全部面板跑完后，对因网络 / 限流类错误（``common.is_network_error``：连接重置、超时、HTTP 403 / 429 / 5xx、
  重试耗尽、Baostock 网络错误、熔断）失败的面板暂停 SECOND_PASS_PAUSE（60 秒）后再构建一次，之后才写 manifest——
  一段短暂的坏网络不至于让一个面板整天停在旧数据（2026-10-24 runner 上 widebase 就是这样丢了一天）
* 无变化不改写：新产出除 ``generated`` 外与现有文件完全相同时不重写文件（``generated`` 沿用旧值），manifest 也只在
  任一面板条目变化时才写——周末 / 节假日的暖缓存空跑于是不产生任何文件改动，Actions 上「没有可提交的变化」
* 退出码：0 至少一个面板成功；1 全部失败；2 参数错误
* 自测钩子：环境变量 ASHARE_FORCE_FAIL=coverage[,loss] 强制对应面板抛错（不发请求）
"""
from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etl.common import (DATA_DIR, _fmt_exc, get_logger, is_network_error, iso_now, read_json,  # noqa: E402
                        write_json_atomic)
import etl.panels  # noqa: E402

SECOND_PASS_PAUSE = 60.0            # 第二轮前的暂停（秒）；测试里打成 0
MANIFEST_COMPARE_KEYS = ("ok", "asOf", "generated", "error", "static")   # durationSec 不算变化


def discover_panels():
    mods = []
    for info in sorted(pkgutil.iter_modules(etl.panels.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"etl.panels.{info.name}")
        if hasattr(mod, "PANEL_ID") and hasattr(mod, "build"):
            mods.append(mod)
    return mods


def unchanged_except_generated(prev, new) -> bool:
    """现有文件与新产出除 ``generated`` 外是否完全相同（JSON 值级比较）。``prev`` 不是合法外壳时为 False。"""
    if not isinstance(prev, dict) or not isinstance(prev.get("generated"), str):
        return False
    a = {k: v for k, v in prev.items() if k != "generated"}
    b = {k: v for k, v in new.items() if k != "generated"}
    return a == b


def manifest_entries_changed(old_entries, new_entries) -> bool:
    """两份 manifest.panels 在 MANIFEST_COMPARE_KEYS 上是否有差别（面板集合不同也算变化）。"""
    old_entries = old_entries or {}
    if set(old_entries) != set(new_entries):
        return True
    for pid, e in new_entries.items():
        o = old_entries.get(pid) or {}
        if any(o.get(k) != e.get(k) for k in MANIFEST_COMPARE_KEYS):
            return True
    return False


def retry_candidates(panels, entries):
    """第一轮之后值得再试一次的面板：失败且错误文本属于网络 / 限流类。"""
    return [m for m in panels if not entries[m.PANEL_ID]["ok"] and is_network_error(entries[m.PANEL_ID]["error"])]


def reset_for_second_pass():
    """清掉进程内记住的「本次已失败」状态，让第二轮真的重试：全市场日线库的增量记忆、中证熔断。"""
    from etl import market_state
    from etl.sources import csindex_hist
    market_state.reset_update_memo()
    csindex_hist.reset_circuit()


def build_one(mod, logger):
    pid = mod.PANEL_ID
    static = bool(getattr(mod, "STATIC", False))
    out_path = DATA_DIR / f"{pid}.json"
    forced = [s.strip() for s in os.environ.get("ASHARE_FORCE_FAIL", "").split(",") if s.strip()]
    t0 = time.monotonic()
    try:
        if pid in forced:
            raise RuntimeError("forced failure (ASHARE_FORCE_FAIL)")
        res = mod.build()
        envelope = {
            "panel": pid,
            "title": getattr(mod, "TITLE", pid),
            "generated": iso_now(),
            "asOf": res["asOf"],
            "sources": list(res.get("sources", [])),
            "notes": list(res.get("notes", [])),
            "data": res["data"],
        }
        prev = read_json(out_path, default=None)
        if unchanged_except_generated(prev, envelope):
            envelope["generated"] = prev["generated"]
            dur = round(time.monotonic() - t0, 1)
            logger.info("面板 %s 完成，asOf=%s，与现有文件无差别（沿用 generated=%s），%.1fs", pid, envelope["asOf"],
                        envelope["generated"], dur)
        else:
            write_json_atomic(out_path, envelope, compact=bool(getattr(mod, "COMPACT_JSON", False)))
            dur = round(time.monotonic() - t0, 1)
            logger.info("面板 %s 完成，asOf=%s，%.1fs", pid, envelope["asOf"], dur)
        return {"ok": True, "asOf": envelope["asOf"], "generated": envelope["generated"],
                "error": None, "durationSec": dur, "static": static}
    except Exception as e:  # noqa: BLE001 — 面板级隔离，任何异常都不能拖垮其它面板
        dur = round(time.monotonic() - t0, 1)
        logger.exception("面板 %s 失败: %s", pid, _fmt_exc(e))
        prev = read_json(out_path, default=None) or {}
        return {"ok": False, "asOf": prev.get("asOf"), "generated": prev.get("generated"),
                "error": _fmt_exc(e), "durationSec": dur, "static": static}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="构建 A 股看板数据文件")
    ap.add_argument("--only", help="只构建这些面板（逗号分隔 id）")
    ap.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    args = ap.parse_args(argv)

    logger = get_logger("etl.build", verbose=args.verbose)
    panels = discover_panels()
    known = [m.PANEL_ID for m in panels]
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [w for w in wanted if w not in known]
        if unknown:
            print(f"未知面板: {', '.join(unknown)}；可用: {', '.join(known)}", file=sys.stderr)
            return 2
        panels = [m for m in panels if m.PANEL_ID in wanted]

    logger.info("开始构建 %d 个面板: %s", len(panels), ", ".join(m.PANEL_ID for m in panels))
    t_all = time.monotonic()
    manifest_path = DATA_DIR / "manifest.json"
    manifest = read_json(manifest_path, default=None) or {}
    old_entries = dict(manifest.get("panels") or {})
    entries = dict(old_entries)
    for mod in panels:
        entries[mod.PANEL_ID] = build_one(mod, logger)
    retry = retry_candidates(panels, entries)
    if retry:
        logger.warning("第二轮：%d 个面板因网络 / 限流错误失败（%s），%.0f s 后再试一次", len(retry),
                       ", ".join(m.PANEL_ID for m in retry), SECOND_PASS_PAUSE)
        time.sleep(SECOND_PASS_PAUSE)
        reset_for_second_pass()
        for mod in retry:
            entries[mod.PANEL_ID] = build_one(mod, logger)
    if manifest_entries_changed(old_entries, entries):
        write_json_atomic(manifest_path, {"generated": iso_now(), "panels": entries})
    else:
        logger.info("manifest 无变化（%d 个面板的产出都与现有文件相同），不改写", len(panels))

    print()
    print(f"{'panel':<10} | {'ok':<5} | {'asOf':<10} | {'seconds':>7} | error")
    print("-" * 70)
    for pid in sorted(e for e in entries if e in {m.PANEL_ID for m in panels}):
        e = entries[pid]
        print(f"{pid:<10} | {str(e['ok']).lower():<5} | {str(e['asOf'] or '-'):<10} | "
              f"{e['durationSec']:>7.1f} | {e['error'] or ''}")
    total = time.monotonic() - t_all
    print(f"\n总耗时 {total:.1f}s；manifest -> {manifest_path}")
    logger.info("总耗时 %.1fs", total)
    ok_count = sum(1 for m in panels if entries[m.PANEL_ID]["ok"])
    return 0 if ok_count >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
