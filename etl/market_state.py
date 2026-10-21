# -*- coding: utf-8 -*-
"""全市场日线库 ``cache/market/``：按股票存放的后复权日线 + 清单 + 交易日历 + 日度聚合；冷启动 / 日增量 / 重算 / 种子 / CLI。

为什么要有这个库：市场趋势温度（站上 MA250 的比例）与恐贪指数（涨跌家数、涨跌停、MA50 宽度）需要
沪深 A 股每只股票自 2004 年起的日线。Baostock 个股全史每只 6–8 s（5,563 只 ≈ 9–12 h），GitHub Actions
跑不完，于是：**本地冷启动一次 → 导出种子 ``state/market_seed.zip``（每只最近 252 行 + 清单 + 聚合 + 日历，
≤ 15 MB）入库 → Actions / 新机器从种子初始化，之后每个缺失交易日只用一次 ``query_daily_history_k_AStock``
全市场横截面追平**（预算 ``ASHARE_MARKET_BUDGET_MIN`` 分钟，默认 18）。

目录布局（本地、种子、Actions 三处格式相同）：
* ``manifest.json`` ``{version, kind: "full"|"tail", start, coldDone, lastDate, nStocks, updatedAt, universeFetchedAt,
  skippedDays[], codes:{key:{name, ipo, out, status, first, last, n, lastClose}}}``，key = ``sh600519``；
  ``lastClose`` 是该股最后一行的后复权收盘（float64，续链用，**不是**文件里的 float32）；
* ``agg.json``（紧凑）``{dates[], nTraded[], n250[], above250[], n50[], above50[], adv[], dec[], flat[], limitUp[],
  limitDown[], nLimitEligible[]}``；``calendar.json`` ``{fetchedAt, dates[]}``；``progress.json`` 只在冷启动期间存在；
* ``stocks/<key>.bin`` = 12 字节头 ``struct '<4sBBHI'``（magic ``ASHM``、版本 1、保留、保留、行数 n）+ 四段定长
  ``array``：``'i'`` 日期 yyyymmdd、``'f'`` 后复权收盘、``'f'`` 涨跌幅 %、``'B'`` 标志（bit0 = isST）→ 13 字节/行，
  本机小端（目标机器全是）；写入一律 tmp + ``os.replace``。全库 ≈ 1,650 万行 ≈ 215 MB，种子 tail ≈ 12–15 MB。

口径：
* 只存交易日（Baostock ``tradestatus == "1"`` 且 close > 0）；停牌日没有行；
* 后复权续链 ``hfq(t) = hfq(t−1) × (1 + pctChg/100)``——与 Baostock 自己的后复权构造一致（``pctChg`` 是
  交易所涨跌幅，对除权除息已修正，复权前后相同）；横截面里首次出现的代码从原始收盘起链（Baostock 首日后复权 = 原始收盘）；
  停牌多日后复牌同样成立（停牌日没有收益）；
* 聚合按「当日有行的股票」统计：``n250/above250`` 要求含当日 ≥ 250 行（MA250 = 最近 250 行后复权收盘均值，
  above = 收盘 > MA250），``n50/above50`` 同理 50；``adv/dec/flat`` 按 pctChg 正负零，股票的第一行不计
  （其 pctChg 是相对发行价）；涨跌停需 ≥ 5 行：688 ±19.8，30 开头 2020-08-24 起 ±19.8（含 ST）、之前 ±9.8/ST ±4.8，
  60/00 开头 ±9.8/ST ±4.8——阈值已含 0.2 个百分点容差以吸收价格最小变动单位的四舍五入（2.5 元以下的低价股
  可能四舍五入到阈值以下，属已知偏差）。``pct250 = above250 / n250 × 100``。

运行规则：
* Baostock 单会话、顺序查询；冷启动可随时杀掉，``progress.json`` 每 20 只存档，重跑同一命令即续跑；
  绝不同时跑两个冷启动，也不要在它运行时跑别的 Baostock 任务（``--rebuild`` 与 ``ASHARE_MARKET_ALLOW_PARTIAL=1``
  的面板构建不联网，安全）；
* 冷启动收尾（``finalize_cold``）：跨越 18:00 发布时刻或跨天的运行会让早抓的股票少一两天，用最后几天的横截面
  对比已存的最后一行（一致率需 ≥ 99 %）并把差的天补上，然后重算聚合、置 ``coldDone``、删 ``progress.json``；
* 日增量（``update_incremental``）：先用缓存日历算缺失交易日，缺失为空且日历够用时**不连 Baostock**；
  当天 0 行 = 尚未发布（正常停止）；过去某天 0 行 → PanelError（下次重试或 ``--skip-day``）；
  行数不足前一日 70 % → PanelError；每天存档一次；预算用尽 → ``lagging``（面板照常出图并提示落后）；
* ``skip_day`` 把某天记入 ``skippedDays`` 并推进 ``lastDate``：那一天的收益从每只股票的链里永久缺失（已记录的漂移）；
* ``rebuild_agg`` 只读股票文件、只写 ``agg.json``，不碰清单，冷启动运行期间也可以跑。
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import os
import struct
import sys
import time
import zipfile
from array import array
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from etl import compute
from etl.common import (CACHE_DIR, STATE_DIR, PanelError, cache_is_fresh, env_int, get_logger, iso_now,
                        market_closed, read_json, today_str, write_json_atomic)
from etl.sources import baostock_src
from etl.sources.baostock_src import (BaostockError, Session, fetch_cross_section, fetch_history,
                                      fetch_stock_basic, fetch_trade_dates)

log = logging.getLogger("etl.market")

MARKET_DIR = CACHE_DIR / "market"
STOCKS_DIR = MARKET_DIR / "stocks"
MANIFEST_FILE = "manifest.json"
AGG_FILE = "agg.json"
CALENDAR_FILE = "calendar.json"
PROGRESS_FILE = "progress.json"
SEED_PATH = STATE_DIR / "market_seed.zip"
HISTORY_START = baostock_src.HISTORY_START
FORMAT_VERSION = 1
MAGIC = b"ASHM"
HEADER = struct.Struct("<4sBBHI")
ROW_BYTES = 4 + 4 + 4 + 1
TAIL_ROWS = 252
MA_LONG = 250
MA_SHORT = 50
MIN_ROWS_FOR_LIMIT = 5
CHECKPOINT_EVERY = 20
DEFAULT_BUDGET_MIN = 18
MAX_FAILED_SHARE = 0.03
MIN_ROW_SHARE_VS_PREV = 0.70
CALENDAR_AHEAD_DAYS = 60
CALENDAR_MIN_AHEAD_DAYS = 30
LIMIT_TOL = 0.2
CYB_20PCT_FROM = "2020-08-24"
ENV_BUDGET = "ASHARE_MARKET_BUDGET_MIN"
ENV_ALLOW_PARTIAL = "ASHARE_MARKET_ALLOW_PARTIAL"
MAX_SEED_BYTES = 15 * 1024 * 1024
MIN_SEED_STOCKS = 5000
PATCH_LOOKBACK_DAYS = 10
MAX_UNEQUAL_SHARE = 0.01
PCT_COMPARE_TOL = 1e-4
MAX_CONSECUTIVE_FAILURES = 10

# 涨跌停阈值（已扣 LIMIT_TOL = 0.2 个百分点容差）：主板 10 %、ST 5 %、科创/创业板（2020-08-24 起）20 %
LIMIT_MAIN = 9.8
LIMIT_ST = 4.8
LIMIT_20 = 19.8

AGG_KEYS = ("nTraded", "n250", "above250", "n50", "above50", "adv", "dec", "flat", "limitUp", "limitDown",
            "nLimitEligible")
FLAG_KEYS = ("traded", "has250", "above250", "has50", "above50", "adv", "dec", "flat", "limitEligible", "limitUp",
             "limitDown")
_FLAG_TO_AGG = {"traded": "nTraded", "has250": "n250", "above250": "above250", "has50": "n50", "above50": "above50",
                "adv": "adv", "dec": "dec", "flat": "flat", "limitEligible": "nLimitEligible", "limitUp": "limitUp",
                "limitDown": "limitDown"}

if array("i").itemsize != 4 or array("f").itemsize != 4:   # pragma: no cover — 所有目标平台都是 4 字节
    raise ImportError("market_state 需要 4 字节的 array('i') / array('f')")


class StockSeries(NamedTuple):
    dates: Sequence[int]
    close: Sequence[float]
    pct: Sequence[float]
    flags: Sequence[int]


def _monotonic() -> float:
    return time.monotonic()


# --------------------------------------------------------------------------- 纯函数：编码、日期、阈值、标志
def pack_series(dates: Sequence[int], close: Sequence[float], pct: Sequence[float], flags: Sequence[int]) -> bytes:
    """四个平行序列 → 12 字节头 + 'i' 日期 + 'f' 收盘 + 'f' 涨跌幅 + 'B' 标志。长度不一致抛 PanelError。"""
    n = len(dates)
    if not (len(close) == n and len(pct) == n and len(flags) == n):
        raise PanelError(f"股票序列长度不一致: dates {n} close {len(close)} pct {len(pct)} flags {len(flags)}")
    if n > 0xFFFFFFFF:
        raise PanelError("股票序列过长")
    head = HEADER.pack(MAGIC, FORMAT_VERSION, 0, 0, n)
    return (head + array("i", dates).tobytes() + array("f", close).tobytes() + array("f", pct).tobytes()
            + array("B", flags).tobytes())


def unpack_series(data: bytes) -> StockSeries:
    """``pack_series`` 的逆：校验 magic / 版本 / 长度，返回四个 ``array``。"""
    if len(data) < HEADER.size:
        raise PanelError(f"股票文件过短 ({len(data)} 字节)")
    magic, version, _r1, _r2, n = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise PanelError(f"股票文件 magic 不对: {magic!r}")
    if version != FORMAT_VERSION:
        raise PanelError(f"股票文件版本 {version} ≠ {FORMAT_VERSION}")
    expected = HEADER.size + n * ROW_BYTES
    if len(data) != expected:
        raise PanelError(f"股票文件长度 {len(data)} ≠ 期望 {expected}（n={n}）")
    off = HEADER.size
    dates = array("i")
    dates.frombytes(data[off:off + 4 * n])
    off += 4 * n
    close = array("f")
    close.frombytes(data[off:off + 4 * n])
    off += 4 * n
    pct = array("f")
    pct.frombytes(data[off:off + 4 * n])
    off += 4 * n
    flags = array("B")
    flags.frombytes(data[off:off + n])
    return StockSeries(dates, close, pct, flags)


def date_int(s: str) -> int:
    """``"2026-10-20"`` → 20261020。"""
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        raise PanelError(f"日期格式应为 YYYY-MM-DD: {s!r}")
    return int(s[:4]) * 10000 + int(s[5:7]) * 100 + int(s[8:10])


def date_str(d: int) -> str:
    """20261020 → ``"2026-10-20"``。"""
    return f"{d // 10000:04d}-{(d // 100) % 100:02d}-{d % 100:02d}"


def shift_days(s: str, days: int) -> str:
    return (compute.parse_date(s) + timedelta(days=days)).isoformat()


def prev_trading_day(cal: Sequence[str], d: str) -> Optional[str]:
    """日历中严格早于 d 的最后一个交易日；没有则 None。"""
    i = bisect.bisect_left(cal, d)
    return cal[i - 1] if i > 0 else None


def limit_threshold(code6: str, date: str, is_st: bool) -> float:
    """涨跌停判定阈值（%，已含 0.2 个百分点容差）。

    688xxx → 19.8；30xxxx → 2020-08-24 起 19.8（含 ST），之前 9.8 / ST 4.8；其它（60/00）→ 9.8 / ST 4.8。
    """
    if code6.startswith("688"):
        return LIMIT_20
    if code6.startswith("30") and date >= CYB_20PCT_FROM:
        return LIMIT_20
    return LIMIT_ST if is_st else LIMIT_MAIN


def stock_day_flags(closes_tail: Sequence[float], pct: float, is_st: bool, n_rows: int, code6: str, date: str
                    ) -> Dict[str, int]:
    """一只股票某交易日的计数标志（0/1）。

    ``closes_tail`` 必须以当日收盘结尾、至少含最近 ``MA_LONG`` 行（若 n_rows ≥ MA_LONG）；``n_rows`` 是该股
    截至当日的已存行数：≥ 250/50 决定 has250/has50，≥ 2 才计 adv/dec/flat，≥ MIN_ROWS_FOR_LIMIT 才判涨跌停。
    """
    out = {k: 0 for k in FLAG_KEYS}
    out["traded"] = 1
    if not closes_tail:
        raise PanelError("closes_tail 为空")
    close = closes_tail[-1]
    if n_rows >= MA_LONG:
        if len(closes_tail) < MA_LONG:
            raise PanelError(f"closes_tail 只有 {len(closes_tail)} 行，不足 MA_LONG")
        out["has250"] = 1
        if close > sum(closes_tail[-MA_LONG:]) / MA_LONG:
            out["above250"] = 1
    if n_rows >= MA_SHORT:
        if len(closes_tail) < MA_SHORT:
            raise PanelError(f"closes_tail 只有 {len(closes_tail)} 行，不足 MA_SHORT")
        out["has50"] = 1
        if close > sum(closes_tail[-MA_SHORT:]) / MA_SHORT:
            out["above50"] = 1
    if n_rows >= 2:
        if pct > 0:
            out["adv"] = 1
        elif pct < 0:
            out["dec"] = 1
        else:
            out["flat"] = 1
    if n_rows >= MIN_ROWS_FOR_LIMIT:
        out["limitEligible"] = 1
        thr = limit_threshold(code6, date, bool(is_st))
        if pct >= thr:
            out["limitUp"] = 1
        elif pct <= -thr:
            out["limitDown"] = 1
    return out


def empty_agg() -> Dict[str, list]:
    agg: Dict[str, list] = {"dates": []}
    for k in AGG_KEYS:
        agg[k] = []
    return agg


def agg_append(agg: Dict[str, list], date: str, counters: Dict[str, int]) -> None:
    if agg["dates"] and date <= agg["dates"][-1]:
        raise PanelError(f"聚合日期必须递增: {agg['dates'][-1]} -> {date}")
    agg["dates"].append(date)
    for k in AGG_KEYS:
        agg[k].append(int(counters.get(k, 0)))


def agg_from_counters(by_date: Dict[str, Dict[str, int]]) -> Dict[str, list]:
    agg = empty_agg()
    for d in sorted(by_date):
        agg_append(agg, d, by_date[d])
    return agg


def add_flags(counters: Dict[str, int], flags: Dict[str, int]) -> None:
    """把 ``stock_day_flags`` 的 0/1 加进以 AGG_KEYS 为键的计数器。"""
    for fk, ak in _FLAG_TO_AGG.items():
        counters[ak] = counters.get(ak, 0) + flags.get(fk, 0)


# --------------------------------------------------------------------------- 存储
class MarketStore:
    """``cache/market/`` 的读写；``root`` 可换成临时目录做测试。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else MARKET_DIR
        self.stocks_dir = self.root / "stocks"

    # ---- 目录 / 路径
    def ensure_dirs(self) -> None:
        self.stocks_dir.mkdir(parents=True, exist_ok=True)

    def stock_path(self, key: str) -> Path:
        return self.stocks_dir / f"{key}.bin"

    # ---- 股票文件
    def read_stock(self, key: str) -> StockSeries:
        p = self.stock_path(key)
        if not p.is_file():
            return StockSeries(array("i"), array("f"), array("f"), array("B"))
        return unpack_series(p.read_bytes())

    def write_stock(self, key: str, series: StockSeries) -> None:
        self.stocks_dir.mkdir(parents=True, exist_ok=True)
        p = self.stock_path(key)
        tmp = p.with_suffix(".bin.tmp")
        tmp.write_bytes(pack_series(series.dates, series.close, series.pct, series.flags))
        os.replace(tmp, p)

    def append_rows(self, key: str, rows: Sequence[Tuple[int, float, float, int]]) -> StockSeries:
        """追加 ``(date_int, close, pct, flag)`` 行（日期必须大于已有最后一行），返回更新后的序列。"""
        s = self.read_stock(key)
        dates = array("i", s.dates)
        close = array("f", s.close)
        pct = array("f", s.pct)
        flags = array("B", s.flags)
        for d, c, p, f in rows:
            if dates and d <= dates[-1]:
                raise PanelError(f"{key} 追加日期 {d} 不大于已有最后一行 {dates[-1]}")
            dates.append(int(d))
            close.append(float(c))
            pct.append(float(p))
            flags.append(int(f) & 0xFF)
        series = StockSeries(dates, close, pct, flags)
        self.write_stock(key, series)
        return series

    # ---- JSON 文件
    def load_manifest(self) -> Optional[Dict[str, Any]]:
        return read_json(self.root / MANIFEST_FILE, default=None)

    def save_manifest(self, manifest: Dict[str, Any]) -> None:
        write_json_atomic(self.root / MANIFEST_FILE, manifest, compact=True)

    def load_agg(self) -> Dict[str, list]:
        agg = read_json(self.root / AGG_FILE, default=None)
        if not agg or not isinstance(agg, dict) or "dates" not in agg:
            return empty_agg()
        for k in AGG_KEYS:
            agg.setdefault(k, [0] * len(agg["dates"]))
        return agg

    def save_agg(self, agg: Dict[str, list]) -> None:
        write_json_atomic(self.root / AGG_FILE, agg, compact=True)

    def load_calendar(self) -> Optional[Dict[str, Any]]:
        return read_json(self.root / CALENDAR_FILE, default=None)

    def save_calendar(self, cal: Dict[str, Any]) -> None:
        write_json_atomic(self.root / CALENDAR_FILE, cal)

    def load_progress(self) -> Optional[Dict[str, Any]]:
        return read_json(self.root / PROGRESS_FILE, default=None)

    def save_progress(self, progress: Dict[str, Any]) -> None:
        write_json_atomic(self.root / PROGRESS_FILE, progress)

    def delete_progress(self) -> None:
        p = self.root / PROGRESS_FILE
        if p.is_file():
            p.unlink()

    # ---- 概览
    def status(self) -> Dict[str, Any]:
        manifest = self.load_manifest() or {}
        progress = self.load_progress()
        agg = self.load_agg()
        cal = self.load_calendar() or {}
        files = list(self.stocks_dir.glob("*.bin")) if self.stocks_dir.is_dir() else []
        total_bytes = sum(p.stat().st_size for p in files)
        codes = manifest.get("codes") or {}
        out: Dict[str, Any] = {
            "root": str(self.root),
            "exists": bool(manifest),
            "kind": manifest.get("kind"),
            "coldDone": bool(manifest.get("coldDone", False)),
            "start": manifest.get("start"),
            "lastDate": manifest.get("lastDate"),
            "nStocks": sum(1 for c in codes.values() if c.get("n")),
            "nCodes": len(codes),
            "nFiles": len(files),
            "bytes": total_bytes,
            "aggDays": len(agg["dates"]),
            "aggLastDate": agg["dates"][-1] if agg["dates"] else None,
            "calendarLast": (cal.get("dates") or [None])[-1],
            "skippedDays": manifest.get("skippedDays") or [],
            "updatedAt": manifest.get("updatedAt"),
            "pending": len(progress["pending"]) if progress else 0,
            "done": progress.get("done") if progress else None,
            "failed": len(progress.get("failed") or []) if progress else 0,
            "total": progress.get("total") if progress else None,
            "spentSec": round(float(progress.get("spentSec") or 0.0), 1) if progress else None,
        }
        return out


# --------------------------------------------------------------------------- 日历
def ensure_calendar(store: MarketStore, sess: Session) -> List[str]:
    """交易日历（升序，到今天 + CALENDAR_AHEAD_DAYS）。

    缺失 → 从 HISTORY_START 全取；已有 → 末日不足今天 + CALENDAR_MIN_AHEAD_DAYS 或缓存不新鲜时，从
    ``min(末日, 今天) − CALENDAR_MIN_AHEAD_DAYS`` 起重取到今天 + CALENDAR_AHEAD_DAYS 并接上：重叠的过去部分
    （≤ min(末日, 今天)）必须与已有日历逐日一致，否则抛 PanelError（前缀变化意味着服务端日历改了，库里的
    日期无法再信任）；未来部分以新取的为准（节假日安排可能调整）。
    """
    today = today_str()
    ahead = shift_days(today, CALENDAR_AHEAD_DAYS)
    cal = store.load_calendar()
    dates: List[str] = list(cal.get("dates") or []) if cal else []
    if dates:
        need = dates[-1] < shift_days(today, CALENDAR_MIN_AHEAD_DAYS) or not cache_is_fresh(cal.get("fetchedAt"))
        if not need:
            return dates
        hi = min(dates[-1], today)
        start = max(HISTORY_START, shift_days(hi, -CALENDAR_MIN_AHEAD_DAYS))
        fresh = fetch_trade_dates(sess, start, ahead)
        old_overlap = [d for d in dates if start <= d <= hi]
        new_overlap = [d for d in fresh if d <= hi]
        if old_overlap != new_overlap:
            raise PanelError("交易日历前缀变化，请 make clean-cache-market 后重建")
        merged = [d for d in dates if d < start] + fresh
    else:
        merged = fetch_trade_dates(sess, HISTORY_START, ahead)
    compute.assert_ascending(merged, "交易日历")
    if not merged:
        raise PanelError("Baostock 交易日历为空")
    store.save_calendar({"fetchedAt": iso_now(), "dates": merged})
    log.info("交易日历: %d 个交易日，%s → %s", len(merged), merged[0], merged[-1])
    return merged


# --------------------------------------------------------------------------- 冷启动
def _new_manifest(stocks: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    codes: Dict[str, Dict[str, Any]] = {}
    for s in stocks:
        codes[s["key"]] = {"name": s.get("name", ""), "ipo": s.get("ipo", ""), "out": s.get("out", ""),
                           "status": s.get("status", ""), "first": None, "last": None, "n": None, "lastClose": None}
    now = iso_now()
    return {"version": FORMAT_VERSION, "kind": "full", "start": HISTORY_START, "coldDone": False, "lastDate": None,
            "nStocks": 0, "updatedAt": now, "universeFetchedAt": now, "skippedDays": [], "codes": codes}


def _eligible(entry: Dict[str, Any]) -> bool:
    out = entry.get("out") or ""
    return out == "" or out >= HISTORY_START


def _store_history(store: MarketStore, manifest: Dict[str, Any], key: str,
                   rows: Sequence[Tuple[str, float, float, int]]) -> None:
    series = StockSeries([date_int(r[0]) for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                         [r[3] for r in rows])
    store.write_stock(key, series)
    e = manifest["codes"].setdefault(key, {"name": "", "ipo": "", "out": "", "status": "1"})
    e["first"] = rows[0][0] if rows else None
    e["last"] = rows[-1][0] if rows else None
    e["n"] = len(rows)
    e["lastClose"] = float(rows[-1][1]) if rows else None


def _count_stocks(manifest: Dict[str, Any]) -> int:
    return sum(1 for c in manifest["codes"].values() if c.get("n"))


def _checkpoint(store: MarketStore, manifest: Dict[str, Any], progress: Dict[str, Any], spent: float) -> None:
    manifest["nStocks"] = _count_stocks(manifest)
    manifest["updatedAt"] = iso_now()
    progress["spentSec"] = round(float(progress.get("spentSec") or 0.0) + spent, 1)
    progress["lastRunAt"] = iso_now()
    store.save_manifest(manifest)
    store.save_progress(progress)


def _fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}"


def cold_start(budget_min: Optional[float] = None, limit: Optional[int] = None, verbose: bool = False) -> Dict[str, Any]:
    """逐只抓全史建库（可中断、可续跑）；``pending`` 清空后做收尾校验并置 ``coldDone``。返回 ``store.status()``。"""
    store = MarketStore()
    store.ensure_dirs()
    manifest = store.load_manifest()
    if manifest and manifest.get("coldDone"):
        log.info("冷启动已完成（截至 %s），无需重复", manifest.get("lastDate"))
        return store.status()
    progress = store.load_progress()
    today = today_str()
    include_today = market_closed()
    t_run = _monotonic()
    deadline = None if budget_min is None else t_run + float(budget_min) * 60.0
    with Session() as sess:
        if manifest is None:
            stocks = fetch_stock_basic(sess)
            manifest = _new_manifest(stocks)
            progress = None
            log.info("冷启动开始：证券基本资料 %d 只（含已退市 %d 只）", len(stocks),
                     sum(1 for s in stocks if s.get("out")))
        if progress is None:
            pending = sorted(k for k, c in manifest["codes"].items() if c.get("n") is None and _eligible(c))
            done = sum(1 for c in manifest["codes"].values() if c.get("n") is not None)
            progress = {"startedAt": iso_now(), "total": len(pending) + done, "pending": pending, "done": done,
                        "failed": [], "spentSec": 0.0, "lastRunAt": iso_now()}
            store.save_manifest(manifest)
            store.save_progress(progress)
            log.info("冷启动待抓 %d 只（起点 %s）", len(pending), HISTORY_START)
        pending: List[str] = progress["pending"]
        total = int(progress.get("total") or (progress["done"] + len(pending) + len(progress["failed"])))
        n_this = 0
        t_last = _monotonic()
        streak: List[str] = []
        while pending:
            if limit is not None and n_this >= limit:
                log.info("冷启动达到 --limit %d，本次停止", limit)
                break
            if deadline is not None and _monotonic() >= deadline:
                log.info("冷启动预算 %.1f 分钟用尽，本次停止", budget_min)
                break
            key = pending[0]
            try:
                rows = fetch_history(sess, key, HISTORY_START, today)
            except BaostockError as e:
                log.warning("冷启动 %s 抓取失败: %s", key, e)
                progress["failed"].append(key)
                pending.pop(0)
                streak.append(key)
                if len(streak) >= MAX_CONSECUTIVE_FAILURES:
                    for k in streak:
                        progress["failed"].remove(k)
                    progress["pending"] = streak + pending
                    _checkpoint(store, manifest, progress, _monotonic() - t_last)
                    raise PanelError(f"冷启动连续 {len(streak)} 只失败（疑似网络中断），已存档，稍后重跑同一命令续跑")
                continue
            streak = []
            if rows and rows[-1][0] == today and not include_today:
                rows = rows[:-1]
            _store_history(store, manifest, key, rows)
            pending.pop(0)
            progress["done"] += 1
            n_this += 1
            if n_this % CHECKPOINT_EVERY == 0:
                now = _monotonic()
                _checkpoint(store, manifest, progress, now - t_last)
                t_last = now
                per = (now - t_run) / n_this
                log.info("冷启动 %d/%d，本次 %d 只，均 %.1f s/只，预计剩余 %s", progress["done"], total, n_this, per,
                         _fmt_eta(per * len(pending)))
        now = _monotonic()
        _checkpoint(store, manifest, progress, now - t_last)
        if n_this:
            log.info("冷启动本次 %d 只，%.0f s，均 %.1f s/只；累计 %d/%d，失败 %d，累计用时 %s",
                     n_this, now - t_run, (now - t_run) / n_this, progress["done"], total, len(progress["failed"]),
                     _fmt_eta(progress["spentSec"]))
        if not pending:
            failed = progress["failed"]
            if len(failed) > MAX_FAILED_SHARE * total:
                raise PanelError(f"冷启动失败股票过多 {len(failed)}/{total}（> {MAX_FAILED_SHARE:.0%}），前 20 个: {failed[:20]}")
            if failed:
                log.warning("冷启动有 %d 只抓取失败（≤ %.0f%%，日增量会在横截面里从原始收盘起链）: %s",
                            len(failed), MAX_FAILED_SHARE * 100, failed[:20])
            finalize_cold(store, sess, manifest)
    return store.status()


def finalize_cold(store: MarketStore, sess: Session, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """冷启动收尾：用最后几天的横截面校验/补齐早抓的股票，重算聚合，置 coldDone，删 progress.json。"""
    manifest = manifest if manifest is not None else store.load_manifest()
    if not manifest:
        raise PanelError("冷启动收尾：清单不存在")
    codes = manifest["codes"]
    cal = ensure_calendar(store, sess)
    lasts = [c["last"] for c in codes.values() if c.get("last")]
    if not lasts:
        raise PanelError("冷启动收尾：没有任何股票数据")
    last_date = max(lasts)
    cutoff = shift_days(last_date, -PATCH_LOOKBACK_DAYS)
    recent = [d for d in lasts if d >= cutoff]
    first_patch = min(recent)
    stale = [k for k, c in codes.items() if c.get("last") and c["last"] < cutoff and not (c.get("out") or "")]
    if stale:
        log.warning("冷启动收尾：%d 只在册股票的最后日期早于 %s（长期停牌），复牌出现在横截面时按停牌续链，例: %s",
                    len(stale), cutoff, stale[:10])
    patch_days = [d for d in cal if first_patch < d <= last_date]
    log.info("冷启动收尾：最后日期 %s，最早待补 %s，校验/补齐 %d 个交易日", last_date, first_patch, len(patch_days))
    # 每只股票的全史都抓到了至少 first_patch（最早一次抓取时 Baostock 已发布到那天），而 (first_patch, d) 的每个
    # 交易日都在这里按序处理过：某股在那些天不在横截面里 = 停牌，所以 last < d 的股票一律可以按链补上 d。
    for d in patch_days:
        rows = fetch_cross_section(sess, d)
        n_eq = n_ne = n_patch = 0
        di = date_int(d)
        for r in rows:
            e = codes.get(r["key"])
            if e is None or not e.get("n") or not e.get("last"):
                continue
            if e["last"] == d:
                s = store.read_stock(r["key"])
                if len(s.dates) and s.dates[-1] == di and abs(float(s.pct[-1]) - r["pct"]) <= PCT_COMPARE_TOL:
                    n_eq += 1
                else:
                    n_ne += 1
            elif e["last"] < d:
                hfq = float(e["lastClose"]) * (1.0 + r["pct"] / 100.0)
                series = store.append_rows(r["key"], [(di, hfq, r["pct"], r["is_st"])])
                e["last"] = d
                e["n"] = len(series.dates)
                e["lastClose"] = hfq
                n_patch += 1
        compared = n_eq + n_ne
        log.info("冷启动校验 %s：对比 %d 一致 %d 不一致，补齐 %d 只", d, compared, n_ne, n_patch)
        if compared and n_ne > MAX_UNEQUAL_SHARE * compared:
            store.save_manifest(manifest)
            raise PanelError(f"冷启动校验 {d}：{n_ne}/{compared} 只的最后一行与横截面不一致（> 1%），请检查")
        store.save_manifest(manifest)
    rebuild_agg(store, manifest)
    manifest["coldDone"] = True
    manifest["lastDate"] = last_date
    manifest["nStocks"] = _count_stocks(manifest)
    manifest["updatedAt"] = iso_now()
    store.save_manifest(manifest)
    store.delete_progress()
    log.info("冷启动完成：%d 只，截至 %s", manifest["nStocks"], last_date)
    return manifest


# --------------------------------------------------------------------------- 聚合重算
def _accumulate_series(by_date: Dict[int, List[int]], code6: str, dates: Sequence[int], close: Sequence[float],
                       pct: Sequence[float], flags: Sequence[int]) -> None:
    """一只股票全部行 → 累加到 ``by_date[date_int] = [11 个计数]``（与 ``stock_day_flags`` 同口径，滚动和 O(n)）。"""
    is_688 = code6.startswith("688")
    is_30 = code6.startswith("30")
    cyb_from = date_int(CYB_20PCT_FROM)
    sum_long = 0.0
    sum_short = 0.0
    n = len(dates)
    for i in range(n):
        c = close[i]
        sum_long += c
        sum_short += c
        if i >= MA_LONG:
            sum_long -= close[i - MA_LONG]
        if i >= MA_SHORT:
            sum_short -= close[i - MA_SHORT]
        d = dates[i]
        cnt = by_date.get(d)
        if cnt is None:
            cnt = by_date[d] = [0] * 11
        n_rows = i + 1
        cnt[0] += 1
        if n_rows >= MA_LONG:
            cnt[1] += 1
            if c > sum_long / MA_LONG:
                cnt[2] += 1
        if n_rows >= MA_SHORT:
            cnt[3] += 1
            if c > sum_short / MA_SHORT:
                cnt[4] += 1
        if n_rows >= 2:
            p = pct[i]
            if p > 0:
                cnt[5] += 1
            elif p < 0:
                cnt[6] += 1
            else:
                cnt[7] += 1
            if n_rows >= MIN_ROWS_FOR_LIMIT:
                cnt[10] += 1
                if is_688 or (is_30 and d >= cyb_from):
                    thr = LIMIT_20
                elif flags[i] & 1:
                    thr = LIMIT_ST
                else:
                    thr = LIMIT_MAIN
                if p >= thr:
                    cnt[8] += 1
                elif p <= -thr:
                    cnt[9] += 1


def rebuild_agg(store: MarketStore, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, list]:
    """从全部 ``stocks/*.bin`` 重算 ``agg.json``（不联网、不写清单）；``kind != "full"`` 抛 PanelError。"""
    manifest = manifest if manifest is not None else store.load_manifest()
    if not manifest:
        raise PanelError("重算聚合：清单不存在")
    if manifest.get("kind") != "full":
        raise PanelError(f"重算聚合只支持 kind=full 的库（当前 {manifest.get('kind')!r}：tail 种子库只有最近 {TAIL_ROWS} 行）")
    t0 = _monotonic()
    by_date: Dict[int, List[int]] = {}
    n_stocks = n_rows = 0
    for path in sorted(store.stocks_dir.glob("*.bin")):
        s = unpack_series(path.read_bytes())
        if not len(s.dates):
            continue
        n_stocks += 1
        n_rows += len(s.dates)
        _accumulate_series(by_date, path.stem[-6:], s.dates, s.close, s.pct, s.flags)
    agg = empty_agg()
    for d in sorted(by_date):
        cnt = by_date[d]
        agg["dates"].append(date_str(d))
        for j, k in enumerate(AGG_KEYS):
            agg[k].append(cnt[j])
    store.save_agg(agg)
    log.info("重算聚合：%s 只 %s 行 %s 个交易日，%.1f s", format(n_stocks, ","), format(n_rows, ","),
             format(len(agg["dates"]), ","), _monotonic() - t0)
    return agg


# --------------------------------------------------------------------------- 日增量
def apply_day(store: MarketStore, d: str, rows: Sequence[Dict[str, Any]], manifest: Dict[str, Any],
              agg: Dict[str, list]) -> Dict[str, int]:
    """把一天的横截面写进库：续链/起链、追加行、算当日计数、``agg_append``、``manifest.lastDate = d``。不落盘清单/聚合。"""
    codes = manifest["codes"]
    di = date_int(d)
    counters: Dict[str, int] = {k: 0 for k in AGG_KEYS}
    for r in rows:
        key = r["key"]
        e = codes.get(key)
        if e is None or not e.get("n"):
            if e is None:
                e = codes[key] = {"name": "", "ipo": d, "out": "", "status": "1", "first": None, "last": None,
                                  "n": 0, "lastClose": None}
            else:
                e.setdefault("name", "")
                e["status"] = e.get("status") or "1"
                e["ipo"] = e.get("ipo") or d
            hfq = float(r["close"])
        else:
            if e.get("last") and e["last"] >= d:
                raise PanelError(f"{key} 已有 {e['last']} 的行，不能再写 {d}")
            hfq = float(e["lastClose"]) * (1.0 + float(r["pct"]) / 100.0)
        series = store.append_rows(key, [(di, hfq, r["pct"], r["is_st"])])
        n_rows = len(series.dates)
        e["first"] = e.get("first") or d
        e["last"] = d
        e["n"] = n_rows
        e["lastClose"] = hfq
        tail = series.close[-MA_LONG:]
        fl = stock_day_flags(tail, float(series.pct[-1]), bool(series.flags[-1] & 1), n_rows, key[-6:], d)
        add_flags(counters, fl)
    agg_append(agg, d, counters)
    manifest["lastDate"] = d
    manifest["nStocks"] = _count_stocks(manifest)
    manifest["updatedAt"] = iso_now()
    return counters


def ensure_store(store: Optional[MarketStore] = None) -> Dict[str, Any]:
    """返回清单；库不存在时从种子初始化，没有种子则抛 PanelError。"""
    store = store if store is not None else MarketStore()
    manifest = store.load_manifest()
    if manifest is None:
        if SEED_PATH.is_file():
            import_seed(SEED_PATH, store)
            manifest = store.load_manifest()
        if manifest is None:
            raise PanelError("全市场日线库不存在：本地先运行 make market-cold，或提交 state/market_seed.zip")
    return manifest


def budget_from_env() -> float:
    return float(env_int(ENV_BUDGET, DEFAULT_BUDGET_MIN))


_UPDATE_RESULT: Optional[Tuple[Optional[Dict[str, Any]], Optional[PanelError]]] = None


def update_incremental(budget_min: float = DEFAULT_BUDGET_MIN) -> Dict[str, Any]:
    """用全市场横截面把库追到最新（每进程只做一次：第二次调用返回同一结果或重新抛出同一 PanelError）。

    → ``{updated: [dates], lagging, lastDate, today, nQueries, seconds}``。
    """
    global _UPDATE_RESULT
    if _UPDATE_RESULT is not None:
        res, err = _UPDATE_RESULT
        if err is not None:
            raise err
        return res  # type: ignore[return-value]
    try:
        res = _update_incremental(budget_min)
    except PanelError as e:
        _UPDATE_RESULT = (None, e)
        raise
    _UPDATE_RESULT = (res, None)
    return res


def _update_incremental(budget_min: float) -> Dict[str, Any]:
    store = MarketStore()
    manifest = ensure_store(store)
    progress = store.load_progress()
    if progress is not None and not manifest.get("coldDone"):
        raise PanelError(f"全市场日线库建设中 {progress.get('done')}/{progress.get('total')}，"
                         f"待抓 {len(progress.get('pending') or [])} 只；完成后再构建")
    if not manifest.get("coldDone"):
        raise PanelError("全市场日线库未完成冷启动（coldDone=false）")
    last_date = manifest.get("lastDate") or ""
    today = today_str()
    closed = market_closed()
    t0 = _monotonic()
    deadline = t0 + float(budget_min) * 60.0

    def missing_days(cal: Sequence[str]) -> List[str]:
        return [d for d in cal if last_date < d <= today and (d < today or closed)]

    cal_obj = store.load_calendar() or {}
    cal: List[str] = list(cal_obj.get("dates") or [])
    missing = missing_days(cal)
    need_cal = not cal or cal[-1] < shift_days(today, CALENDAR_MIN_AHEAD_DAYS)
    result: Dict[str, Any] = {"updated": [], "lagging": False, "lastDate": last_date, "today": today,
                              "nQueries": 0, "seconds": 0.0}
    if not missing and not need_cal:
        log.info("全市场日线库已是最新（截至 %s），不连接 Baostock", last_date)
        result["seconds"] = round(_monotonic() - t0, 1)
        return result
    agg = store.load_agg()
    try:
        with Session() as sess:
            cal = ensure_calendar(store, sess)
            missing = missing_days(cal)
            log.info("全市场日线库截至 %s，缺 %d 个交易日%s", last_date, len(missing),
                     f"（{missing[0]} → {missing[-1]}）" if missing else "")
            for d in missing:
                if _monotonic() >= deadline:
                    result["lagging"] = True
                    remaining = len(missing) - len(result["updated"])
                    log.warning("预算 %.0f 分钟用尽，还差 %d 个交易日，下次运行继续补齐", budget_min, remaining)
                    break
                rows = fetch_cross_section(sess, d)
                if not rows:
                    if d == today:
                        log.info("Baostock 尚未发布 %s，本次到此为止", d)
                        break
                    raise PanelError(f"Baostock 没有 {d} 的全市场数据；下次运行重试，或 "
                                     f"python -m etl.market_state --skip-day {d}")
                prev_n = agg["nTraded"][-1] if agg["nTraded"] else 0
                if prev_n and len(rows) < MIN_ROW_SHARE_VS_PREV * prev_n:
                    raise PanelError(f"{d} 全市场横截面只有 {len(rows)} 行，不足前一交易日 {prev_n} 的 "
                                     f"{MIN_ROW_SHARE_VS_PREV:.0%}，疑似数据不全")
                counters = apply_day(store, d, rows, manifest, agg)
                store.save_agg(agg)
                store.save_manifest(manifest)
                result["updated"].append(d)
                pct250 = 100.0 * counters["above250"] / counters["n250"] if counters["n250"] else 0.0
                log.info("全市场 %s: 交易 %d 只，站上 MA250 %.1f%%，涨 %d 跌 %d，涨停 %d 跌停 %d", d,
                         counters["nTraded"], pct250, counters["adv"], counters["dec"], counters["limitUp"],
                         counters["limitDown"])
            result["nQueries"] = sess.n_queries
    except BaostockError as e:
        raise PanelError(str(e)) from e
    result["lastDate"] = manifest.get("lastDate")
    result["seconds"] = round(_monotonic() - t0, 1)
    return result


# --------------------------------------------------------------------------- 种子
def export_seed(path: Path = SEED_PATH, tail_rows: int = TAIL_ROWS, store: Optional[MarketStore] = None) -> Path:
    """把 full 库导出为 tail 种子 zip（清单 kind=tail + 聚合 + 日历 + 每只最后 ``tail_rows`` 行）；> 15 MB 抛 PanelError。"""
    store = store if store is not None else MarketStore()
    manifest = store.load_manifest()
    if not manifest or not manifest.get("coldDone"):
        raise PanelError("冷启动未完成（coldDone=false），不能导出种子")
    if store.load_progress() is not None:
        raise PanelError("progress.json 仍存在，冷启动尚未收尾")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".zip.tmp")
    seed_manifest = dict(manifest)
    seed_manifest.update({"kind": "tail", "tailRows": int(tail_rows), "exportedAt": iso_now(), "sourceKind": "full"})
    n_files = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_FILE, json.dumps(seed_manifest, ensure_ascii=False, separators=(",", ":")))
        zf.writestr(AGG_FILE, json.dumps(store.load_agg(), ensure_ascii=False, separators=(",", ":")))
        zf.writestr(CALENDAR_FILE, json.dumps(store.load_calendar() or {}, ensure_ascii=False, separators=(",", ":")))
        for p in sorted(store.stocks_dir.glob("*.bin")):
            s = unpack_series(p.read_bytes())
            if len(s.dates) > tail_rows:
                s = StockSeries(s.dates[-tail_rows:], s.close[-tail_rows:], s.pct[-tail_rows:], s.flags[-tail_rows:])
            zf.writestr(f"stocks/{p.name}", pack_series(s.dates, s.close, s.pct, s.flags))
            n_files += 1
    size = tmp.stat().st_size
    if size > MAX_SEED_BYTES:
        tmp.unlink()
        raise PanelError(f"种子 {size / 1048576:.1f} MB 超过 {MAX_SEED_BYTES // 1048576} MB 上限")
    os.replace(tmp, path)
    log.info("导出种子 %s：%d 只（各 ≤ %d 行），%.1f MB，截至 %s", path, n_files, tail_rows, size / 1048576,
             manifest.get("lastDate"))
    return path


def import_seed(path: Path = SEED_PATH, store: Optional[MarketStore] = None) -> Dict[str, Any]:
    """把种子 zip 解到空的 ``cache/market/``（已有清单则拒绝）；校验 kind=tail、coldDone、聚合日期递增、股票文件数。"""
    store = store if store is not None else MarketStore()
    if store.load_manifest() is not None:
        raise PanelError(f"{store.root} 已有清单，拒绝覆盖；先 make clean-cache-market")
    path = Path(path)
    if not path.is_file():
        raise PanelError(f"种子文件不存在: {path}")
    store.ensure_dirs()
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for required in (MANIFEST_FILE, AGG_FILE, CALENDAR_FILE):
            if required not in names:
                raise PanelError(f"种子缺少 {required}")
        manifest = json.loads(zf.read(MANIFEST_FILE).decode("utf-8"))
        if manifest.get("kind") != "tail" or not manifest.get("coldDone"):
            raise PanelError(f"种子清单异常: kind={manifest.get('kind')!r} coldDone={manifest.get('coldDone')!r}")
        agg = json.loads(zf.read(AGG_FILE).decode("utf-8"))
        compute.assert_ascending(agg.get("dates") or [], "种子聚合日期")
        calendar = json.loads(zf.read(CALENDAR_FILE).decode("utf-8"))
        stock_names = sorted(n for n in names if n.startswith("stocks/") and n.endswith(".bin"))
        if len(stock_names) < MIN_SEED_STOCKS:
            raise PanelError(f"种子只有 {len(stock_names)} 个股票文件（应 ≥ {MIN_SEED_STOCKS}）")
        for n in stock_names:
            key = n[len("stocks/"):-len(".bin")]
            if not baostock_src.KEY_RE.match(key):
                raise PanelError(f"种子里有非法文件名: {n}")
            store.stock_path(key).write_bytes(zf.read(n))
        store.save_calendar(calendar)
        store.save_agg(agg)
        store.save_manifest(manifest)
    log.info("从种子初始化全市场日线库：%d 只，截至 %s（%s）", len(stock_names), manifest.get("lastDate"), path)
    return manifest


def skip_day(d: str, store: Optional[MarketStore] = None) -> Dict[str, Any]:
    """把交易日 d 记入 ``skippedDays`` 并在需要时推进 ``lastDate``（该日收益从所有股票的链里缺失，已记录的漂移）。"""
    store = store if store is not None else MarketStore()
    manifest = store.load_manifest()
    if not manifest:
        raise PanelError("全市场日线库不存在，无法跳过日期")
    date_int(d)
    skipped = manifest.setdefault("skippedDays", [])
    if d not in skipped:
        skipped.append(d)
        skipped.sort()
    if not manifest.get("lastDate") or d > manifest["lastDate"]:
        manifest["lastDate"] = d
    manifest["updatedAt"] = iso_now()
    store.save_manifest(manifest)
    log.warning("已跳过 %s：该日收益不进入任何股票的后复权链（skippedDays=%s）", d, skipped)
    return manifest


# --------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="全市场日线库 cache/market 维护：冷启动 / 日增量 / 重算聚合 / 种子 / 状态")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cold", action="store_true", help="逐只抓全史建库（可中断续跑）")
    g.add_argument("--update", action="store_true", help="用全市场横截面追平缺失交易日")
    g.add_argument("--rebuild", action="store_true", help="只从股票文件重算 agg.json（不联网）")
    g.add_argument("--export-seed", action="store_true", help="导出 tail 种子 zip")
    g.add_argument("--import-seed", action="store_true", help="从种子 zip 初始化空库")
    g.add_argument("--status", action="store_true", help="打印库状态")
    g.add_argument("--skip-day", metavar="YYYY-MM-DD", help="跳过某个 Baostock 没有数据的交易日")
    ap.add_argument("--budget-min", type=float, default=None, help=f"时间预算（分钟）；--update 默认取 {ENV_BUDGET} 或 {DEFAULT_BUDGET_MIN}")
    ap.add_argument("--limit", type=int, default=None, help="--cold 本次最多抓多少只")
    ap.add_argument("--out", type=Path, default=SEED_PATH, help="--export-seed 输出路径")
    ap.add_argument("--seed", type=Path, default=SEED_PATH, help="--import-seed 种子路径")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    get_logger("etl.market", args.verbose)
    store = MarketStore()
    try:
        if args.cold:
            cold_start(budget_min=args.budget_min, limit=args.limit, verbose=args.verbose)
        elif args.update:
            res = update_incremental(args.budget_min if args.budget_min is not None else budget_from_env())
            log.info("日增量结果: %s", json.dumps(res, ensure_ascii=False))
        elif args.rebuild:
            rebuild_agg(store)
        elif args.export_seed:
            export_seed(args.out)
        elif args.import_seed:
            import_seed(args.seed)
        elif args.skip_day:
            skip_day(args.skip_day)
        print(json.dumps(store.status(), ensure_ascii=True, indent=1))
        return 0
    except PanelError as e:
        log.error("%s", e)
        print(json.dumps({"error": str(e), **store.status()}, ensure_ascii=True, indent=1))
        return 1


if __name__ == "__main__":
    sys.exit(main())
