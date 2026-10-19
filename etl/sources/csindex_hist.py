# -*- coding: utf-8 -*-
"""数据源：中证指数官网 指数日行情 ``csindex-home/perf/index-perf``（价格指数与全收益指数通用）。

接口（2026-10-19 探测，m1_work/probe_sources.txt）：
* ``GET ?indexCode={code}&startDate=YYYYMMDD&endDate=YYYYMMDD``，需带 ``Referer``；
  返回 ``{"code":"200","msg":"Success","data":[...],"success":true}``，注意 ``code`` 是字符串；
* 不限区间：从 20050101 到今天一次返回全部（沪深300 约 5,290 行、1.8 MB）；
* 每行 ``tradeDate``（YYYYMMDD）、``close``、``peg``（滚动市盈率，价格与全收益序列上数值相同），
  其它字段不用；
* 第 0 行永远是 ``20050101``（周六）的基点值，即使指数基日更晚（中证2000 基日 2013-12-31）——
  周末日期的行一律丢弃；
* 只给已收盘的交易日；接口不存在的代码返回 ``data: []`` 而不是错误状态。

缓存 ``cache/csindex_hist/{code}.json`` 保存整条历史；每次只请求 ``[最后缓存日 − 15 天, 今天]``
并按日期合并（新值覆盖旧值），15 天足以跨过春节假期。同一天内按 ``cache_is_fresh`` 规则不重复请求。
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now,
                        market_closed, today_compact, today_str)

log = logging.getLogger("etl.csindex_hist")

PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
HEADERS = {"Referer": "https://www.csindex.com.cn/", "Accept": "application/json, text/plain, */*"}
FULL_START = "20050101"
INCREMENTAL_DAYS = 15
CACHE_SUB = "csindex_hist"
TIMEOUT = 60

Row = Tuple[str, float, Optional[float]]   # (YYYY-MM-DD, close, pe|None)


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def parse_perf(body: bytes) -> Dict[str, Any]:
    """解析接口响应 → {code, name, nameFull, rows:[(date, close, pe)]}。空 data → rows=[]。"""
    try:
        j = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PanelError(f"csindex perf 不是 JSON: {type(e).__name__}: {body[:120]!r}")
    if not isinstance(j, dict) or str(j.get("code")) != "200" or not j.get("success"):
        raise PanelError(f"csindex perf 返回异常: {str(j)[:200]}")
    data = j.get("data") or []
    by_date: Dict[str, Tuple[float, Optional[float]]] = {}
    code = name = name_full = ""
    for r in data:
        td = str(r.get("tradeDate") or "")
        if len(td) != 8 or not td.isdigit():
            continue
        d = _iso(td)
        if compute.is_weekend(d):
            continue
        close = r.get("close")
        if close is None or close <= 0:
            continue
        pe = r.get("peg")
        pe = float(pe) if isinstance(pe, (int, float)) and pe > 0 else None
        by_date[d] = (float(close), pe)
        if not code:
            code = str(r.get("indexCode") or "")
            name = str(r.get("indexNameCn") or "")
            name_full = str(r.get("indexNameCnAll") or "")
    rows: List[Row] = [(d, by_date[d][0], by_date[d][1]) for d in sorted(by_date)]
    return {"code": code, "name": name, "nameFull": name_full, "rows": rows}


def _request(code: str, start: str, end: str) -> Dict[str, Any]:
    url = PERF_URL + "?" + urlencode({"indexCode": code, "startDate": start, "endDate": end})
    body, _hdr = http_get(url, timeout=TIMEOUT, headers=HEADERS)
    parsed = parse_perf(body)
    log.debug("csindex perf %s %s-%s: %d 字节，%d 行", code, start, end, len(body), len(parsed["rows"]))
    return parsed


def _validate(code: str, dates: List[str], close: List[float]) -> None:
    if not dates:
        raise PanelError(f"csindex {code} 没有任何行情")
    compute.assert_ascending(dates, f"csindex {code} 日期")
    bad = [d for d, v in zip(dates, close) if v is None or v <= 0]
    if bad:
        raise PanelError(f"csindex {code} 有 {len(bad)} 个非正收盘值，首个 {bad[0]}")


def get_index_history(code: str, use_cache: bool = True) -> Dict[str, Any]:
    """取某指数全历史日线 → {code, name, nameFull, dates[], close[], pe[], start, fetchedAt}。"""
    key = f"{code}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cached.get("dates"):
        if cache_is_fresh(cached.get("fetchedAt")):
            log.info("csindex %s: 缓存命中 %d 行，截至 %s", code, len(cached["dates"]), cached["dates"][-1])
            return cached
        last = cached["dates"][-1]
        start = (compute.parse_date(last) - timedelta(days=INCREMENTAL_DAYS)).strftime("%Y%m%d")
        incremental = True
    else:
        cached = None
        start = FULL_START
        incremental = False

    parsed = _request(code, start, today_compact())
    new_rows = parsed["rows"]
    if not new_rows:
        if not incremental:
            raise PanelError(f"csindex {code} 返回空数据（代码不存在或接口变化）")
        log.warning("csindex %s: 增量区间 %s 起无新数据，沿用缓存", code, start)

    old_dates = cached["dates"] if cached else []
    old_vals = list(zip(cached["close"], cached["pe"])) if cached else []
    dates, pairs = compute.merge_series(old_dates, old_vals,
                                        [r[0] for r in new_rows], [(r[1], r[2]) for r in new_rows])
    if dates and dates[-1] == today_str() and not market_closed():
        dates, pairs = dates[:-1], pairs[:-1]
    close = [p[0] for p in pairs]
    pe = [p[1] for p in pairs]
    _validate(code, dates, close)

    result = {
        "code": parsed["code"] or (cached or {}).get("code") or code,
        "name": parsed["name"] or (cached or {}).get("name") or code,
        "nameFull": parsed["nameFull"] or (cached or {}).get("nameFull") or code,
        "start": dates[0],
        "fetchedAt": iso_now(),
        "dates": dates,
        "close": close,
        "pe": pe,
    }
    cache_save(CACHE_SUB, key, result)
    added = len(dates) - len(old_dates)
    log.info("csindex %s %s: 新增 %d 行，共 %d 行，截至 %s", code, result["name"], added, len(dates), dates[-1])
    return result
