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

申万行业总览 ``GET https://legulegu.com/stockdata/sw-industry-overview``（2026-10-19 探测，m2_work/probe_sources.txt §5）：
* 普通 HTML 页面（1.18 MB，约 1.4 s），只需 ``Referer: https://legulegu.com/``，不需要 CSRF/token；
* ``div#level1Items``（31 个一级）、``div#level2Items``（131 个二级）、``div#level3Items``（三级，不用）；
  每项 ``div.lg-industries-item-chinese-title`` = ``801016.SI``（去掉 ``.SI``），
  ``div.lg-industries-item-number`` = ``种植业(20)[农林牧渔]`` = 名称(成份数)[上级行业名]，
  上级只给名称，映射回一级代码靠 level1 的名称表；
* 这是申万接口本身不提供的 **二级 → 一级父级映射** 的唯一来源；代码前缀推不出父级
  （801092 → 汽车 801880、801101 → 计算机 801750 等），所以必须抓这页。
缓存 ``cache/legulegu/sw_overview.json`` = ``{fetchedAt, level1:{code:name}, level2:{code:{name,parent,parentName,count}}}``，
``PARENT_MAP_TTL_DAYS`` 天内复用；页面抓取失败但有旧缓存时打 WARNING 沿用旧映射。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_age_days, cache_is_fresh, cache_load, cache_save, http_get,
                        iso_now, market_closed, now_sh, today_compact, today_str)

log = logging.getLogger("etl.legulegu")

PAGE_URL = "https://legulegu.com/stockdata/sz50-ttm-lyr"
API_URL = "https://legulegu.com/api/stockdata/index-basic-pe"
KEY_PE = "addTtmPe"      # 整体法滚动市盈率；ttmPe 是等权口径，勿用
KEY_CLOSE = "close"
CACHE_SUB = "legulegu"
TIMEOUT = 30

SW_OVERVIEW_URL = "https://legulegu.com/stockdata/sw-industry-overview"
SW_OVERVIEW_TIMEOUT = 60
PARENT_MAP_TTL_DAYS = 30
CACHE_KEY_SW = "sw_overview.json"
MIN_LEVEL1 = 31      # 申万 2021 版一级行业数
MIN_LEVEL2 = 100     # 二级实际 131，低于 100 视为页面残缺
_L1_RE = re.compile(r"^(.*?)\((\d+)\)$")
_L2_RE = re.compile(r"^(.*?)\((\d+)\)\[(.*?)\]$")
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


# --------------------------------------------------------------------------- 申万行业总览（二级 → 一级）
class _SwOverviewParser(HTMLParser):
    """只认三样东西：容器 div 的 id（level1Items/level2Items/level3Items）切换当前层级；
    class 含 ``lg-industries-item`` 的 div 开始一个条目；条目内 ``…-chinese-title`` 与 ``…-item-number``
    两个 div 的全部文本（含嵌套 span）分别是代码与「名称(数量)[上级]」。
    页面里 ``<li>`` 与容器 div 的闭合并不规整，所以不按标签深度配对，只按出现顺序。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section: Optional[str] = None
        self.items: Dict[str, List[Tuple[str, str]]] = {"level1Items": [], "level2Items": []}
        self._code: Optional[str] = None
        self._capture: Optional[str] = None
        self._depth = 0
        self._buf: List[str] = []

    @staticmethod
    def _classes(attrs) -> List[str]:
        for k, v in attrs:
            if k == "class" and v:
                return v.split()
        return []

    def handle_starttag(self, tag, attrs):
        if self._capture is not None:
            self._depth += 1
            return
        if tag != "div":
            return
        attrs_d = dict(attrs)
        if attrs_d.get("id") in ("level1Items", "level2Items", "level3Items"):
            self.section = attrs_d["id"]
            return
        classes = self._classes(attrs)
        if "lg-industries-item" in classes:
            self._code = None
        elif "lg-industries-item-chinese-title" in classes:
            self._capture, self._depth, self._buf = "code", 0, []
        elif "lg-industries-item-number" in classes:
            self._capture, self._depth, self._buf = "number", 0, []

    def handle_endtag(self, tag):
        if self._capture is None:
            return
        if self._depth > 0:
            self._depth -= 1
            return
        text = re.sub(r"\s+", "", "".join(self._buf))
        kind, self._capture = self._capture, None
        if kind == "code":
            self._code = text[:-3] if text.endswith(".SI") else text
        elif self._code and self.section in self.items:
            self.items[self.section].append((self._code, text))
            self._code = None

    def handle_data(self, data):
        if self._capture is not None:
            self._buf.append(data)


def parse_sw_overview(html: str) -> Dict[str, Any]:
    """行业总览页 → {"level1": {code: name}, "level2": {code: {name, parent, parentName, count}}}。

    二级的上级名必须出现在一级名单里，否则 PanelError；一级少于 MIN_LEVEL1 也 PanelError。
    二级数量下限在 ``get_sw_parent_map`` 里检查（便于用裁剪过的页面做离线测试）。
    """
    p = _SwOverviewParser()
    p.feed(html)
    p.close()
    level1: Dict[str, str] = {}
    for code, text in p.items["level1Items"]:
        m = _L1_RE.match(text)
        if not m:
            raise PanelError(f"乐咕一级行业条目无法解析: {code} {text!r}")
        level1[code] = m.group(1)
    if len(level1) < MIN_LEVEL1:
        raise PanelError(f"乐咕行业总览只解析出 {len(level1)} 个一级行业（应为 {MIN_LEVEL1}），页面可能改版")
    name_to_code = {n: c for c, n in level1.items()}
    level2: Dict[str, Dict[str, Any]] = {}
    for code, text in p.items["level2Items"]:
        m = _L2_RE.match(text)
        if not m:
            raise PanelError(f"乐咕二级行业条目无法解析: {code} {text!r}")
        name, count, parent_name = m.group(1), int(m.group(2)), m.group(3)
        parent = name_to_code.get(parent_name)
        if parent is None:
            raise PanelError(f"乐咕二级行业 {code} {name} 的上级「{parent_name}」不在一级名单里")
        level2[code] = {"name": name, "parent": parent, "parentName": parent_name, "count": count}
    return {"level1": level1, "level2": level2}


def get_sw_parent_map(use_cache: bool = True) -> Dict[str, Any]:
    """申万 二级 → 一级 映射 → {fetchedAt, level1:{code:name}, level2:{code:{name,parent,parentName,count}}}。

    缓存 PARENT_MAP_TTL_DAYS 天内直接复用；过期则重抓；抓取/解析失败但有旧缓存 → WARNING 并沿用旧映射；
    没有缓存则抛出。
    """
    cached = cache_load(CACHE_SUB, CACHE_KEY_SW) if use_cache else None
    age = cache_age_days(cached.get("fetchedAt")) if isinstance(cached, dict) else None
    usable = bool(cached and cached.get("level1") and cached.get("level2"))
    if usable and age is not None and 0 <= age <= PARENT_MAP_TTL_DAYS:
        log.info("乐咕行业总览: 缓存命中（%.1f 天前），%d 个二级", age, len(cached["level2"]))
        return cached
    try:
        body, _hdr = http_get(SW_OVERVIEW_URL, timeout=SW_OVERVIEW_TIMEOUT, headers={
            "Referer": "https://legulegu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        parsed = parse_sw_overview(body.decode("utf-8", errors="replace"))
        if len(parsed["level2"]) < MIN_LEVEL2:
            raise PanelError(f"乐咕行业总览只解析出 {len(parsed['level2'])} 个二级行业（应 ≥ {MIN_LEVEL2}）")
    except PanelError as e:
        if usable:
            log.warning("乐咕行业总览抓取失败（%s），沿用 %d 天前的父级映射", e, round(age or 0))
            return cached
        raise
    result = {"fetchedAt": iso_now(), "level1": parsed["level1"], "level2": parsed["level2"]}
    cache_save(CACHE_SUB, CACHE_KEY_SW, result)
    log.info("乐咕行业总览: 下载 %d 个一级、%d 个二级", len(result["level1"]), len(result["level2"]))
    return result
