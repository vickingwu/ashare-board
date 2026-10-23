# -*- coding: utf-8 -*-
"""数据源：上海证券交易所 每日概况（``query.sse.com.cn/commonQuery.do`` ``COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C``）— 沪市当日上市数、
市值、成交额、换手率，按品种分行。

接口（2026-10-23 探测，m4_work/probe_sources.txt §E4）：``GET …/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C
&PRODUCT_CODE=01,02,03,11,17&type=inParams&SEARCH_DATE=YYYY-MM-DD&_=<毫秒>``，需带 ``Referer: https://www.sse.com.cn/``；
返回 ``{result: [{PRODUCT_CODE, TRADE_DATE "20261022", LIST_NUM, TOTAL_VALUE, NEGO_VALUE, TRADE_AMT, TRADE_VOL, TRADE_NUM,
AVG_PE_RATE, TOTAL_TO_RATE, NEGO_TO_RATE}]}``，**全部是字符串**，金额单位 亿元、股数 亿股、换手率 %；``-`` 表示无值。
``PRODUCT_CODE``：01 主板 A 股、02 B 股、03 科创板、11 存托凭证、17 股票合计（含 B 股）。非交易日 ``result`` 为空数组。

口径：沪市 A 股 = 01 + 03 + 11（``A_SHARE_CODES``）= 17 − 02（相差 ≤ 0.01），``a_share_totals`` 按此求和，
换手率 = 成交额 ÷ 流通市值 × 100。当日概况在收盘后当晚可查。

缓存 ``cache/sse/overview_YYYYMMDD.json``：有数据存 ``{fetchedAt, date, rows}``，空结果存 ``{"empty": true, "fetchedAt"}``
——同一天内按 ``cache_is_fresh`` 不重复问（节假日每天最多问一次）；``get_latest_overview`` 从给定日期往前最多
``MAX_BACK_DAYS`` 个自然日，周末不发请求。每次构建通常只需 1 个请求。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from etl import compute
from etl.common import PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now

log = logging.getLogger("etl.sse_overview")

URL = "https://query.sse.com.cn/commonQuery.do"
SQL_ID = "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C"
PRODUCT_CODES = "01,02,03,11,17"
A_SHARE_CODES = ("01", "03", "11")
HEADERS = {"Referer": "https://www.sse.com.cn/", "Accept": "application/json, text/javascript, */*; q=0.01"}
CACHE_SUB = "sse"
MAX_BACK_DAYS = 5
TIMEOUT = 60

FIELDS = {
    "listNum": "LIST_NUM",
    "totalValue": "TOTAL_VALUE",
    "negoValue": "NEGO_VALUE",
    "tradeAmt": "TRADE_AMT",
    "tradeVol": "TRADE_VOL",
    "negoToRate": "NEGO_TO_RATE",
}


# --------------------------------------------------------------------------- 解析（纯函数）
def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_overview(body: Any) -> Optional[Dict[str, Any]]:
    """接口响应 → ``{date "YYYY-MM-DD", rows{code: {listNum, totalValue, negoValue, tradeAmt, tradeVol, negoToRate}}}``。

    ``result`` 为空数组（非交易日）→ None；非 JSON / 缺 ``result`` → PanelError；``-`` → None。
    """
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"上交所每日概况返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    if not isinstance(j, dict) or not isinstance(j.get("result"), list):
        raise PanelError(f"上交所每日概况返回缺少 result 数组: {str(j)[:200]}")
    rows: Dict[str, Dict[str, Optional[float]]] = {}
    date = None
    for r in j["result"]:
        if not isinstance(r, dict):
            continue
        code = str(r.get("PRODUCT_CODE") or "").strip()
        td = str(r.get("TRADE_DATE") or "").strip()
        if not code or len(td) != 8 or not td.isdigit():
            continue
        d = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        if date is None:
            date = d
        elif d != date:
            raise PanelError(f"上交所每日概况同一响应里日期不一致: {date} / {d}")
        rows[code] = {k: _num(r.get(src)) for k, src in FIELDS.items()}
    if not rows or date is None:
        return None
    return {"date": date, "rows": rows}


def a_share_totals(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """沪市 A 股合计（01 + 03 + 11）→ ``{date, listNum, totalValue, negoValue, tradeAmt, turnoverPct}``（亿元 / %）。"""
    rows = parsed["rows"]
    present = [c for c in A_SHARE_CODES if c in rows]
    if "01" not in present:
        raise PanelError(f"上交所每日概况 {parsed.get('date')} 缺少主板 A 股（01）行")

    def total(field: str) -> Optional[float]:
        vals = [rows[c].get(field) for c in present]
        vals = [v for v in vals if v is not None]
        return round(sum(vals), 2) if vals else None

    nego = total("negoValue")
    amt = total("tradeAmt")
    list_num = total("listNum")
    return {
        "date": parsed["date"],
        "listNum": int(list_num) if list_num is not None else None,
        "totalValue": total("totalValue"),
        "negoValue": nego,
        "tradeAmt": amt,
        "turnoverPct": compute.safe_div_pct(amt, nego, 4),
    }


# --------------------------------------------------------------------------- I/O
def _url(date: str) -> str:
    return URL + "?" + urlencode({
        "sqlId": SQL_ID,
        "PRODUCT_CODE": PRODUCT_CODES,
        "type": "inParams",
        "SEARCH_DATE": date,
        "_": int(time.time() * 1000),
    })


def get_overview(date: str) -> Optional[Dict[str, Any]]:
    """某日每日概况（``YYYY-MM-DD``）→ ``{date, rows}`` 或 None（非交易日）。按日缓存，空结果也缓存一天。"""
    key = f"overview_{date.replace('-', '')}.json"
    cached = cache_load(CACHE_SUB, key)
    if cached:
        if cached.get("empty"):
            if cache_is_fresh(cached.get("fetchedAt")):
                log.debug("上交所每日概况 %s: 缓存记录为无数据", date)
                return None
        elif cached.get("rows"):
            log.debug("上交所每日概况 %s: 缓存命中", date)
            return {"date": cached.get("date", date), "rows": cached["rows"]}
    body, _hdr = http_get(_url(date), timeout=TIMEOUT, headers=HEADERS)
    parsed = parse_overview(body)
    if parsed is None:
        cache_save(CACHE_SUB, key, {"empty": True, "fetchedAt": iso_now()})
        log.info("上交所每日概况 %s: 无数据（非交易日或尚未发布）", date)
        return None
    cache_save(CACHE_SUB, key, {"fetchedAt": iso_now(), "date": parsed["date"], "rows": parsed["rows"]})
    log.info("上交所每日概况 %s: %d 个品种", parsed["date"], len(parsed["rows"]))
    return parsed


def get_latest_overview(on_or_before: str) -> Optional[Dict[str, Any]]:
    """从 ``on_or_before`` 往前找最近一个有数据的交易日（≤ ``MAX_BACK_DAYS`` 个自然日，周末跳过不发请求）。"""
    d = compute.parse_date(on_or_before)
    for _ in range(MAX_BACK_DAYS + 1):
        s = d.isoformat()
        if d.weekday() < 5:
            parsed = get_overview(s)
            if parsed is not None:
                return parsed
        d -= timedelta(days=1)
    log.warning("上交所每日概况: %s 往前 %d 天都没有数据", on_or_before, MAX_BACK_DAYS)
    return None
