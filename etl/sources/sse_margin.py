# -*- coding: utf-8 -*-
"""数据源：上海证券交易所 融资融券交易汇总（``query.sse.com.cn`` queryMargin.do）— 沪市两融日度余额全历史。

接口（2026-10-21 探测，m3_work/probe_sources.txt §C）：
``GET https://query.sse.com.cn/marketdata/tradedata/queryMargin.do?isPagination=true&tabType=&stockCode=
&beginDate=YYYYMMDD&endDate=YYYYMMDD&pageHelp.pageSize=2000&pageHelp.pageNo=N&pageHelp.beginPage=N
&pageHelp.cacheSize=1&pageHelp.endPage=N&_=<毫秒时间戳>``，必须带 ``Referer: https://www.sse.com.cn/`` 与
``Accept: application/json, text/javascript, */*; q=0.01``（https 严格校验证书可用）。
返回 ``{pageHelp:{total, pageSize, pageCount, pageNo, …}, result:[{opDate "YYYYMMDD", rzye, rzmre, rzche,
rqyl, rqylje, rzrqjyzl, …}]}``，``result`` 按日期**倒序**；金额字段单位为 **元**（整数），本模块统一换成
**亿元**（保留 4 位小数）：``rzye`` 融资余额、``rqylje`` 融券余额、``rzrqjyzl`` 融资融券余额（→ ``total``）。

口径与坑：
* ``pageHelp.pageSize`` 服务端上限 2000（请求 5000 也只给 2000）；两融自 2010-03-31 起到今天共约 4,019 个
  交易日 = 3 页（每页约 950 KB、0.3–0.7 s），所以冷抓取 3 次请求、之后增量 1 次（``[最后缓存日 − 7 天, 今天]``）；
* 上交所 T+1 上午发布前一交易日数据，当天不会出现当天的行，无需「16:00 前丢弃今天」的处理；
* 深交所只有按日期的单日报表（无区间接口，补全史要 4,000 次请求），本模块**不覆盖深市**，
  两融子指标采用「沪市融资余额」口径，面板说明里写明；
* 主机限速 1 秒/次（common.HOST_MIN_INTERVAL）。

缓存 ``cache/sse/margin.json`` = ``{fetchedAt, dates[], rzye[], rqylje[], total[]}``（平行数组，升序），
同日复用遵循 ``cache_is_fresh``；增量区间按日期合并、新值覆盖旧值。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now, today_compact

log = logging.getLogger("etl.sse_margin")

MARGIN_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
HEADERS = {"Referer": "https://www.sse.com.cn/", "Accept": "application/json, text/javascript, */*; q=0.01"}
PAGE_SIZE = 2000
MAX_PAGES = 10
FIRST_DAY = "20100331"
INCREMENTAL_DAYS = 7
TIMEOUT = 60
CACHE_SUB = "sse"
CACHE_KEY = "margin.json"
MIN_ROWS_FULL = 3900
YI = 1e8

FIELDS = ("rzye", "rqylje", "total")
SOURCE_FIELD = {"rzye": "rzye", "rqylje": "rqylje", "total": "rzrqjyzl"}


# --------------------------------------------------------------------------- 解析（纯函数）
def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _yi(v: Any) -> Optional[float]:
    """元 → 亿元，4 位小数；None / 空 / 非数字 → None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return round(float(v) / YI, 4)
    except (TypeError, ValueError):
        return None


def parse_margin(body: Any) -> Tuple[int, int, List[Dict[str, Any]]]:
    """接口响应 → (total, pageCount, rows)，``row = {date "YYYY-MM-DD", rzye, rqylje, total}``（亿元），按日期升序。

    非 JSON、缺少 ``result`` 数组 → PanelError；日期不是 8 位数字的行跳过。
    """
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"上交所两融返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    if not isinstance(j, dict) or not isinstance(j.get("result"), list):
        raise PanelError(f"上交所两融返回缺少 result 数组: {str(j)[:200]}")
    page_help = j.get("pageHelp") if isinstance(j.get("pageHelp"), dict) else {}
    result = j["result"]
    try:
        total = int(page_help.get("total", len(result)))
        page_count = int(page_help.get("pageCount", 1))
    except (TypeError, ValueError):
        raise PanelError(f"上交所两融 pageHelp 异常: {str(page_help)[:200]}")
    by_date: Dict[str, Dict[str, Any]] = {}
    for r in result:
        if not isinstance(r, dict):
            continue
        op = str(r.get("opDate") or "").strip()
        if len(op) != 8 or not op.isdigit():
            continue
        row = {"date": _iso(op)}
        for f in FIELDS:
            row[f] = _yi(r.get(SOURCE_FIELD[f]))
        by_date[row["date"]] = row
    rows = [by_date[d] for d in sorted(by_date)]
    return total, page_count, rows


# --------------------------------------------------------------------------- I/O
def _params(begin: str, end: str, page_no: int) -> Dict[str, Any]:
    return {
        "isPagination": "true",
        "tabType": "",
        "stockCode": "",
        "beginDate": begin,
        "endDate": end,
        "pageHelp.pageSize": PAGE_SIZE,
        "pageHelp.pageNo": page_no,
        "pageHelp.beginPage": page_no,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": page_no,
        "_": int(time.time() * 1000),
    }


def _get_page(begin: str, end: str, page_no: int) -> Tuple[int, int, List[Dict[str, Any]]]:
    url = MARGIN_URL + "?" + urlencode(_params(begin, end, page_no))
    body, _hdr = http_get(url, timeout=TIMEOUT, headers=HEADERS)
    total, page_count, rows = parse_margin(body)
    log.debug("上交所两融 %s→%s 第 %d/%d 页: %d 字节，%d 行，total %d", begin, end, page_no, page_count,
              len(body), len(rows), total)
    return total, page_count, rows


def fetch_range(begin_yyyymmdd: str, end_yyyymmdd: str) -> List[Dict[str, Any]]:
    """[begin, end]（YYYYMMDD）内的全部行：第 1 页后按 pageCount 翻页（≤ MAX_PAGES），按日期去重、升序。"""
    by_date: Dict[str, Dict[str, Any]] = {}
    total, page_count, rows = _get_page(begin_yyyymmdd, end_yyyymmdd, 1)
    for r in rows:
        by_date[r["date"]] = r
    last_page = min(page_count, MAX_PAGES)
    for page in range(2, last_page + 1):
        _t, _pc, rows = _get_page(begin_yyyymmdd, end_yyyymmdd, page)
        for r in rows:
            by_date[r["date"]] = r
    if page_count > MAX_PAGES:
        log.warning("上交所两融 %s→%s: pageCount %d 超过 MAX_PAGES %d，只取前 %d 页", begin_yyyymmdd, end_yyyymmdd,
                    page_count, MAX_PAGES, MAX_PAGES)
    out = [by_date[d] for d in sorted(by_date)]
    if len(out) < total and page_count <= MAX_PAGES:
        log.warning("上交所两融 %s→%s: total=%d 但只取到 %d 行", begin_yyyymmdd, end_yyyymmdd, total, len(out))
    return out


def _validate(dates: List[str], rzye: List[Optional[float]], full: bool) -> None:
    if not dates:
        raise PanelError("上交所两融没有任何数据")
    compute.assert_ascending(dates, "上交所两融日期")
    bad = [d for d, v in zip(dates, rzye) if v is None or v <= 0]
    if bad:
        raise PanelError(f"上交所两融有 {len(bad)} 个非正融资余额，首个 {bad[0]}")
    if full and len(dates) < MIN_ROWS_FULL:
        raise PanelError(f"上交所两融全量只有 {len(dates)} 行（应 ≥ {MIN_ROWS_FULL}，2010-03-31 起）")


def get_margin_history(use_cache: bool = True) -> Dict[str, Any]:
    """沪市两融日度全历史 → ``{fetchedAt, dates[], rzye[], rqylje[], total[]}``（亿元，升序）。

    缓存新鲜直接返回；否则从 ``最后缓存日 − INCREMENTAL_DAYS``（或 ``FIRST_DAY``）抓到今天并按日期合并（新值覆盖）。
    全量返回空 → PanelError；增量返回空 → WARNING 并沿用缓存。
    """
    cached = cache_load(CACHE_SUB, CACHE_KEY) if use_cache else None
    if cached and cached.get("dates"):
        if cache_is_fresh(cached.get("fetchedAt")):
            log.info("上交所两融: 缓存命中 %d 行，截至 %s", len(cached["dates"]), cached["dates"][-1])
            return cached
        last = cached["dates"][-1]
        begin = (compute.parse_date(last) - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y%m%d")
        incremental = True
    else:
        cached = None
        begin = FIRST_DAY
        incremental = False

    end = today_compact()
    rows = fetch_range(begin, end)
    if not rows:
        if not incremental:
            raise PanelError(f"上交所两融全量抓取 {begin}→{end} 返回空（接口变化或参数不对）")
        log.warning("上交所两融: 增量区间 %s 起无新数据，沿用缓存", begin)

    new_dates = [r["date"] for r in rows]
    result: Dict[str, Any] = {"fetchedAt": iso_now()}
    dates: List[str] = []
    old_dates: List[str] = cached["dates"] if cached else []
    for f in FIELDS:
        old_vals = (cached.get(f) or [None] * len(old_dates)) if cached else []
        dates, vals = compute.merge_series(old_dates, old_vals, new_dates, [r[f] for r in rows])
        result[f] = vals
    result["dates"] = dates
    _validate(dates, result["rzye"], full=not incremental)
    cache_save(CACHE_SUB, CACHE_KEY, result)
    added = len(dates) - len(old_dates)
    log.info("上交所两融: 新增 %d 行，共 %d 行，%s → %s，最新融资余额 %.2f 亿元", added, len(dates), dates[0], dates[-1],
             result["rzye"][-1])
    return result
