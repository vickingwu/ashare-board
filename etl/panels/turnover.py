# -*- coding: utf-8 -*-
"""面板 turnover —— 全市场成交金额与换手率。

成交额 = 申万Ａ指 日线 ``bargainsum``（亿元）；流通市值 = 申万Ａ指 指数分析日报表 ``negotiablessharesum1``（亿元，申万口径）；
换手率 = 当日成交额 ÷ 当日流通市值 × 100（%）。申万Ａ指 = 沪深两市全部 A 股，不含北交所与 B 股。

口径与坑：
* 流通市值在 2011-01-04 → 2012-04-16 有一段申万自身的空档（307 个交易日为 null，其中零星 3 天有值），
  空值日的换手率为 ``null``，**不填补**；有值的零星日子照常计算。``gaps`` 把这类空档列成
  ``[{from, to, n, nPresent}]``（n = 空值日数，nPresent = 段内有值日数；见 ``_null_runs``）；
* 申万自己的 ``turnoverrate`` 按股本口径计算，与成交额 ÷ 流通市值不是一回事，只作参考列（``swTurnover``）；
* 20 日均线只对成交额计算（``ma20``），前 19 日为 ``null``；
* 自检：日期严格递增、成交额全部 > 0、换手率在 ``[T_MIN, T_MAX]`` 之内、换手率与 成交额/流通市值 恒等、
  行数 ≥ ``MIN_ROWS``、最新一日流通市值非空。

数据形状：``{dates[], amount[], mv[], turnover[], ma20[], swTurnover[], mvKind, mvLabel, scope, gaps[], summary}``，
五个数组与 ``dates`` 等长；``summary = {latest:{date, amount, ma20, mv, turnover, swTurnover}, pctAll}``。
``compute_panel()`` 是纯函数；``build()`` 负责抓数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from etl import compute
from etl.common import PanelError
from etl.sources import sws

log = logging.getLogger("etl.turnover")

PANEL_ID = "turnover"
TITLE = "全市场成交金额与换手率"

MA_N = 20
T_MIN, T_MAX = 0.05, 15.0
MIN_ROWS = 5000
GAP_MIN_RUN = 5          # 空值行数超过它才算一段空档
GAP_MERGE_WITHIN = 5     # 两段空档之间夹着 ≤ 5 个有值日时并成一段
MV_KIND = "float"
MV_LABEL = "申万Ａ指成分流通市值（申万口径）"
SCOPE = "沪深两市全部 A 股（申万Ａ指成分），不含北交所与 B 股"


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _null_runs(values: List[Optional[float]], dates: List[str], min_run: int, merge_within: int) -> List[Dict[str, Any]]:
    """None 空档 → [{from, to, n, nPresent}]。

    先找出连续 None 的区段；相邻两段之间夹着的有值行数 ≤ ``merge_within`` 时并成一段（申万 2011–2012 的空档里
    零星有 3 天有值，仍算同一段空档；这些日子的换手率照常计算，不丢）；最后只保留空值行数 > ``min_run`` 的段。
    ``n`` = 段内空值行数，``nPresent`` = 段内有值行数。
    """
    runs: List[List[int]] = []                       # [start, end] 下标闭区间
    start: Optional[int] = None
    for i in range(len(values) + 1):
        is_none = i < len(values) and values[i] is None
        if is_none and start is None:
            start = i
        elif not is_none and start is not None:
            runs.append([start, i - 1])
            start = None
    merged: List[List[int]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] - 1 <= merge_within:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    out: List[Dict[str, Any]] = []
    for a, b in merged:
        n_null = sum(1 for v in values[a:b + 1] if v is None)
        if n_null > min_run:
            out.append({"from": dates[a], "to": dates[b], "n": n_null, "nPresent": (b - a + 1) - n_null})
    return out


def compute_panel(a: Dict[str, Any]) -> Dict[str, Any]:
    """a = sws.get_a_index() 的返回 {dates[], amount[], mv[], turnoverSw[], ...} → data。"""
    rows = [(d, amt, mv, tsw) for d, amt, mv, tsw in zip(a["dates"], a["amount"], a["mv"], a["turnoverSw"])
            if amt is not None and amt > 0]
    if len(rows) < MIN_ROWS:
        raise PanelError(f"申万Ａ指 成交额只有 {len(rows)} 行（< {MIN_ROWS}），疑似数据不完整")
    dates = [r[0] for r in rows]
    compute.assert_ascending(dates, "申万Ａ指 日期")
    amount_raw = [r[1] for r in rows]
    mv_raw = [r[2] if (r[2] is not None and r[2] > 0) else None for r in rows]
    tsw_raw = [r[3] for r in rows]

    turnover: List[Optional[float]] = []
    for d, amt, mv in zip(dates, amount_raw, mv_raw):
        if mv is None:
            turnover.append(None)
            continue
        exact = amt / mv * 100.0
        if not (T_MIN <= exact <= T_MAX):
            raise PanelError(f"{d} 换手率 {exact:.3f}% 超出 [{T_MIN}, {T_MAX}]：成交额 {amt} / 流通市值 {mv}")
        stored = compute.safe_div_pct(amt, mv)
        if stored is None or abs(stored - exact) > 1e-4:
            raise PanelError(f"{d} 换手率与 成交额 ÷ 流通市值 不一致")
        turnover.append(stored)
    if mv_raw[-1] is None or turnover[-1] is None:
        raise PanelError(f"最新一日 {dates[-1]} 没有流通市值，换手率无法计算")

    ma20 = [_r(v) for v in compute.rolling_mean(amount_raw, MA_N)]
    gaps = _null_runs(mv_raw, dates, GAP_MIN_RUN, GAP_MERGE_WITHIN)
    present = [t for t in turnover if t is not None]
    latest = {
        "date": dates[-1],
        "amount": _r(amount_raw[-1]),
        "ma20": ma20[-1],
        "mv": _r(mv_raw[-1]),
        "turnover": turnover[-1],
        "swTurnover": _r(tsw_raw[-1]),
    }
    summary = {
        "latest": latest,
        "pctAll": round(compute.percentile_rank(present, turnover[-1]), 1),
        "nDays": len(dates),
        "nTurnover": len(present),
        "start": dates[0],
    }
    return {
        "dates": dates,
        "amount": [_r(v) for v in amount_raw],
        "mv": [_r(v) for v in mv_raw],
        "turnover": turnover,
        "ma20": ma20,
        "swTurnover": [_r(v) for v in tsw_raw],
        "mvKind": MV_KIND,
        "mvLabel": MV_LABEL,
        "scope": SCOPE,
        "gaps": gaps,
        "summary": summary,
    }


def build() -> dict:
    a = sws.get_a_index()
    data = compute_panel(a)
    s = data["summary"]
    lt = s["latest"]
    log.info("成交额/换手率: %s 日 %s → %s，最新 成交额 %.2f 亿元 流通市值 %.2f 亿元 换手率 %.4f%%（分位 %.1f%%）申万换手率 %s，流通市值空档 %s",
             format(s["nDays"], ","), data["dates"][0], lt["date"], lt["amount"], lt["mv"], lt["turnover"], s["pctAll"],
             lt["swTurnover"], "；".join(f"{g['from']}→{g['to']}（{g['n']} 日）" for g in data["gaps"]) or "无")
    gap_text = "、".join(f"{g['from']} 至 {g['to']}（{g['n']} 个交易日" + (f"，期间仅 {g['nPresent']} 天有值" if g["nPresent"] else "") + "）"
                        for g in data["gaps"])
    notes = [
        "成交额为申万Ａ指成分股的日成交金额（亿元），即沪深两市全部 A 股，不含北交所与 B 股；"
        "与新闻里「两市成交额」的口径相差约 0.3%（主要是 B 股）。",
        "换手率 = 当日成交额 ÷ 当日流通市值 × 100%。流通市值用申万口径的 A 股流通市值，不是总市值；"
        "按总市值算的换手率会系统性低一半以上，不能与这里的数字直接比较。",
        "申万自己发布的换手率按股本口径计算，与成交额 ÷ 流通市值不是同一定义，图中只作参考列（swTurnover）。",
        ("流通市值在 " + gap_text + " 缺失（申万数据空档），这段换手率留空，不用邻近值填补；成交额不受影响。")
        if gap_text else "流通市值序列没有超过 5 个交易日的连续缺失。",
        "成交额的 20 日均线为含当日的简单平均，前 19 个交易日为空；所选时间窗内的均值、±1σ 与当前分位在浏览器内按窗口重新计算。",
        "数据来自申万宏源研究的申万Ａ指 日线与指数分析日报表，缓存在 cache/sws/ 并按日增量补齐；"
        "申万按 T+1 发布，16:00 之前运行时当日数据不纳入。",
    ]
    return {
        "asOf": lt["date"],
        "sources": ["申万宏源研究 swsresearch.com（申万Ａ指 日线成交额；指数分析·日报表 流通市值、换手率）"],
        "notes": notes,
        "data": data,
    }
