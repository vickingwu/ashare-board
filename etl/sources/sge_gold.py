# -*- coding: utf-8 -*-
"""数据源：上海黄金交易所 Au99.99 日行情（``POST https://www.sge.com.cn/graph/Dailyhq``）— 人民币金价（元/克）的近十年日收盘。

接口（2026-10-23 探测，m4_work/probe_sources.txt §B1）：表单 ``instid=Au99.99``，需带 ``Referer https://www.sge.com.cn/sjzx/mrhq``、
``X-Requested-With: XMLHttpRequest``、``Origin https://www.sge.com.cn``；返回 ``{"time": [[date, open, close, low, high], …]}``
约 2,386 行、97 KB，日期升序、无周末行，只有节假日空缺。**窗口是滚动约 10 年**（2016-12-19 起），更早的历史不在这里，
由 ``gold_history`` 的种子（世界银行月均 × 中间价）衔接。

列顺序是推断的（ECharts K 线约定 开/收/低/高）：2,369/2,386 行的开盘与收盘都落在 [低, 高] 之内，其余 17 行偏出 ≤ 0.6 元
——交易所公布的收盘价可以略微落在当日区间之外，所以解析器**不**断言 close ∈ [low, high]，只要求 ``close > 0`` 且
相邻两行（相隔 ≤ ``MAX_GAP_DAYS`` = 14 个自然日时）日间变动 |Δ| ≤ ``MAX_DAY_MOVE``（15 %），再大就视为列顺序或数据出错抛
``PanelError``；相隔更久的两行（缓存里的历史断档、裁剪过的测试样本）不比较。

缓存与刷新：``cache/sge/au9999.json`` = ``{fetchedAt, fetchMonth, dates[], close[]}``。按 ``common.monthly_refresh_due``
以 ``RELEASE_DAY = 1`` 刷新——每个自然月**首次运行**抓一次，拿到上月完整的日收盘；接口的十年窗口会丢掉最早的行，
缓存按日期合并保留它们（新值覆盖同日旧值）。16:00 前丢掉当天的行。面板用 ``monthly_averages`` 取月均价，
``complete_only`` 时排除当前自然月。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import (PanelError, cache_load, cache_save, http_post, iso_now, market_closed, monthly_fetch_month,
                        monthly_refresh_due, today_str)

log = logging.getLogger("etl.sge_gold")

URL = "https://www.sge.com.cn/graph/Dailyhq"
INSTID = "Au99.99"
HEADERS = {
    "Referer": "https://www.sge.com.cn/sjzx/mrhq",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.sge.com.cn",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
CACHE_SUB = "sge"
CACHE_KEY = "au9999.json"
MIN_ROWS_FULL = 2000
MAX_DAY_MOVE = 0.15
# 日变动护栏只比较相隔 ≤ 14 个自然日的相邻行（春节等假期空缺最长约 10 天）；更长的空缺视为历史断档，不比较
MAX_GAP_DAYS = 14
RELEASE_DAY = 1
TIMEOUT = 60


# --------------------------------------------------------------------------- 解析（纯函数）
def parse_dailyhq(body: Any) -> List[Tuple[str, float]]:
    """接口响应 → ``[(YYYY-MM-DD, close), …]`` 升序。

    行是 5 元素列表 ``[date, open, close, low, high]``（推断的顺序，见模块说明），取 ``row[2]``；周末日期与
    ``close <= 0`` 的行丢弃；日期重复或非升序、相邻两日 |close/prev − 1| > ``MAX_DAY_MOVE`` → PanelError。
    """
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"上海黄金交易所返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    if not isinstance(j, dict) or not isinstance(j.get("time"), list):
        raise PanelError(f"上海黄金交易所返回缺少 time 数组: {str(j)[:200]}")
    rows: List[Tuple[str, float]] = []
    for r in j["time"]:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        d = str(r[0])[:10]
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            continue
        try:
            close = float(r[2])
        except (TypeError, ValueError):
            continue
        if close <= 0 or compute.is_weekend(d):
            continue
        rows.append((d, close))
    compute.assert_ascending([r[0] for r in rows], "上海黄金交易所日期")
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1][1], rows[i][1]
        gap = (compute.parse_date(rows[i][0]) - compute.parse_date(rows[i - 1][0])).days
        if gap <= MAX_GAP_DAYS and abs(cur / prev - 1.0) > MAX_DAY_MOVE:
            raise PanelError(f"上海黄金交易所 {rows[i][0]} 收盘 {cur} 相对前一日 {prev} 变动超过 {MAX_DAY_MOVE:.0%}，疑似列顺序或数据错误")
    return rows


def monthly_averages(hist: Dict[str, Any], complete_only: bool = True, today: Optional[str] = None
                     ) -> Tuple[List[str], List[float], List[int]]:
    """日收盘 → 月均价 ``(months[], avg[], nDays[])``；``complete_only`` 时排除当前自然月（``today`` 默认今天）。"""
    months, means, counts = compute.month_mean(hist.get("dates") or [], hist.get("close") or [])
    if complete_only and months:
        current = (today or today_str())[:7]
        if months[-1] >= current:
            keep = [i for i, m in enumerate(months) if m < current]
            months = [months[i] for i in keep]
            means = [means[i] for i in keep]
            counts = [counts[i] for i in keep]
    return months, means, counts


# --------------------------------------------------------------------------- I/O
def fetch_dailyhq() -> List[Tuple[str, float]]:
    body, _hdr = http_post(URL, data={"instid": INSTID}, timeout=TIMEOUT, headers=HEADERS)
    rows = parse_dailyhq(body)
    log.debug("上海黄金交易所 %s: %d 字节，%d 行", INSTID, len(body), len(rows))
    return rows


def get_au9999(use_cache: bool = True) -> Dict[str, Any]:
    """Au99.99 日收盘全历史（缓存累积）→ ``{fetchedAt, fetchMonth, dates[], close[]}``（元/克，升序）。

    按月刷新（``RELEASE_DAY = 1``）：缓存的 ``fetchMonth`` 已是当月则直接返回；否则 POST 一次并按日期合并（新值覆盖），
    冷抓行数 < ``MIN_ROWS_FULL`` → PanelError；16:00 前丢掉当天的行。
    """
    cached = cache_load(CACHE_SUB, CACHE_KEY) if use_cache else None
    if cached and cached.get("dates") and not monthly_refresh_due(cached.get("fetchMonth"), release_day=RELEASE_DAY):
        log.info("上海黄金交易所 Au99.99: 本月（%s）已刷新，缓存命中 %d 行，截至 %s", cached.get("fetchMonth"),
                 len(cached["dates"]), cached["dates"][-1])
        return cached
    if not (cached and cached.get("dates")):
        cached = None

    rows = fetch_dailyhq()
    if not cached and len(rows) < MIN_ROWS_FULL:
        raise PanelError(f"上海黄金交易所 Au99.99 全量只有 {len(rows)} 行（应 ≥ {MIN_ROWS_FULL}）")
    if not rows and cached:
        log.warning("上海黄金交易所 Au99.99: 返回空，沿用缓存")
    old_dates = cached["dates"] if cached else []
    old_close = cached["close"] if cached else []
    dates, close = compute.merge_series(old_dates, old_close, [r[0] for r in rows], [r[1] for r in rows])
    if dates and dates[-1] == today_str() and not market_closed():
        dates, close = dates[:-1], close[:-1]
    if not dates:
        raise PanelError("上海黄金交易所 Au99.99 没有任何行情")
    result = {
        "fetchedAt": iso_now(),
        "fetchMonth": monthly_fetch_month(release_day=RELEASE_DAY),
        "dates": dates,
        "close": close,
    }
    cache_save(CACHE_SUB, CACHE_KEY, result)
    log.info("上海黄金交易所 Au99.99: 新增 %d 行，共 %d 行，%s → %s，最新 %.2f 元/克", len(dates) - len(old_dates), len(dates),
             dates[0], dates[-1], close[-1])
    return result
