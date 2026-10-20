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

from etl.common import DATA_DIR, _fmt_exc, get_logger, iso_now, read_json, write_json_atomic  # noqa: E402
import etl.panels  # noqa: E402


def discover_panels():
    mods = []
    for info in sorted(pkgutil.iter_modules(etl.panels.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"etl.panels.{info.name}")
        if hasattr(mod, "PANEL_ID") and hasattr(mod, "build"):
            mods.append(mod)
    return mods


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
    entries = dict(manifest.get("panels") or {})
    for mod in panels:
        entries[mod.PANEL_ID] = build_one(mod, logger)
    write_json_atomic(manifest_path, {"generated": iso_now(), "panels": entries})

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
