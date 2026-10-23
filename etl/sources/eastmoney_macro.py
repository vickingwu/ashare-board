# -*- coding: utf-8 -*-
"""数据源：东方财富数据中心 宏观经济库（``datacenter-web.eastmoney.com/api/data/v1/get``）— M2、GDP 累计值、70 城二手住宅环比。

**仅作为国家统计局接口不可达时的最后手段**（2026-10-23 探测：data.stats.gov.cn 的 easyquery 对本机与云端 IP
一律 HTTP 403「UrlACL」，连 HTML 入口页也被拦，GitHub runner 同属云端 IP 段）。本模块把三个报表隔离在一处，
面板说明与 README 里都写明「统计局数据经东方财富数据中心转载」。只用 ``datacenter`` 的 ``api/data/v1/get``；
不碰行情类 ``push2his`` 接口。

接口（m4_work/probe_sources.txt §A2）：
``GET …/get?reportName=<R>&columns=ALL&pageNumber=N&pageSize=500&sortColumns=REPORT_DATE&sortTypes=1&source=WEB
&client=WEB&_=<毫秒>``（可选 ``filter=(REPORT_DATE>='YYYY-MM-01')``），需带 ``Referer: https://data.eastmoney.com/cjsj/``；
响应 ``text/plain`` 的 JSON ``{"result":{"pages","data":[…],"count"},"success":true,"code":0,"message":"ok"}``，
``result`` 为 null 表示没有数据。

字段口径：
* ``RPT_ECONOMY_CURRENCY_SUPPLY`` 货币供应量：``REPORT_DATE`` = 数据月份的 1 日 → 月键 ``YYYY-MM``；``BASIC_CURRENCY`` M2
  期末值（亿元）、``CURRENCY`` M1、``FREE_CASH`` M0；``*_SAME`` 同比 %、``*_SEQUENTIAL`` 环比 %（不用）。2008-01 起，1 页；
* ``RPT_ECONOMY_GDP`` 国内生产总值：``DOMESTICL_PRODUCT_BASE`` 是 **累计值**（亿元，``TIME`` 如「2026年第1-3季度」），
  ``REPORT_DATE`` 落在 03/06/09/12 月 → 季末日期；当季值 = 本季累计 − 上季累计（一季度 = 累计值）、四季度滚动求和
  都在面板里用 ``compute.cumulative_to_period`` / ``rolling_sum`` 完成；2006Q1 起，1 页；
* ``RPT_ECONOMY_HOUSE_PRICE`` 70 个大中城市住宅销售价格：``CITY`` 中文城市名，``SECOND_HOUSE_SEQUENTIAL`` 二手住宅
  环比（上月 = 100）；``SECOND_HOUSE_BASE``（定基）2026 年为 null，所以链式定基（2010-12 = 100）由面板用
  ``compute.chain_link`` 自己算。2011-01 起 70 城 × 月，pageSize 500 时冷抓 27 页，增量用 ``filter`` 只取最后缓存月起的 1 页。

发布与缓存：统计局每月 10–20 日陆续发布上月数据（M2 约 10–15 日、70 城约 15–18 日、GDP 约 1/4/7/10 月 16–20 日），
所以 ``RELEASE_DAY = 20``：按 ``common.monthly_refresh_due`` 每个自然月在 20 日当天或之后首次运行时刷新一次，
其余运行只读 ``cache/eastmoney/<name>.json``（``{fetchedAt, fetchMonth, …}``）。20 日前的冷抓记上月为 ``fetchMonth``，
到 20 日会再抓一次（该月至多 2 次）。刷新失败而缓存存在时记 WARNING 沿用缓存。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_load, cache_save, http_get, iso_now, monthly_fetch_month,
                        monthly_refresh_due)

log = logging.getLogger("etl.eastmoney_macro")

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"Referer": "https://data.eastmoney.com/cjsj/", "Accept": "application/json, text/javascript, */*; q=0.01"}
REPORT_M2 = "RPT_ECONOMY_CURRENCY_SUPPLY"
REPORT_GDP = "RPT_ECONOMY_GDP"
REPORT_HOUSE = "RPT_ECONOMY_HOUSE_PRICE"
PAGE_SIZE = 500
MAX_PAGES = 40
RELEASE_DAY = 20
CACHE_SUB = "eastmoney"
TIMEOUT = 60
TIER1 = ("北京", "上海", "广州", "深圳")
N_CITIES = 70
MIN_CITIES = 68
MIN_M2_ROWS = 200
MIN_GDP_ROWS = 80
QUARTER_END_MONTHS = ("03", "06", "09", "12")


# --------------------------------------------------------------------------- 解析（纯函数）
def parse_response(body: Any) -> Tuple[int, int, List[Dict[str, Any]]]:
    """接口响应 → (count, pages, rows)。``success`` 非真或 ``code != 0`` → PanelError；``result`` 为 null → (0, 0, [])。"""
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"东方财富数据中心返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    if not isinstance(j, dict) or not j.get("success") or j.get("code") not in (0, "0"):
        raise PanelError(f"东方财富数据中心返回异常: {str(j)[:200]}")
    result = j.get("result")
    if result is None:
        return 0, 0, []
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise PanelError(f"东方财富数据中心 result 形状异常: {str(result)[:200]}")
    try:
        count = int(result.get("count", len(result["data"])))
        pages = int(result.get("pages", 1))
    except (TypeError, ValueError):
        raise PanelError(f"东方财富数据中心 count/pages 异常: {str(result)[:200]}")
    return count, pages, [r for r in result["data"] if isinstance(r, dict)]


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month(row: Dict[str, Any]) -> str:
    d = str(row.get("REPORT_DATE") or "")
    if len(d) < 7 or d[4] != "-":
        raise PanelError(f"东方财富 REPORT_DATE 异常: {d!r}")
    return d[:7]


def rows_to_m2(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """货币供应量行 → ``{months[], m2[], m1[], m0[]}``（升序；同月重复后者覆盖；首末月之间缺月 → PanelError）。"""
    by_month: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
    for r in rows:
        by_month[_month(r)] = (_num(r.get("BASIC_CURRENCY")), _num(r.get("CURRENCY")), _num(r.get("FREE_CASH")))
    months = sorted(by_month)
    if months:
        expected = compute.months_between(months[0], months[-1])
        missing = [m for m in expected if m not in by_month]
        if missing:
            raise PanelError(f"东方财富 M2 缺少月份 {missing[:5]}（共 {len(missing)} 个）")
    return {
        "months": months,
        "m2": [by_month[m][0] for m in months],
        "m1": [by_month[m][1] for m in months],
        "m0": [by_month[m][2] for m in months],
    }


def rows_to_gdp(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """GDP 行 → ``{quarterEnds[], cum[]}``：``REPORT_DATE`` 月份必须是 03/06/09/12 → 自然季末日期；累计值 亿元；升序。"""
    by_month: Dict[str, Optional[float]] = {}
    for r in rows:
        m = _month(r)
        if m[5:7] not in QUARTER_END_MONTHS:
            raise PanelError(f"东方财富 GDP 的 REPORT_DATE 不在季末月: {m}")
        by_month[m] = _num(r.get("DOMESTICL_PRODUCT_BASE"))
    months = sorted(by_month)
    return {"quarterEnds": [compute.month_end_date(m) for m in months], "cum": [by_month[m] for m in months]}


def rows_to_house(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """70 城行 → ``{months[], cities[], seq{city: values[]}}``（二手住宅环比，上月 = 100）。

    城市按名称排序、月份升序、缺格为 None；任一月份非 None 城市数 < ``MIN_CITIES`` 或缺任一一线城市 → PanelError。
    """
    cells: Dict[Tuple[str, str], Optional[float]] = {}
    cities_set = set()
    months_set = set()
    for r in rows:
        city = str(r.get("CITY") or "").strip()
        if not city:
            continue
        m = _month(r)
        cities_set.add(city)
        months_set.add(m)
        cells[(m, city)] = _num(r.get("SECOND_HOUSE_SEQUENTIAL"))
    months = sorted(months_set)
    cities = sorted(cities_set)
    missing_tier1 = [c for c in TIER1 if c not in cities_set]
    if months and missing_tier1:
        raise PanelError(f"东方财富 70 城缺少一线城市 {missing_tier1}")
    seq: Dict[str, List[Optional[float]]] = {c: [cells.get((m, c)) for m in months] for c in cities}
    for i, m in enumerate(months):
        present = sum(1 for c in cities if seq[c][i] is not None)
        if present < MIN_CITIES:
            raise PanelError(f"东方财富 70 城 {m} 只有 {present} 个城市有环比（应 ≥ {MIN_CITIES}）")
        for c in TIER1:
            if seq[c][i] is None:
                raise PanelError(f"东方财富 70 城 {m} 缺 {c} 的环比")
    return {"months": months, "cities": cities, "seq": seq}


# --------------------------------------------------------------------------- I/O
def _params(report: str, page: int, flt: Optional[str]) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "reportName": report,
        "columns": "ALL",
        "pageNumber": page,
        "pageSize": PAGE_SIZE,
        "sortColumns": "REPORT_DATE",
        "sortTypes": 1,
        "source": "WEB",
        "client": "WEB",
        "_": int(time.time() * 1000),
    }
    if flt:
        p["filter"] = flt
    return p


def _fetch_report(report: str, flt: Optional[str] = None) -> List[Dict[str, Any]]:
    """按页抓整个报表（或 ``flt`` 过滤后的部分）：从第 1 页起，直到行数 ≥ count 或超过 ``MAX_PAGES``。"""
    rows: List[Dict[str, Any]] = []
    page = 1
    count = None
    while True:
        url = URL + "?" + urlencode(_params(report, page, flt))
        body, _hdr = http_get(url, timeout=TIMEOUT, headers=HEADERS)
        c, pages, got = parse_response(body)
        count = c if count is None else count
        rows.extend(got)
        log.debug("东方财富 %s 第 %d/%s 页: %d 字节，%d 行，count %d", report, page, pages, len(body), len(got), c)
        if not got or len(rows) >= count or page >= pages:
            break
        page += 1
        if page > MAX_PAGES:
            log.warning("东方财富 %s: 超过 MAX_PAGES=%d 仍未取完（count=%d，已取 %d 行）", report, MAX_PAGES, count, len(rows))
            break
    return rows


def _monthly_cached(name: str, fetch_fn: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
                    merge_fn: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
                    validate_fn: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """月度缓存规则（common.monthly_refresh_due，release_day = RELEASE_DAY）。

    到期 → ``fetch_fn(cached)`` 抓新数据 → 有缓存时 ``merge_fn(cached, fresh)`` 合并（同键新值覆盖） → ``validate_fn``
    → 写 ``cache/eastmoney/<name>.json``（带 ``fetchedAt`` / ``fetchMonth``）。抓取失败且有缓存 → WARNING 沿用缓存。
    """
    key = f"{name}.json"
    cached = cache_load(CACHE_SUB, key)
    if cached and not monthly_refresh_due(cached.get("fetchMonth"), release_day=RELEASE_DAY):
        log.info("东方财富 %s: 本月（%s）已刷新，缓存命中", name, cached.get("fetchMonth"))
        return cached
    try:
        fresh = fetch_fn(cached)
        merged = merge_fn(cached, fresh) if cached else fresh
        if validate_fn is not None:
            validate_fn(merged)
    except PanelError as e:
        if not cached:
            raise
        log.warning("东方财富 %s: 刷新失败（%s），沿用缓存 fetchMonth=%s", name, e, cached.get("fetchMonth"))
        return cached
    merged["fetchedAt"] = iso_now()
    merged["fetchMonth"] = monthly_fetch_month(release_day=RELEASE_DAY)
    cache_save(CACHE_SUB, key, merged)
    log.info("东方财富 %s: 已刷新，fetchMonth=%s", name, merged["fetchMonth"])
    return merged


def _merge_keyed(cached: Dict[str, Any], fresh: Dict[str, Any], key_field: str, value_fields: Tuple[str, ...]
                 ) -> Dict[str, Any]:
    """按键（月份 / 季末日期）合并平行数组，新值覆盖旧值。"""
    out: Dict[str, Any] = {}
    keys = None
    for f in value_fields:
        keys, vals = compute.merge_series(cached.get(key_field) or [], cached.get(f) or [],
                                          fresh.get(key_field) or [], fresh.get(f) or [])
        out[f] = vals
    out[key_field] = keys or []
    return out


def _validate_m2(obj: Dict[str, Any]) -> None:
    if len(obj["months"]) < MIN_M2_ROWS:
        raise PanelError(f"东方财富 M2 只有 {len(obj['months'])} 个月（应 ≥ {MIN_M2_ROWS}）")
    bad = [m for m, v in zip(obj["months"], obj["m2"]) if v is None or v <= 0]
    if bad:
        raise PanelError(f"东方财富 M2 有 {len(bad)} 个非正值，首个 {bad[0]}")
    missing = [m for m in compute.months_between(obj["months"][0], obj["months"][-1]) if m not in set(obj["months"])]
    if missing:
        raise PanelError(f"东方财富 M2 合并后缺月 {missing[:5]}")


def _validate_gdp(obj: Dict[str, Any]) -> None:
    if len(obj["quarterEnds"]) < MIN_GDP_ROWS:
        raise PanelError(f"东方财富 GDP 只有 {len(obj['quarterEnds'])} 个季度（应 ≥ {MIN_GDP_ROWS}）")
    compute.assert_ascending(obj["quarterEnds"], "东方财富 GDP 季末日期")
    bad = [d for d, v in zip(obj["quarterEnds"], obj["cum"]) if v is None or v <= 0]
    if bad:
        raise PanelError(f"东方财富 GDP 有 {len(bad)} 个非正累计值，首个 {bad[0]}")


def _validate_house(obj: Dict[str, Any]) -> None:
    if not obj["months"]:
        raise PanelError("东方财富 70 城没有任何月份")
    missing = [m for m in compute.months_between(obj["months"][0], obj["months"][-1]) if m not in set(obj["months"])]
    if missing:
        raise PanelError(f"东方财富 70 城缺月 {missing[:5]}")
    if len(obj["cities"]) < MIN_CITIES:
        raise PanelError(f"东方财富 70 城只有 {len(obj['cities'])} 个城市")


def get_m2() -> Dict[str, Any]:
    """M2 / M1 / M0 月度期末值 → ``{fetchedAt, fetchMonth, months[], m2[], m1[], m0[]}``（亿元）。整表 1 页，每月刷新一次。"""
    return _monthly_cached(
        "m2",
        lambda cached: rows_to_m2(_fetch_report(REPORT_M2)),
        lambda cached, fresh: _merge_keyed(cached, fresh, "months", ("m2", "m1", "m0")),
        _validate_m2,
    )


def get_gdp() -> Dict[str, Any]:
    """GDP 累计值 → ``{fetchedAt, fetchMonth, quarterEnds[], cum[]}``（亿元）。整表 1 页，每月刷新一次。"""
    return _monthly_cached(
        "gdp",
        lambda cached: rows_to_gdp(_fetch_report(REPORT_GDP)),
        lambda cached, fresh: _merge_keyed(cached, fresh, "quarterEnds", ("cum",)),
        _validate_gdp,
    )


def _fetch_house(cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    flt = None
    if cached and cached.get("months"):
        flt = f"(REPORT_DATE>='{cached['months'][-1]}-01')"
    return rows_to_house(_fetch_report(REPORT_HOUSE, flt))


def _merge_house(cached: Dict[str, Any], fresh: Dict[str, Any]) -> Dict[str, Any]:
    cities = sorted(set(cached.get("cities") or []) | set(fresh.get("cities") or []))
    months = None
    seq: Dict[str, List[Optional[float]]] = {}
    for c in cities:
        old = (cached.get("seq") or {}).get(c) or [None] * len(cached.get("months") or [])
        new = (fresh.get("seq") or {}).get(c) or [None] * len(fresh.get("months") or [])
        months, vals = compute.merge_series(cached.get("months") or [], old, fresh.get("months") or [], new)
        seq[c] = vals
    return {"months": months or [], "cities": cities, "seq": seq}


def get_house_prices() -> Dict[str, Any]:
    """70 城二手住宅环比 → ``{fetchedAt, fetchMonth, months[], cities[], seq{city: values[]}}``。

    冷抓整表（27 页）；有缓存时只取 ``filter=(REPORT_DATE>='<最后缓存月>-01')`` 的 1 页并按月合并。
    """
    return _monthly_cached("house", _fetch_house, _merge_house, _validate_house)
