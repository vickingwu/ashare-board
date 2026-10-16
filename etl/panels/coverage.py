# -*- coding: utf-8 -*-
"""面板 coverage —— 宽基指数覆盖范围。

把中证全指的全部成分按「总市值」从大到小排成一列，每 50 只切成一格，
数出每一格里有多少只属于沪深300 / 中证500 / 中证1000 / 中证2000 / 中证A500，
再算每个指数的市值占比、两两重叠矩阵。

``compute()`` 是纯函数（无 I/O），方便离线单测；``build()`` 负责抓数和校验。
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Set, Tuple

from etl.common import PanelError
from etl.sources.csindex import fetch_constituents
from etl.sources.tencent import fetch_quotes, latest_quote_date, to_symbol, verify_field_index

log = logging.getLogger("etl.coverage")

PANEL_ID = "coverage"
TITLE = "宽基指数覆盖范围"

INDEXES: List[Tuple[str, str]] = [
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("932000", "中证2000"),
    ("000985", "中证全指"),
    ("000510", "中证A500"),
]
NAME_OF = dict(INDEXES)
SIZE_CODES = [c for c, _ in INDEXES[:4]]
UNIVERSE = "000985"
BOUNDARIES = [300, 800, 1800, 3800]   # 四个规模层的名义累计只数
CELL = 50
EXPECTED = {
    "000300": (300, 300),
    "000905": (500, 500),
    "000852": (1000, 1000),
    "932000": (2000, 2000),
    "000510": (500, 500),
    "000985": (4500, 5600),
}


def _mv_of(quotes: Dict[str, dict], code: str):
    q = quotes.get(code)
    mv = q.get("mvTotal") if q else None
    return mv if (mv is not None and mv > 0) else None


def compute(cons: Dict[str, List[dict]], quotes: Dict[str, dict]) -> Tuple[dict, List[str]]:
    """纯计算：cons = {指数代码: [{code,name,...}]}，quotes = {股票代码: {mvTotal,...}}。"""
    notes: List[str] = []
    universe_rows = cons[UNIVERSE]
    universe_set: Set[str] = {r["code"] for r in universe_rows}
    name_of_stock = {r["code"]: r["name"] for rows in cons.values() for r in rows}

    bad_prefix = 0
    for r in universe_rows:
        try:
            to_symbol(r["code"])
        except ValueError:
            bad_prefix += 1
    if bad_prefix:
        notes.append(f"{bad_prefix} 只全指成分代码前缀无法识别交易所，未取行情")

    quoted = [(r["code"], _mv_of(quotes, r["code"])) for r in universe_rows]
    quoted = [(c, mv) for c, mv in quoted if mv is not None]
    quoted.sort(key=lambda t: (-t[1], t[0]))
    n = len(quoted)
    missing = len(universe_rows) - n
    if missing > 0:
        notes.append(f"{missing} 只全指成分未取到行情，已排除（不计入市值）")
    if n == 0:
        raise PanelError("没有任何全指成分取到行情")
    mv_total = sum(mv for _, mv in quoted)
    rank_of = {c: i + 1 for i, (c, _) in enumerate(quoted)}
    mv_of = dict(quoted)

    sets: Dict[str, Set[str]] = {code: {r["code"] for r in cons[code]} for code, _ in INDEXES}
    size_union: Set[str] = set().union(*(sets[c] for c in SIZE_CODES))
    uncovered_set = universe_set - size_union

    def summarize(members: Set[str]) -> Tuple[float, float, List[dict]]:
        with_mv = [(c, mv_of[c]) for c in members if c in mv_of]
        with_mv.sort(key=lambda t: (-t[1], t[0]))
        mv_sum = sum(mv for _, mv in with_mv)
        top3 = [{"code": c, "name": name_of_stock.get(c, ""), "mv": round(mv, 1)} for c, mv in with_mv[:3]]
        return round(mv_sum, 1), round(mv_sum / mv_total * 100, 2), top3

    indexes = []
    for code, name in INDEXES:
        s = sets[code]
        mv_sum, share, top3 = summarize(s)
        not_in_universe = len(s - universe_set)
        if not_in_universe and code != UNIVERSE:
            notes.append(f"{name} 有 {not_in_universe} 只成分不在中证全指名单中（名单日期不同步），市值统计时略过")
        indexes.append({
            "code": code, "name": name, "count": len(s),
            "mvSum": mv_sum, "mvShare": share, "top3": top3,
            "notInUniverse": not_in_universe,
        })

    unc_sum, unc_share, _ = summarize(uncovered_set)

    cells = []
    unc_cells = []
    for k in range(math.ceil(n / CELL)):
        chunk = quoted[k * CELL:(k + 1) * CELL]
        codes = {c for c, _ in chunk}
        unc_n = len(codes & uncovered_set)
        unc_cells.append(unc_n)
        cells.append({
            "k": k,
            "rankFrom": k * CELL + 1,
            "rankTo": k * CELL + len(chunk),
            "mvMax": round(chunk[0][1], 1),
            "mvMin": round(chunk[-1][1], 1),
            "counts": {code: len(codes & sets[code]) for code, _ in INDEXES},
            "uncovered": unc_n,
        })

    codes6 = [c for c, _ in INDEXES]
    overlap = {
        "codes": codes6,
        "names": [NAME_OF[c] for c in codes6],
        "matrix": [[round(100 * len(sets[a] & sets[b]) / len(sets[a]), 1) if sets[a] else 0.0
                    for b in codes6] for a in codes6],
    }

    mv_ladder = [{"code": c, "name": NAME_OF[c], "mvShare": next(i["mvShare"] for i in indexes if i["code"] == c)}
                 for c in SIZE_CODES]
    mv_ladder.append({"code": "uncovered", "name": "未被规模宽基覆盖", "mvShare": unc_share})

    data = {
        "universe": {
            "code": UNIVERSE, "name": NAME_OF[UNIVERSE],
            "count": n, "countListed": len(universe_rows),
            "sizeUnionCount": len(size_union & universe_set),
            "mvTotal": round(mv_total, 1),
        },
        "indexes": indexes,
        "cells": cells,
        "uncovered": {"count": len(uncovered_set), "mvSum": unc_sum, "mvShare": unc_share, "cells": unc_cells},
        "overlap": overlap,
        "mvLadder": mv_ladder,
        "boundaries": BOUNDARIES,
        "cellSize": CELL,
    }
    return data, notes


def build() -> dict:
    cons: Dict[str, List[dict]] = {}
    cons_dates: Dict[str, str] = {}
    for code, name in INDEXES:
        parsed = fetch_constituents(code)
        lo, hi = EXPECTED[code]
        cnt = len(parsed["rows"])
        if not (lo <= cnt <= hi):
            raise PanelError(f"{name} 成分数 {cnt} 不在 [{lo},{hi}]")
        cons[code] = parsed["rows"]
        cons_dates[code] = parsed["asOf"]
        log.info("%s %s: %d 只成分，名单日期 %s", code, name, cnt, parsed["asOf"])

    universe_codes = [r["code"] for r in cons[UNIVERSE]]
    quotes = fetch_quotes(universe_codes)
    field_check = verify_field_index(quotes)
    for line in field_check:
        log.info(line)
    quote_date, hhmm = latest_quote_date(quotes)

    data, notes = compute(cons, quotes)
    data["asOfQuote"] = quote_date
    data["quoteTime"] = hhmm
    data["asOfConstituents"] = cons_dates[UNIVERSE]
    data["fieldCheck"] = field_check

    notes.extend(field_check)
    distinct_dates = sorted(set(cons_dates.values()))
    if len(distinct_dates) == 1:
        notes.append(f"成分股名单日期 {distinct_dates[0]}（中证指数官网）")
    else:
        notes.append("成分股名单日期：" + "，".join(f"{NAME_OF[c]} {d}" for c, d in cons_dates.items()))
    if hhmm < "1500":
        notes.append(f"行情抓取于盘中 {hhmm[:2]}:{hhmm[2:]}，市值为盘中快照")
    else:
        notes.append(f"行情时间 {quote_date} {hhmm[:2]}:{hhmm[2:]}（收盘后快照）")

    return {
        "asOf": quote_date,
        "sources": ["中证指数有限公司 csindex.com.cn（成分股名单）", "腾讯行情 qt.gtimg.cn（个股总市值）"],
        "notes": notes,
        "data": data,
    }
