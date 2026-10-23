# -*- coding: utf-8 -*-
"""黄金人民币价早期历史（2006-01 → 2016-11）的种子：世界银行月均美元金价 × 中国货币网美元兑人民币中间价月均 ÷ 31.1034768，
再乘以与上海黄金交易所 Au99.99 月均价在重叠期（2016-12 → 2024-12）的几何均值比 ``k``，入库为 ``state/gold_seed.json``。

为什么要种子：SGE ``graph/Dailyhq``（见 ``sge_gold``）只有滚动约 10 年的日线，2006–2016 的人民币金价没有免费的直接来源
（LBMA 403、FRED 超时、新浪 COMEX 同样只有 10 年）。世界银行 Pink Sheet 月均价（美元/盎司）与中间价都是静态历史，
所以只在本机构建一次、提交种子，运行时只抓 SGE。

来源（2026-10-23 探测，m4_work/probe_sources.txt §B3/§B6）：
* 世界银行 ``CMO-Historical-Data-Monthly.xlsx``（约 765 KB）：工作表 ``Monthly Prices``，第 4 行是名称行（含 ``Gold``）、
  第 5 行单位（``($/troy oz)``）、第 6 行起数据，第 0 列 ``YYYYMmm``；该稳定 URL 截至 2024M12（够用：只要 2006-01 → 2016-11
  与重叠期）。用标准库 ``zipfile`` + ``xml.etree`` 解析，不引入 openpyxl；``…`` 或非数字单元格 → None；
* 中国货币网 ``POST …/CcprHisNew``（``startDate`` / ``endDate`` / ``currency=USD/CNY`` / ``pageNum=1`` / ``pageSize=400``，需带
  ``Referer``）：``{"head":{"rep_code":"200"},"data":{"total","pageTotal"},"records":[{"date","values":["7.8087"]}]}``，
  新→旧，一年一页（243 行）；
* 两条腿的拼接：``k`` = 几何均值(SGE 月均 ÷ 原始腿 A 月均) 于 ``OVERLAP`` 内两边都有的月份（应 97 个月，k 约 1.00–1.03，
  体现 SGE 相对国际价的溢价与月均口径差）；重叠月不足 24 个抛 PanelError。

种子形状：``{"version": 1, "builtAt", "unit": "元/克（月均价）", "legAEnd", "k", "overlapMonths", "overlap": [start, end],
"sources": [...], "months": [...], "usdPerOz": [...], "usdcny": [...], "cnyPerGramRaw": [...], "cnyPerGram": [...]}``，
``months`` 必须恰好是 ``LEG_A_START..LEG_A_END``（131 个月）、连续、``cnyPerGram`` 全部 > 0、``0.9 ≤ k ≤ 1.2``（``load_seed`` 校验）。

命令：``python -m etl.sources.gold_history --build-seed [--out PATH]``（世界银行 1 次 + 中间价 19 次 + SGE 1 次，约 1 分钟）。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from etl import compute
from etl.common import STATE_DIR, PanelError, get_logger, http_get, http_post, iso_now, read_json, write_json_atomic

log = logging.getLogger("etl.gold_history")

SEED_PATH = STATE_DIR / "gold_seed.json"
G_PER_OZ = 31.1034768
WB_URL = ("https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/"
          "CMO-Historical-Data-Monthly.xlsx")
WB_SHEET = "Monthly Prices"
CM_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
CM_HEADERS = {
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}
CM_CURRENCY = "USD/CNY"
LEG_A_START = "2006-01"
LEG_A_END = "2016-11"
OVERLAP = ("2016-12", "2024-12")
FX_YEARS = range(2006, 2025)
MIN_OVERLAP = 24
K_RANGE = (0.9, 1.2)
SGE_LABEL = "上海黄金交易所 Au99.99（graph/Dailyhq）"

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WB_MONTH = re.compile(r"^(\d{4})M(\d{2})$")


# --------------------------------------------------------------------------- 世界银行 xlsx（纯函数）
def _col_index(ref: str) -> int:
    m = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (m.group(0) if m else "A"):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_sheet(zf: zipfile.ZipFile, sheet_path: str, shared: List[str]) -> List[List[Any]]:
    root = ET.fromstring(zf.read(sheet_path))
    sheet_data = root.find("m:sheetData", _NS)
    rows: List[List[Any]] = []
    if sheet_data is None:
        return rows
    for row in sheet_data:
        cells: Dict[int, Any] = {}
        for c in row:
            ref = c.get("r")
            t = c.get("t")
            v = c.find("m:v", _NS)
            if v is None:
                is_ = c.find("m:is", _NS)
                val = "".join(x.text or "" for x in is_.iter("{%s}t" % _NS["m"])) if is_ is not None else None
            else:
                val = v.text
                if t == "s" and val is not None:
                    val = shared[int(val)]
            cells[_col_index(ref)] = val
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i) for i in range(width)])
    return rows


def _sheet_rows(xlsx_bytes: bytes, sheet_name: str) -> List[List[Any]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    except zipfile.BadZipFile as e:
        raise PanelError(f"世界银行文件不是 xlsx（zip）: {e}; head={xlsx_bytes[:60]!r}")
    names = set(zf.namelist())
    if "xl/workbook.xml" not in names:
        raise PanelError("世界银行 xlsx 缺少 xl/workbook.xml")
    shared: List[str] = []
    if "xl/sharedStrings.xml" in names:
        ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in ss.iter("{%s}si" % _NS["m"]):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
    for s in wb.iter("{%s}sheet" % _NS["m"]):
        if s.get("name") != sheet_name:
            continue
        target = rid_to_target.get(s.get("{%s}id" % _RNS), "") or ""
        spath = target[1:] if target.startswith("/") else "xl/" + target
        if spath not in names:
            raise PanelError(f"世界银行 xlsx 工作表 {sheet_name!r} 指向不存在的 {spath}")
        return _read_sheet(zf, spath, shared)
    raise PanelError(f"世界银行 xlsx 没有工作表 {sheet_name!r}")


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def parse_worldbank_monthly(xlsx_bytes: bytes, column: str = "Gold") -> Dict[str, Any]:
    """Pink Sheet 月度 xlsx → ``{months["YYYY-MM"], gold[]}``（美元/盎司月均；``…`` / 非数字 → None）。

    名称行 = 前 10 行里含有单元格 ``column`` 的那一行；其下第 0 列匹配 ``YYYYMmm`` 的行是数据。
    """
    rows = _sheet_rows(xlsx_bytes, WB_SHEET)
    hdr_i = col = None
    for i, r in enumerate(rows[:10]):
        for j, x in enumerate(r):
            if isinstance(x, str) and x.strip().lower() == column.lower():
                hdr_i, col = i, j
    if hdr_i is None or col is None:
        raise PanelError(f"世界银行 {WB_SHEET!r} 前 10 行没有 {column!r} 列")
    months: List[str] = []
    gold: List[Optional[float]] = []
    for r in rows[hdr_i + 1:]:
        if not r or not isinstance(r[0], str):
            continue
        m = _WB_MONTH.match(r[0].strip())
        if not m:
            continue
        months.append(f"{m.group(1)}-{m.group(2)}")
        gold.append(_num(r[col]) if col < len(r) else None)
    if not months:
        raise PanelError("世界银行 xlsx 没有解析出任何月份")
    compute.assert_ascending(months, "世界银行月份")
    return {"months": months, "gold": gold}


# --------------------------------------------------------------------------- 中国货币网（纯函数）
def parse_chinamoney(body: Any) -> List[Tuple[str, float]]:
    """``CcprHisNew`` 响应 → ``[(YYYY-MM-DD, rate), …]`` 升序；``head.rep_code != "200"`` 或缺 ``records`` → PanelError。"""
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"中国货币网返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    head = j.get("head") if isinstance(j, dict) and isinstance(j.get("head"), dict) else {}
    if str(head.get("rep_code")) != "200":
        raise PanelError(f"中国货币网返回异常: {str(j)[:200]}")
    records = j.get("records")
    if not isinstance(records, list):
        raise PanelError(f"中国货币网返回缺少 records: {str(j)[:200]}")
    by_date: Dict[str, float] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "")[:10]
        vals = r.get("values")
        if len(d) != 10 or not isinstance(vals, list) or not vals:
            continue
        rate = _num(vals[0])
        if rate is None or rate <= 0:
            continue
        by_date[d] = rate
    return [(d, by_date[d]) for d in sorted(by_date)]


# --------------------------------------------------------------------------- 拼接与种子（纯函数）
def splice_factor(leg_a: Dict[str, Optional[float]], leg_b: Dict[str, Optional[float]], start: str, end: str
                  ) -> Tuple[float, int]:
    """``k`` = 几何均值(leg_b[m] / leg_a[m]) 于 ``[start, end]`` 内两边都有正值的月份；个数 < MIN_OVERLAP → PanelError。"""
    ratios: List[float] = []
    for m in sorted(leg_a):
        if m < start or m > end:
            continue
        a, b = leg_a.get(m), leg_b.get(m)
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        ratios.append(b / a)
    if len(ratios) < MIN_OVERLAP:
        raise PanelError(f"黄金两腿重叠月份只有 {len(ratios)} 个（应 ≥ {MIN_OVERLAP}，{start} → {end}）")
    k = compute.geometric_mean(ratios)
    if k is None:
        raise PanelError("黄金拼接系数无法计算")
    return k, len(ratios)


def build_seed(wb: Dict[str, Any], fx_daily: Sequence[Tuple[str, float]], sge_monthly: Dict[str, float],
               built_at: Optional[str] = None) -> Dict[str, Any]:
    """世界银行月均 + 中间价日值 + SGE 月均 → 种子 dict（见模块说明）。``LEG_A_START..LEG_A_END`` 任一月缺 WB 或 FX → PanelError。"""
    usd_by_month: Dict[str, Optional[float]] = dict(zip(wb["months"], wb["gold"]))
    fx_months, fx_means, _n = compute.month_mean([d for d, _ in fx_daily], [v for _, v in fx_daily])
    fx_by_month: Dict[str, float] = dict(zip(fx_months, fx_means))
    raw_by_month: Dict[str, float] = {}
    for m, usd in usd_by_month.items():
        fx = fx_by_month.get(m)
        if usd is not None and usd > 0 and fx is not None and fx > 0:
            raw_by_month[m] = usd * fx / G_PER_OZ
    k, n = splice_factor(raw_by_month, sge_monthly, OVERLAP[0], OVERLAP[1])
    months = compute.months_between(LEG_A_START, LEG_A_END)
    missing = [m for m in months if m not in raw_by_month]
    if missing:
        raise PanelError(f"黄金种子缺少月份（世界银行或中间价无值）: {missing[:6]}（共 {len(missing)} 个）")
    raw = [round(raw_by_month[m], 4) for m in months]
    return {
        "version": 1,
        "builtAt": built_at or iso_now(),
        "unit": "元/克（月均价）",
        "legAEnd": LEG_A_END,
        "k": round(k, 6),
        "overlapMonths": n,
        "overlap": [OVERLAP[0], OVERLAP[1]],
        "sources": [WB_URL, CM_URL, SGE_LABEL],
        "months": months,
        "usdPerOz": [usd_by_month[m] for m in months],
        "usdcny": [round(fx_by_month[m], 4) for m in months],
        "cnyPerGramRaw": raw,
        "cnyPerGram": [round(v * k, 2) for v in raw],
    }


def load_seed(path: Path = SEED_PATH) -> Dict[str, Any]:
    """读取并校验种子：存在、``version == 1``、``months == LEG_A_START..LEG_A_END``、``cnyPerGram`` 全部 > 0、``k`` 在 K_RANGE 内。"""
    seed = read_json(Path(path), default=None)
    if not isinstance(seed, dict):
        raise PanelError(f"黄金种子不存在或不是 JSON 对象: {path}（先运行 python -m etl.sources.gold_history --build-seed）")
    if seed.get("version") != 1:
        raise PanelError(f"黄金种子 version 不是 1: {seed.get('version')!r}")
    months = seed.get("months")
    expected = compute.months_between(LEG_A_START, LEG_A_END)
    if months != expected:
        raise PanelError(f"黄金种子月份应为 {LEG_A_START} → {LEG_A_END} 共 {len(expected)} 个连续月份，实际 "
                         f"{len(months) if isinstance(months, list) else months} 个")
    values = seed.get("cnyPerGram")
    if not isinstance(values, list) or len(values) != len(months) or any(v is None or v <= 0 for v in values):
        raise PanelError("黄金种子 cnyPerGram 长度不符或含非正值")
    k = seed.get("k")
    if not isinstance(k, (int, float)) or not (K_RANGE[0] <= k <= K_RANGE[1]):
        raise PanelError(f"黄金种子拼接系数 k={k!r} 不在 {K_RANGE} 内")
    return seed


# --------------------------------------------------------------------------- I/O（只在构建种子时用）
def fetch_worldbank() -> Dict[str, Any]:
    body, _hdr = http_get(WB_URL, timeout=120)
    parsed = parse_worldbank_monthly(body)
    log.info("世界银行 Pink Sheet: %d 字节，%d 个月，%s → %s", len(body), len(parsed["months"]), parsed["months"][0],
             parsed["months"][-1])
    return parsed


def fetch_chinamoney_year(year: int) -> List[Tuple[str, float]]:
    data = {"startDate": f"{year}-01-01", "endDate": f"{year}-12-31", "currency": CM_CURRENCY, "pageNum": 1, "pageSize": 400}
    body, _hdr = http_post(CM_URL, data=data, timeout=60, headers=CM_HEADERS)
    j = json.loads(body.decode("utf-8")) if isinstance(body, (bytes, bytearray)) else body
    page_total = ((j.get("data") or {}) if isinstance(j, dict) else {}).get("pageTotal")
    if page_total is not None and int(page_total) > 1:
        raise PanelError(f"中国货币网 {year} 年中间价超过一页（pageTotal={page_total}），请加大 pageSize")
    rows = parse_chinamoney(j)
    log.info("中国货币网 USD/CNY 中间价 %d: %d 行", year, len(rows))
    return rows


def build_and_write(out: Path = SEED_PATH) -> Dict[str, Any]:
    """抓世界银行 + 各年中间价 + SGE，构建种子并原子写入 ``out``；返回种子。"""
    from etl.sources import sge_gold

    wb = fetch_worldbank()
    fx: List[Tuple[str, float]] = []
    for year in FX_YEARS:
        fx.extend(fetch_chinamoney_year(year))
    sge = sge_gold.get_au9999()
    months, avgs, _n = sge_gold.monthly_averages(sge, complete_only=True)
    seed = build_seed(wb, fx, dict(zip(months, avgs)))
    write_json_atomic(Path(out), seed)
    log.info("黄金种子已写入 %s：%d 个月 %s → %s，k=%.6f（重叠 %d 个月），首月 %.2f 元/克", out, len(seed["months"]),
             seed["months"][0], seed["months"][-1], seed["k"], seed["overlapMonths"], seed["cnyPerGram"][0])
    return seed


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="黄金早期历史种子（世界银行 × 中间价，按 SGE 重叠期校准）")
    ap.add_argument("--build-seed", action="store_true", help="联网构建并写入种子")
    ap.add_argument("--out", default=str(SEED_PATH), help=f"输出路径（默认 {SEED_PATH}）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    get_logger("etl.gold_history", verbose=args.verbose)
    if not args.build_seed:
        ap.print_help()
        return 1
    try:
        seed = build_and_write(Path(args.out))
    except PanelError as e:
        log.error("构建失败: %s", e)
        return 1
    print(json.dumps({"k": seed["k"], "overlapMonths": seed["overlapMonths"], "months": [seed["months"][0], seed["months"][-1]],
                      "first": seed["cnyPerGram"][0], "last": seed["cnyPerGram"][-1]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
