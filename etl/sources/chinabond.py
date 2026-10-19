# -*- coding: utf-8 -*-
"""数据源：中债登 收益率曲线历史查询 ``yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery``。

接口（2026-10-19 探测，m1_work/probe_sources.txt）：
* ``GET ?startDate=YYYY-01-01&endDate=YYYY-12-31&gjqx=0&qxId=ycqx&locale=cn_ZH``，带 ``Referer``；
* 返回的是 **HTML**（不是 JSON），第二个 ``<table>`` 表头
  ``曲线名称 / 日期 / 3月 / 6月 / 1年 / 3年 / 5年 / 7年 / 10年 / 30年``；
* 三条曲线的行交错排列（国债 / 商业银行普通债 AAA / 中短期票据 AAA），只保留
  ``曲线名称 == 中债国债收益率曲线`` 的行；日期降序，需反转；
* 一次最多查一年（页面限制 365 天）；2005 年无数据，2006 年从 3 月 1 日起有 214 行；
* 很慢：每年 5–13 秒，超时设 120 秒。

缓存 ``cache/chinabond/{year}.json``：往年文件一旦存在就不再请求；当年按 ``cache_is_fresh`` 规则刷新。
"""
from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now, now_sh)

log = logging.getLogger("etl.chinabond")

URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"
BASE_PARAMS = {"gjqx": "0", "qxId": "ycqx", "locale": "cn_ZH"}
HEADERS = {"Referer": "https://yield.chinabond.com.cn/"}
CURVE = "中债国债收益率曲线"
COL_DATE = "日期"
COL_10Y = "10年"
FIRST_YEAR = 2006
MIN_ROWS_PAST_YEAR = 200
TIMEOUT = 120
CACHE_SUB = "chinabond"
YIELD_MIN, YIELD_MAX = 0.0, 10.0


class _TableParser(HTMLParser):
    """把页面里所有 <table> 收集成 [[cell_text, ...], ...]（忽略注释与标签，合并空白）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._table: Optional[List[List[str]]] = None
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_history_html(html: str) -> List[Tuple[str, float]]:
    """解析查询页 → [(YYYY-MM-DD, 10 年期国债收益率 %)]，按日期升序。找不到数据表时抛 PanelError。"""
    p = _TableParser()
    p.feed(html)
    p.close()
    table = None
    for t in p.tables:
        if t and "曲线名称" in t[0] and COL_10Y in t[0]:
            table = t
            break
    if table is None:
        text = " ".join(html.split())[:200]
        raise PanelError(f"中债页面里没有收益率表，页面开头: {text}")
    header = table[0]
    i_date, i_10 = header.index(COL_DATE), header.index(COL_10Y)
    by_date: Dict[str, float] = {}
    for row in table[1:]:
        if len(row) <= max(i_date, i_10) or row[0] != CURVE:
            continue
        d = row[i_date][:10]
        try:
            y = float(row[i_10])
        except ValueError:
            continue
        by_date[d] = y
    return [(d, by_date[d]) for d in sorted(by_date)]


def validate_year(year: int, rows: List[Tuple[str, float]], current_year: int) -> None:
    """数值在 (0, 10) 之内；往年至少 200 行（2006 年只有 214 行，所以 200 是安全下限）。"""
    bad = [(d, y) for d, y in rows if not (YIELD_MIN < y < YIELD_MAX)]
    if bad:
        raise PanelError(f"中债 {year} 有 {len(bad)} 个异常收益率，首个 {bad[0]}")
    if year < current_year and len(rows) < MIN_ROWS_PAST_YEAR:
        raise PanelError(f"中债 {year} 只有 {len(rows)} 行，疑似接口变化")
    for d, _ in rows:
        if d[:4] != str(year):
            raise PanelError(f"中债 {year} 返回了别的年份的数据: {d}")


def fetch_year(year: int, use_cache: bool = True) -> Dict[str, Any]:
    """取某一年的国债 10Y 收益率 → {year, fetchedAt, dates[], yield[]}。"""
    current_year = now_sh().year
    key = f"{year}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cached.get("dates"):
        if year < current_year or cache_is_fresh(cached.get("fetchedAt")):
            log.info("中债 %d: 缓存命中 %d 行", year, len(cached["dates"]))
            return cached
    params = dict(BASE_PARAMS)
    params.update({"startDate": f"{year}-01-01", "endDate": f"{year}-12-31"})
    body, _hdr = http_get(URL + "?" + urlencode(params), timeout=TIMEOUT, headers=HEADERS)
    rows = parse_history_html(body.decode("utf-8", errors="replace"))
    validate_year(year, rows, current_year)
    if not rows and cached and cached.get("dates"):
        log.warning("中债 %d: 本次返回为空，沿用缓存", year)
        return cached
    result = {"year": year, "fetchedAt": iso_now(),
              "dates": [d for d, _ in rows], "yield": [y for _, y in rows]}
    cache_save(CACHE_SUB, key, result)
    log.info("中债 %d: 下载 %d 行（%d 字节）", year, len(rows), len(body))
    return result


def get_cgb10y(use_cache: bool = True) -> Dict[str, Any]:
    """2006 年至今的 10 年期国债收益率日序列 → {dates[], yield[], start, end, fetchedAt}。"""
    dates: List[str] = []
    ys: List[float] = []
    for year in range(FIRST_YEAR, now_sh().year + 1):
        r = fetch_year(year, use_cache=use_cache)
        dates.extend(r["dates"])
        ys.extend(r["yield"])
    if not dates:
        raise PanelError("中债没有取到任何收益率")
    compute.assert_ascending(dates, "中债日期")
    return {"dates": dates, "yield": ys, "start": dates[0], "end": dates[-1], "fetchedAt": iso_now()}
