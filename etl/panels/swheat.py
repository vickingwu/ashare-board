# -*- coding: utf-8 -*-
"""面板 swheat —— 申万一级行业年度涨跌幅与长期年化。

31 个申万一级行业（2021 版分类）每个自然年的涨跌幅 + 年初至今 + 全程 / 10 年 / 5 年 / 3 年年化，
配合各年排名，供前端画年度热力表。

口径：
* 收盘来自申万指数分析日报表（价格指数，股息未计入）；基日 1999-12-30 = 1000 点，是 2000 年的基准；
* 年 Y 的年末收盘 = 落在 ``[Y-12-20, Y-12-31]`` 内最后一条记录；年度涨跌幅 = 年末 ÷ 上年末 − 1；
  两端任一缺失即 ``null``，不用邻近日期顶替；
* 各行业历史起点不同：16 个一级从 1999-12-30 起（``BASE_1999_CODES``），12 个从 2014-02-21 起（首个完整年 2015），
  3 个（石油石化 / 环保 / 美容护理）从 2021-12-13 起（首个完整年 2022）。起点之前的格子为 ``null``；
* 源数据本身可能有洞：煤炭（801950）在分析接口里 2017-01-23 → 2021-12-10 没有任何记录（四个字段全空），
  于是 2017–2021 五个年度为 ``null``，列入该行业的 ``gapYears``，不用其它来源补、也不丢掉 2015–2016 的真实值。
  回填失败的特征是「同一年所有行业都缺」，由「每年 ≥ 10 个行业有数据」的自检兜底；
* 年化只跨以上一完整年结尾的最近一段**连续**完整年度：``all`` 从 ``cagrYears.from`` 到 ``lastYear``
  （``cagrYears.all`` 为年数；没有断档时 from = firstYear），``y10 / y5 / y3`` 在连续完整年数不足时为 ``null``。
  不同行业的 ``all`` 起点不同，不能横向比较；
* 排名 1 = 当年涨幅最高，并列共享较小名次；``nByYear[y]`` 是该年有数据的行业数。

数据形状：``{baseDate, years[], ytdYear, ytdAsOf, nByYear[], nYtd,
industries:[{code, name, firstYear, gapYears[], rets[], ytd, ytdRank, cagr{all,y10,y5,y3}, cagrYears{all,from}, ranks[]}]}``，
``rets / ranks`` 与 ``years`` 等长。``compute_panel()`` 是纯函数；``build()`` 负责抓数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from etl import compute
from etl.common import PanelError
from etl.sources import sws

log = logging.getLogger("etl.swheat")

PANEL_ID = "swheat"
TITLE = "申万一级行业年度涨跌幅"

BASE_DATE = "1999-12-30"
FIRST_YEAR = 2000
CAGR_WINDOWS = (("y10", 10), ("y5", 5), ("y3", 3))
MAX_ABS_RET = 350.0
MIN_PER_YEAR = 10
INDUSTRY_COUNT = 31
# 以 1999-12-30 为基日的 16 个一级行业：这些行业的 firstYear 必须是 2000，否则说明回填不完整
BASE_1999_CODES = frozenset({
    "801010", "801030", "801040", "801050", "801080", "801110", "801120", "801130",
    "801140", "801150", "801160", "801170", "801180", "801200", "801210", "801230",
})


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _one_industry(h: Dict[str, Any], years: List[int], as_of_year: int) -> Dict[str, Any]:
    """单个行业：年度涨跌幅、起始年、YTD、年化（排名在外层统一算）。"""
    name = h.get("name") or h["code"]
    dates, close = compute.drop_none(h["dates"], h["close"])
    if not dates:
        raise PanelError(f"{name}（{h['code']}）没有任何收盘数据")
    compute.assert_ascending(dates, f"{name} 日期")
    raw = compute.calendar_year_returns(dates, close, years)
    first_year: Optional[int] = None
    gap_years: List[int] = []
    for y, r in zip(years, raw):
        if r is None:
            if first_year is not None:
                gap_years.append(y)
            continue
        if first_year is None:
            first_year = y
        if abs(r) >= MAX_ABS_RET:
            raise PanelError(f"{name}（{h['code']}）{y} 年涨跌幅 {r:.1f}% 超出 ±{MAX_ABS_RET:.0f}% 合理范围")
    if first_year is None:
        raise PanelError(f"{name}（{h['code']}）在 {years[0]}–{years[-1]} 没有任何完整年度")
    last_year = years[-1]
    # 年化只跨「以 last_year 结尾的最近一段连续完整年度」：有断档时从断档后的第一年起算，窗口跨过断档则为 None
    block_start = (gap_years[-1] + 1) if gap_years else first_year
    n_all = last_year - block_start + 1
    cagr: Dict[str, Optional[float]] = {}
    if n_all <= 0:
        cagr["all"] = None
        n_all = 0
    else:
        cagr["all"] = _r(compute.cagr_over_years(dates, close, last_year, n_all))
    for key, n in CAGR_WINDOWS:
        if n_all <= 0 or last_year - n + 1 < block_start:
            cagr[key] = None
        else:
            cagr[key] = _r(compute.cagr_over_years(dates, close, last_year, n))
    return {
        "code": h["code"],
        "name": name,
        "firstYear": first_year,
        "gapYears": gap_years,
        "rets": [_r(r) for r in raw],
        "ytd": _r(compute.ytd_return(dates, close, as_of_year)),
        "ytdRank": None,
        "cagr": cagr,
        "cagrYears": {"all": n_all, "from": block_start if n_all else None},
        "ranks": [None] * len(years),
    }


def compute_panel(histories: List[Dict[str, Any]], as_of: str) -> Dict[str, Any]:
    """histories: [{code, name, dates[], close[]}]（31 个一级；close 可含 None）→ data。"""
    if len(histories) != INDUSTRY_COUNT:
        raise PanelError(f"申万一级行业应为 {INDUSTRY_COUNT} 个，拿到 {len(histories)} 个")
    as_of_year = int(as_of[:4])
    last_year = as_of_year - 1
    years = list(range(FIRST_YEAR, last_year + 1))
    if not years:
        raise PanelError(f"asOf={as_of} 之前没有完整自然年")
    industries = [_one_industry(h, years, as_of_year) for h in histories]

    for ind in industries:
        if ind["code"] in BASE_1999_CODES and ind["firstYear"] != FIRST_YEAR:
            raise PanelError(f"{ind['name']}（{ind['code']}）基日为 {BASE_DATE}，firstYear 却是 {ind['firstYear']}：回填不完整")

    n_by_year: List[int] = []
    for j, y in enumerate(years):
        column = [ind["rets"][j] for ind in industries]
        ranks = compute.rank_desc(column)
        n = compute.count_not_none(column)
        if n < MIN_PER_YEAR:
            raise PanelError(f"{y} 年只有 {n} 个行业有数据（< {MIN_PER_YEAR}）")
        n_by_year.append(n)
        for ind, rk in zip(industries, ranks):
            ind["ranks"][j] = rk
    ytd_col = [ind["ytd"] for ind in industries]
    for ind, rk in zip(industries, compute.rank_desc(ytd_col)):
        ind["ytdRank"] = rk

    return {
        "baseDate": BASE_DATE,
        "years": years,
        "ytdYear": as_of_year,
        "ytdAsOf": as_of,
        "nByYear": n_by_year,
        "nYtd": compute.count_not_none(ytd_col),
        "industries": industries,
    }


def build() -> dict:
    lv = sws.get_level_history(sws.LEVEL1)
    names = dict(sws.get_index_list(sws.LEVEL1))
    missing = [c for c in names if c not in (lv.get("codes") or {})]
    if missing:
        raise PanelError(f"申万一级行业历史里缺少 {len(missing)} 个指数: {', '.join(missing)}")
    histories = [{"code": c, "name": n, "dates": lv["dates"], "close": lv["codes"][c]["close"]} for c, n in names.items()]
    as_of = lv["dates"][-1]
    data = compute_panel(histories, as_of)

    late: Dict[int, List[str]] = {}
    gapped: List[str] = []
    for ind in data["industries"]:
        if ind["firstYear"] != FIRST_YEAR:
            late.setdefault(ind["firstYear"], []).append(ind["name"])
        if ind["gapYears"]:
            gapped.append(f"{ind['name']}（{ind['code']}）{ind['gapYears'][0]}–{ind['gapYears'][-1]} 年")
            log.info("%s（%s）年度数据断档 %s（源数据如此），年化从 %s 年起算", ind["name"], ind["code"], ind["gapYears"], ind["cagrYears"]["from"])
    log.info("申万一级 %d 个行业，%d–%d 年 + %d 年初至今（截至 %s）；起点较晚: %s", len(histories), data["years"][0],
             data["years"][-1], data["ytdYear"], as_of,
             "；".join(f"{y} 年起 {len(v)} 个" for y, v in sorted(late.items())) or "无")

    late_text = "；".join(f"{y} 年起：" + "、".join(v) for y, v in sorted(late.items()))
    notes = [
        "行业按申万 2021 版分类，共 31 个一级行业；指数基日 1999-12-30 = 1000 点，基日收盘作为 2000 年的基准。"
        "接口返回的是价格指数，股息未计入，长期年化会低于含股息再投资的口径。",
        "年度涨跌幅 = 当年最后一个交易日收盘 ÷ 上一年最后一个交易日收盘 − 1；年初至今用最新交易日收盘与上年末比较。",
        "部分行业的指数起点较晚（2021 版沿用了 2014 版的编码），起点之前没有可用历史，对应年份留空而不是补数："
        + (late_text or "无") + "。",
        "年化只用连续的完整自然年：「全程」从各行业最近一段连续有数据的首个完整年到上一完整年（表头括号内为年数），"
        "10 / 5 / 3 年按最近的完整年数回推；完整年数不够或中间有断档的格子留空。各行业「全程」起点不同，不能横向比大小。",
    ]
    if gapped:
        notes.append("以下行业在申万接口里有整段缺失，对应年份留空、不用其它来源补，年化从缺失之后重新起算："
                     + "；".join(gapped) + "。")
    notes += [
        "颜色按 ±60% 截断，超过的格子只是颜色饱和，数字本身不受影响；排名 1 = 当年涨幅最高，并列共享名次，"
        "各年参与排名的行业数见表头。",
        "数据来自申万宏源研究的指数分析日报表，缓存在 cache/sws/ 并按日增量补齐；申万按 T+1 发布，最新一日通常是上一个交易日。",
    ]
    return {
        "asOf": as_of,
        "sources": ["申万宏源研究 swsresearch.com（申万指数分析·日报表：一级行业收盘）"],
        "notes": notes,
        "data": data,
    }
