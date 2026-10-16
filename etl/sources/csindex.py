# -*- coding: utf-8 -*-
"""数据源：中证指数有限公司 成分股名单（csindex.com.cn 公开下载文件）。

文件 ``{code}cons.xls`` 是真正的旧版 Excel（OLE2 复合文档），用 xlrd 2.x 解析。
表头为中英双语，已知陷阱：
* 「成份券代码」列是数字，000001 会变成 1 —— 需要补回前导零；
* 英文名列也含子串 ``Constituent Name``，定位列时要排除含「英文」的表头；
* 「日期Date」是数字 20261015，表示名单日期。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import xlrd

from etl.common import PanelError, cache_load, cache_save, http_get, today_compact, today_str

log = logging.getLogger("etl.csindex")

CONS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/"
    "{code}cons.xls"
)
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _find_col(headers: List[str], includes: List[str], excludes: List[str] = ()) -> int:
    for i, h in enumerate(headers):
        if any(k in h for k in includes) and not any(x in h for x in excludes):
            return i
    return -1


def _norm_code(v: Any) -> str:
    if isinstance(v, (int, float)):
        return str(int(v)).zfill(6)
    return str(v).strip().zfill(6)


def _norm_date(v: Any) -> str:
    s = str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
    s = s.replace("-", "").replace("/", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return ""


def parse_cons_xls(body: bytes) -> Dict[str, Any]:
    """解析成分股 .xls → {asOf, rows:[{code,name,exchange}], notes:[...]}。"""
    if body[:8] != OLE2_MAGIC:
        raise PanelError(f"csindex 返回的不是 .xls（OLE2）: content starts with {body[:16].hex()}")
    book = xlrd.open_workbook(file_contents=body)
    sheet = book.sheet_by_index(0)
    if sheet.nrows < 2:
        raise PanelError("csindex .xls 没有数据行")
    headers = [str(c).strip() for c in sheet.row_values(0)]
    i_code = _find_col(headers, ["成份券代码", "成分券代码", "Constituent Code"], ["英文"])
    i_name = _find_col(headers, ["成份券名称", "成分券名称"], ["英文"])
    i_exch = _find_col(headers, ["交易所"], ["英文"])
    i_date = _find_col(headers, ["日期", "Date"])
    if min(i_code, i_name) < 0:
        raise PanelError(f"csindex 表头缺少成分券代码/名称列，实际表头: {headers}")

    rows = []
    for r in range(1, sheet.nrows):
        vals = sheet.row_values(r)
        code = _norm_code(vals[i_code])
        if not code.strip("0"):
            continue
        rows.append({
            "code": code,
            "name": str(vals[i_name]).strip(),
            "exchange": str(vals[i_exch]).strip() if i_exch >= 0 else "",
        })
    notes: List[str] = []
    as_of = _norm_date(sheet.row_values(1)[i_date]) if i_date >= 0 else ""
    if not as_of:
        as_of = today_str()
        notes.append("成分文件缺少日期列，名单日期按抓取日记")
    return {"asOf": as_of, "rows": rows, "notes": notes}


def fetch_constituents(code: str, use_cache: bool = True) -> Dict[str, Any]:
    """取某指数成分名单；当日缓存命中则不发请求。"""
    key = f"{code}_{today_compact()}.json"
    if use_cache:
        cached = cache_load("csindex", key)
        if cached:
            log.info("csindex %s: 缓存命中 %d 行 (名单日期 %s)", code, len(cached["rows"]), cached["asOf"])
            return cached
    body, _hdr = http_get(CONS_URL.format(code=code), timeout=60)
    parsed = parse_cons_xls(body)
    cache_save("csindex", key, parsed)
    log.info("csindex %s: 下载 %d 字节，%d 行 (名单日期 %s)", code, len(body), len(parsed["rows"]), parsed["asOf"])
    return parsed
