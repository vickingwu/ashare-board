# -*- coding: utf-8 -*-
"""面板 decomp —— 指数收益来源拆解。

把一个指数的全收益涨幅拆成三块（加法、残差法，单位 %）：
    估值 = PE 期末 / PE 期初 − 1
    盈利 = 价格指数涨幅 − 估值          （价格涨幅里扣掉估值变化，剩下的归于盈利增长）
    股息 = 全收益涨幅 − 价格指数涨幅    （两者之差就是分红再投资的贡献）
    全收益 = 股息 + 盈利 + 估值         （恒等式，compute.decompose 内断言）

区间按月末对齐：起点 = 价格、全收益、PE 三者都有月末值的第一个月，终点 = 最后一个共同月末。
各指数 PE 起点不同（见 pe_select），所以区间不同，横向比较时看「每年」口径更合适。
另给按自然年的分年拆解（每年内部同样加法拆分；分年数字不能直接相加得到全区间值，因为跨年是复利）。
``compute_one()`` 是纯函数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from etl import compute
from etl.common import PanelError, _fmt_exc
from etl.sources import pe_select
from etl.sources.csindex_hist import get_index_history

log = logging.getLogger("etl.decomp")

PANEL_ID = "decomp"
TITLE = "指数收益来源拆解"

# (价格指数代码, 全收益指数代码, 名称)
INDEXES: List[Tuple[str, str, str]] = [
    ("000300", "H00300", "沪深300"),
    ("000905", "H00905", "中证500"),
    ("000852", "H00852", "中证1000"),
    ("000906", "H00906", "中证800"),
    ("000985", "H00985", "中证全指"),
    ("H30269", "H20269", "红利低波"),
]
MIN_MONTHS = 24
MIN_SURVIVORS = 3
FORMULA = "全收益 = 股息 + 盈利 + 估值；估值 = PE 期末 ÷ PE 期初 − 1；盈利 = 价格涨幅 − 估值；股息 = 全收益涨幅 − 价格涨幅"


def _per_year(parts: Dict[str, float], years: float) -> Dict[str, float]:
    return {k: round(parts[k] / years, 2) for k in ("totalReturn", "dividend", "earnings", "valuation")}


def compute_one(price: Tuple[List[str], List[float]], tr: Tuple[List[str], List[float]],
                pe: Tuple[List[str], List[float]]) -> Dict[str, Any]:
    """价格日线、全收益日线、月末 PE → 全区间拆解 + 分年拆解。"""
    p_d, p_v = compute.month_end_sample(price[0], price[1], complete_only=True)
    t_d, t_v = compute.month_end_sample(tr[0], tr[1], complete_only=True)
    months, vals, dates = compute.align_by_month({"p": (p_d, p_v), "tr": (t_d, t_v), "pe": pe})
    n = len(months)
    if n < MIN_MONTHS:
        raise PanelError(f"价格 / 全收益 / PE 三者共同的月末只有 {n} 个（< {MIN_MONTHS}）")
    P, T, E = vals["p"], vals["tr"], vals["pe"]
    D = dates["p"]      # 区间日期用价格指数的月末交易日；PE 在个别月份（如 2011-09）最后一天缺值时会落在前一两天
    start_d, end_d = D[0], D[-1]
    years = compute.years_between(start_d, end_d)
    whole = compute.decompose(P[0], P[-1], T[0], T[-1], E[0], E[-1])

    # 分年：以每个自然年最后一个共同月末为节点
    last_idx_of_year: Dict[str, int] = {}
    for i, m in enumerate(months):
        last_idx_of_year[m[:4]] = i
    by_year = []
    prev_i = 0
    for y in sorted(last_idx_of_year):
        i = last_idx_of_year[y]
        if i == prev_i:
            # 起点恰好是该年最后一个月末（如 2013-12）→ 该年没有可拆的区间
            continue
        parts = compute.decompose(P[prev_i], P[i], T[prev_i], T[i], E[prev_i], E[i])
        full = months[prev_i].endswith("-12") and months[prev_i][:4] == str(int(y) - 1) and months[i].endswith("-12")
        by_year.append({
            "year": int(y), "from": D[prev_i], "to": D[i], "partial": not full,
            "totalReturn": parts["totalReturn"], "dividend": parts["dividend"],
            "earnings": parts["earnings"], "valuation": parts["valuation"],
            "peFrom": round(E[prev_i], 2), "peTo": round(E[i], 2),
        })
        prev_i = i

    return {
        "startDate": start_d, "endDate": end_d, "months": n, "years": round(years, 2),
        "priceStart": round(P[0], 2), "priceEnd": round(P[-1], 2),
        "trStart": round(T[0], 2), "trEnd": round(T[-1], 2),
        "peStart": round(E[0], 2), "peEnd": round(E[-1], 2),
        "totalReturn": whole["totalReturn"], "priceReturn": whole["priceReturn"],
        "dividend": whole["dividend"], "earnings": whole["earnings"], "valuation": whole["valuation"],
        "cagrTotal": round(compute.cagr(T[0], T[-1], years), 2),
        "cagrPrice": round(compute.cagr(P[0], P[-1], years), 2),
        "perYear": _per_year(whole, years),
        "byYear": by_year,
    }


def build() -> dict:
    indexes = []
    skipped = []
    sources_used = set()
    for code, tr_code, name in INDEXES:
        try:
            price = get_index_history(code)
            tr = get_index_history(tr_code)
            sel = pe_select.get_monthly_pe(code, name, pe_code=tr_code)
            one = compute_one((price["dates"], price["close"]), (tr["dates"], tr["close"]),
                              (sel["meDates"], sel["mePe"]))
        except PanelError as e:
            log.warning("%s %s 跳过: %s", code, name, _fmt_exc(e))
            skipped.append(f"{name}：缺少价格 / 全收益 / PE 之一，未纳入（{_fmt_exc(e)}）")
            continue
        one.update({"code": code, "trCode": tr_code, "name": name, "peSource": sel["source"],
                    "peSourceNote": sel["sourceNote"]})
        sources_used.add(sel["source"])
        indexes.append(one)
        log.info("%s %s %s → %s（%.1f 年）: 全收益 %+.1f%% = 股息 %+.1f%% + 盈利 %+.1f%% + 估值 %+.1f%%（PE %.2f → %.2f）",
                 code, name, one["startDate"], one["endDate"], one["years"], one["totalReturn"], one["dividend"],
                 one["earnings"], one["valuation"], one["peStart"], one["peEnd"])
    if len(indexes) < MIN_SURVIVORS:
        raise PanelError(f"只有 {len(indexes)} 个指数可拆解（< {MIN_SURVIVORS}）: {'; '.join(skipped)}")

    sources = ["中证指数有限公司 csindex.com.cn（价格指数与全收益指数日行情）"]
    if "legulegu" in sources_used:
        sources.append("乐咕乐股 legulegu.com（指数整体法滚动市盈率）")
    notes = [
        "拆解是加法残差法：先用 PE 变化算出「估值」，价格涨幅扣掉估值记为「盈利」，全收益涨幅减价格涨幅记为「股息」；"
        "三项之和恒等于全收益涨幅。「盈利」实际是价格里不能被 PE 解释的部分，包含成分调整等因素。",
        "区间起点 = 价格、全收益、PE 三者都有月末值的第一个月末，终点 = 最后一个已完结月份的月末；"
        "各指数 PE 历史长短不同，区间也不同，全区间数字不能横向比大小，看「每年（百分点/年）」更合适。",
        "沪深300 / 中证500 / 中证1000 / 中证800 的 PE 来自乐咕乐股（整体法）；中证全指（2011-09 起）、红利低波（2013-12 起）"
        "的 PE 来自中证指数官网日行情。",
        "分年拆解以每个自然年的最后一个月末为节点，年内同样加法拆分；首尾两年若不满整年标「部分」。"
        "各年数字之间是复利关系，直接相加不等于全区间值。",
        "价格与全收益指数的起点值不同（全收益从基日起累计分红），只影响水平不影响涨幅。",
    ]
    notes.extend(skipped)
    data = {"indexes": indexes, "formula": FORMULA}
    return {"asOf": max(i["endDate"] for i in indexes), "sources": sources, "notes": notes, "data": data}
