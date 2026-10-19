# -*- coding: utf-8 -*-
"""数据源：乐咕乐股 指数市盈率 ``legulegu.com/api/stockdata/index-basic-pe``。

两步请求（2026-10-19 探测，m1_work/probe_sources.txt）：
1. ``GET https://legulegu.com/stockdata/sz50-ttm-lyr`` 拿页面，页面 ``<meta name="_csrf" content="...">``
   是 CSRF 令牌，同时服务端下发的 cookie 由 ``common.py`` 的共享 Session 自动保存；
2. ``GET index-basic-pe?token=<md5(当天日期 'YYYY-MM-DD')>&indexCode=000300.SH``，
   头部 ``Referer`` 指向第 1 步页面、``X-CSRF-TOKEN`` 为 meta 值、``X-Requested-With: XMLHttpRequest``。
   ``indexCode`` 必须带交易所后缀（``.SH``；399 开头的深市指数用 ``.SZ``）。

响应 ``{"data":[{date, lyrPe, ttmPe, addLyrPe, addTtmPe, close, ...}], ...}``：
* **``addTtmPe`` 才是整体法（加权）滚动市盈率**，``ttmPe`` 是等权口径（沪深300 等权约 29 vs 整体法约 12），
  本看板统一用整体法，所以取 ``addTtmPe``；
* 非会员只返回每月一条（月末交易日）+ 最新交易日一条；
* 不支持的指数返回 ``{"data": []}``（如中证全指 000985）。

缓存 ``cache/legulegu/{code}_{YYYYMMDD}.json`` 保存整份结果（空结果也缓存，当天不再重问）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now,
                        market_closed, now_sh, today_compact, today_str)

log = logging.getLogger("etl.legulegu")

PAGE_URL = "https://legulegu.com/stockdata/sz50-ttm-lyr"
API_URL = "https://legulegu.com/api/stockdata/index-basic-pe"
KEY_PE = "addTtmPe"      # 整体法滚动市盈率；ttmPe 是等权口径，勿用
KEY_CLOSE = "close"
CACHE_SUB = "legulegu"
TIMEOUT = 30
# 乐咕有 PE 历史的指数（与 akshare 的映射一致），其它代码不必尝试
KNOWN_CODES = frozenset({
    "000016", "000300", "000009", "399673", "000905", "000010",
    "399324", "399330", "000852", "000015", "000903", "000906",
})

_CSRF_RE = re.compile(r'<meta\s+(?:[^>]*?\s)?name="_csrf"[^>]*?content="([^"]+)"|'
                      r'<meta\s+(?:[^>]*?\s)?content="([^"]+)"[^>]*?name="_csrf"', re.I)
_csrf_cache: Optional[str] = None


def token_for(d: date) -> str:
    """接口的 token = 当天日期 'YYYY-MM-DD' 的 md5 十六进制。"""
    return hashlib.md5(d.isoformat().encode("ascii")).hexdigest()


def to_lg_code(code: str) -> str:
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        raise ValueError(f"乐咕只接受 6 位数字指数代码: {code!r}")
    return c + (".SZ" if c.startswith("399") else ".SH")


def extract_csrf(html: str) -> str:
    m = _CSRF_RE.search(html)
    if not m:
        raise PanelError("乐咕页面没有 _csrf（可能被拦截或页面改版）")
    return m.group(1) or m.group(2)


def _csrf_token() -> str:
    global _csrf_cache
    if _csrf_cache is None:
        body, _hdr = http_get(PAGE_URL, timeout=TIMEOUT, headers={"Referer": "https://legulegu.com/"})
        _csrf_cache = extract_csrf(body.decode("utf-8", errors="replace"))
        log.debug("乐咕 _csrf 已取得")
    return _csrf_cache


def parse_pe(body: bytes) -> List[Dict[str, Any]]:
    """解析接口响应 → [{date, pe, close}]（升序）；data 为空返回 []。"""
    try:
        j = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PanelError(f"乐咕返回的不是 JSON: {type(e).__name__}: {body[:120]!r}")
    data = j.get("data") if isinstance(j, dict) else None
    if data is None:
        raise PanelError(f"乐咕响应缺少 data 字段: {str(j)[:200]}")
    rows: List[Dict[str, Any]] = []
    for r in data:
        d = str(r.get("date") or "")[:10]
        pe = r.get(KEY_PE)
        if len(d) != 10 or pe is None:
            continue
        if not isinstance(pe, (int, float)) or pe <= 0:
            raise PanelError(f"乐咕 {d} 的 {KEY_PE} 非正: {pe!r}")
        close = r.get(KEY_CLOSE)
        rows.append({"date": d, "pe": float(pe), "close": float(close) if isinstance(close, (int, float)) else None})
    rows.sort(key=lambda x: x["date"])
    for i in range(1, len(rows)):
        if rows[i]["date"] == rows[i - 1]["date"]:
            raise PanelError(f"乐咕日期重复: {rows[i]['date']}")
    return rows


def get_index_pe(code: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """取指数整体法 PE-TTM（月末 + 最新一日）→ {code, source, dates[], pe[], close[], fetchedAt}；乐咕没有该指数时返回 None。"""
    key = f"{code}_{today_compact()}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cache_is_fresh(cached.get("fetchedAt")):
        if cached.get("empty"):
            log.info("乐咕 %s: 缓存命中（无此指数）", code)
            return None
        log.info("乐咕 %s: 缓存命中 %d 行，截至 %s", code, len(cached["dates"]), cached["dates"][-1])
        return cached

    csrf = _csrf_token()
    url = API_URL + "?" + urlencode({"token": token_for(now_sh().date()), "indexCode": to_lg_code(code)})
    body, _hdr = http_get(url, timeout=TIMEOUT, headers={
        "Referer": PAGE_URL,
        "X-CSRF-TOKEN": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    rows = parse_pe(body)
    if rows and rows[-1]["date"] == today_str() and not market_closed():
        rows = rows[:-1]
    if not rows:
        cache_save(CACHE_SUB, key, {"code": code, "empty": True, "fetchedAt": iso_now()})
        log.info("乐咕 %s: 无此指数（data 为空）", code)
        return None
    result = {
        "code": code,
        "source": "legulegu",
        "fetchedAt": iso_now(),
        "dates": [r["date"] for r in rows],
        "pe": [r["pe"] for r in rows],
        "close": [r["close"] for r in rows],
    }
    compute.assert_ascending(result["dates"], f"乐咕 {code} 日期")
    cache_save(CACHE_SUB, key, result)
    log.info("乐咕 %s: 下载 %d 行，%s → %s，最新 PE %.2f", code, len(rows), rows[0]["date"], rows[-1]["date"], rows[-1]["pe"])
    return result
