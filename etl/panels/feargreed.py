# -*- coding: utf-8 -*-
"""面板 feargreed —— 恐贪指数（自建）：六个日度子指标按滚动分位打分后等权平均，0 = 极度恐慌，100 = 极度贪婪。

子指标（原始值 → 0–100 分；分 = 最近 ``WINDOW`` = 500 个交易日（含当日）窗口内 ≤ 当日值的有效样本占比，含等号；
窗口内有效样本 < ``MIN_OBS`` = 250 个时该日不计分；越高越贪婪）：
① vol 波动率：中证全指最近 20 个交易日对数收益的样本标准差 × √250 × 100（年化 %）→ 分 = 100 − 分位（波动大 = 恐慌）；
② turnover 相对换手：申万Ａ指 换手率（成交额 ÷ 流通市值 × 100）÷ 其 60 日均值（倍）→ 分位；
   流通市值在 2011-01-04 → 2012-04-16 为申万数据空档，这段原始值为 null，之后要等窗口内有效样本重新凑够 250 个
   子分才恢复（约 2013 年中）；
③ margin 两融：上交所融资余额相对 20 个交易日前的变化率（%）→ 分位；沪市口径，序列自 2010-03-31 起，之前为 null；
   深交所只提供按日查询的单日报表，没有区间接口，未纳入；
④ breadth 市场宽度：收盘站上自身 MA50 的股票占比（%，来自全市场日线库聚合 ``above50 / n50``，n50 < 300 时 null）→ 分位；
⑤ rsi：中证全指 RSI(14)（Wilder 平滑）→ 分位；
⑥ strength 涨跌强度：最近 5 个交易日 上涨家数 ÷ (上涨 + 下跌家数) × 100 的均值（%）→ 分位。
综合分 = 当日有分的子指标的等权平均（用已四舍五入到 1 位小数的子分算，存 2 位小数），有分的子指标不足 ``MIN_SUBS`` = 4 个
时为 null；序列从首个有综合分的日期起（约 2006 年初）。区间左闭右开 ``[lo, hi)``、最后一档右端闭合：
0–25 极度恐慌 / 25–45 恐慌 / 45–55 中性 / 55–75 贪婪 / 75–100 极度贪婪。

主日期轴 = 中证全指（csindex 000985）交易日（2005-01-04 起）；其它序列按日期对齐，缺日为 null。
分位、RSI、波动率等纯函数在 ``etl.compute``；全市场日线库的取数流程与「库落后 / 开发模式」提示与 trend 面板共用
（``trend.load_market`` / ``trend.market_notes``，``update_incremental`` 每进程只跑一次）。

自检：所有子分 ∈ [0, 100]；每个有综合分的日子 |未四舍五入的综合分 − 已存子分的均值| ≤ 1e-6 且有分子指标 ≥ 4；
日期严格递增；latest.date == 末日；起点 ≤ 2007-12-31（开发模式放宽）；紧凑 JSON 正文 ≤ ``SIZE_BUDGET`` 字节。

数据形状：``{dates[], composite[], subs:{key:{name, unit, raw[], score[], definition}}, overlay:{csiAll[]}, bands[],
latest:{date, composite, band, subs:[{key, name, unit, raw, score, definition}]}, window, minObs, minSubs, start, nDays}``。
``compute_panel()`` 是纯函数；``build()`` 负责取数。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from etl import compute, market_state
from etl.common import PanelError, env_flag
from etl.panels import trend
from etl.sources import csindex_hist, sse_margin, sws

log = logging.getLogger("etl.feargreed")

PANEL_ID = "feargreed"
TITLE = "恐贪指数"
COMPACT_JSON = True

WINDOW = 500
MIN_OBS = 250
MIN_SUBS = 4
VOL_N = 20
TURN_N = 60
MARGIN_N = 20
RSI_N = 14
STRENGTH_N = 5
MIN_N50 = 300
SIZE_BUDGET = 1_000_000
START_BY = "2007-12-31"
CSI_ALL = trend.CSI_ALL
BANDS: List[Dict[str, Any]] = [
    {"lo": 0, "hi": 25, "label": "极度恐慌"},
    {"lo": 25, "hi": 45, "label": "恐慌"},
    {"lo": 45, "hi": 55, "label": "中性"},
    {"lo": 55, "hi": 75, "label": "贪婪"},
    {"lo": 75, "hi": 100, "label": "极度贪婪"},
]
SUBS = (("vol", "波动率", "%"), ("turnover", "相对换手", "倍"), ("margin", "两融", "%"), ("breadth", "市场宽度", "%"),
        ("rsi", "RSI", ""), ("strength", "涨跌强度", "%"))
INVERTED = frozenset({"vol"})            # 分 = 100 − 分位
RAW_DP = {"vol": 1, "turnover": 2, "margin": 1, "breadth": 1, "rsi": 1, "strength": 1}
DEFINITIONS = {
    "vol": f"中证全指最近 {VOL_N} 个交易日对数收益的样本标准差 × √250（年化波动率，%）；波动越大越恐慌，分数取 100 − 分位",
    "turnover": f"申万Ａ指 当日换手率（成交额 ÷ 流通市值）÷ 其最近 {TURN_N} 个交易日均值；大于 1 表示交投比近两三个月活跃",
    "margin": f"上交所融资余额相对 {MARGIN_N} 个交易日前的变化率（%）；加杠杆越快越贪婪",
    "breadth": "收盘站上自身 50 日均线的股票占比（%）；分母为当日有成交且已有 ≥ 50 个交易日记录的股票",
    "rsi": f"中证全指 {RSI_N} 日相对强弱指标（Wilder 平滑，0–100）",
    "strength": f"最近 {STRENGTH_N} 个交易日 上涨家数 ÷ (上涨 + 下跌家数) 的平均（%）；平盘与上市首日不计",
}


def _r(x: Optional[float], nd: int) -> Optional[float]:
    return None if x is None else round(x, nd)


def _align(src_dates: Sequence[str], src_values: Sequence, dates: Sequence[str]) -> List[Optional[float]]:
    """按日期把一条序列对齐到主轴；缺日为 None。"""
    by = dict(zip(src_dates, src_values))
    return [by.get(d) for d in dates]


# --------------------------------------------------------------------------- 原始序列（纯函数）
def _turnover_ratio(a_index: Dict[str, Any]) -> List[Optional[float]]:
    turn: List[Optional[float]] = []
    for amt, mv in zip(a_index["amount"], a_index["mv"]):
        turn.append(amt / mv * 100.0 if (amt is not None and mv is not None and amt > 0 and mv > 0) else None)
    return compute.ratio_to_rolling_mean(turn, TURN_N)


def _breadth(agg: Dict[str, list]) -> List[Optional[float]]:
    return [a / n * 100.0 if n >= MIN_N50 else None for a, n in zip(agg["above50"], agg["n50"])]


def _strength(agg: Dict[str, list]) -> List[Optional[float]]:
    ratio = [a / (a + d) * 100.0 if (a + d) > 0 else None for a, d in zip(agg["adv"], agg["dec"])]
    return compute.rolling_mean(ratio, STRENGTH_N)


def raw_series(csi: Dict[str, Any], a_index: Dict[str, Any], margin: Dict[str, Any], agg: Dict[str, list]
               ) -> Dict[str, List[Optional[float]]]:
    """六个子指标的原始值，全部对齐到 ``csi.dates``。"""
    dates = csi["dates"]
    return {
        "vol": compute.realized_vol(csi["close"], VOL_N, 250),
        "turnover": _align(a_index["dates"], _turnover_ratio(a_index), dates),
        "margin": _align(margin["dates"], compute.pct_change_n(margin["rzye"], MARGIN_N), dates),
        "breadth": _align(agg["dates"], _breadth(agg), dates),
        "rsi": compute.rsi(csi["close"], RSI_N),
        "strength": _align(agg["dates"], _strength(agg), dates),
    }


def score_series(key: str, raw: Sequence[Optional[float]]) -> List[Optional[float]]:
    """原始值 → 1 位小数的分数（滚动含等号分位；vol 取反）。"""
    pct = compute.rolling_percentile(raw, WINDOW, MIN_OBS)
    if key in INVERTED:
        pct = [None if p is None else 100.0 - p for p in pct]
    return [_r(p, 1) for p in pct]


def _self_check(data: Dict[str, Any], unrounded: Sequence[Optional[float]], allow_partial: bool) -> None:
    dates = data["dates"]
    compute.assert_ascending(dates, "恐贪指数日期")
    keys = [k for k, _n, _u in SUBS]
    for k in keys:
        for v in data["subs"][k]["score"]:
            if v is not None and not (0.0 <= v <= 100.0):
                raise PanelError(f"子指标 {k} 分数 {v} 超出 [0, 100]")
    for i, c in enumerate(data["composite"]):
        avail = [data["subs"][k]["score"][i] for k in keys if data["subs"][k]["score"][i] is not None]
        if c is None:
            if len(avail) >= MIN_SUBS:
                raise PanelError(f"{dates[i]} 有 {len(avail)} 个子分却没有综合分")
            continue
        if len(avail) < MIN_SUBS:
            raise PanelError(f"{dates[i]} 只有 {len(avail)} 个子分（< {MIN_SUBS}）却有综合分")
        if not (0.0 <= c <= 100.0):
            raise PanelError(f"{dates[i]} 综合分 {c} 超出 [0, 100]")
        if abs(unrounded[i] - sum(avail) / len(avail)) > 1e-6:
            raise PanelError(f"{dates[i]} 综合分 {unrounded[i]} 与子分均值 {sum(avail) / len(avail)} 不一致")
    if data["latest"]["date"] != dates[-1]:
        raise PanelError("latest.date 与末日不一致")
    if not allow_partial and data["start"] > START_BY:
        raise PanelError(f"恐贪指数起点 {data['start']} 晚于 {START_BY}：某个子指标的历史不完整")
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > SIZE_BUDGET:
        raise PanelError(f"恐贪指数 data 紧凑 JSON {size:,} 字节超过 {SIZE_BUDGET:,} 字节预算")


def compute_panel(csi: Dict[str, Any], a_index: Dict[str, Any], margin: Dict[str, Any], agg: Dict[str, list],
                  allow_partial: bool = False) -> Dict[str, Any]:
    """csi = csindex_hist.get_index_history("000985")；a_index = sws.get_a_index()；margin = sse_margin.get_margin_history()；
    agg = 全市场日线库聚合 → data。"""
    dates = list(csi.get("dates") or [])
    if len(dates) <= WINDOW:
        raise PanelError(f"中证全指只有 {len(dates)} 个交易日，不足滚动窗口 {WINDOW}")
    compute.assert_ascending(dates, "中证全指日期")
    raw = raw_series(csi, a_index, margin, agg)
    scores = {k: score_series(k, raw[k]) for k, _n, _u in SUBS}
    keys = [k for k, _n, _u in SUBS]
    unrounded: List[Optional[float]] = []
    composite: List[Optional[float]] = []
    for i in range(len(dates)):
        m, _cnt = compute.mean_of_available([scores[k][i] for k in keys], MIN_SUBS)
        unrounded.append(m)
        composite.append(_r(m, 2))
    start_i = next((i for i, c in enumerate(composite) if c is not None), None)
    if start_i is None:
        raise PanelError(f"没有任何一天凑齐 {MIN_SUBS} 个子指标分数，无法计算综合分")
    sl = slice(start_i, None)
    dates = dates[sl]
    composite = composite[sl]
    unrounded = unrounded[sl]
    subs: Dict[str, Dict[str, Any]] = {}
    for k, name, unit in SUBS:
        subs[k] = {"name": name, "unit": unit, "raw": [_r(v, RAW_DP[k]) for v in raw[k][sl]],
                   "score": scores[k][sl], "definition": DEFINITIONS[k]}
    latest = {
        "date": dates[-1],
        "composite": composite[-1],
        "band": compute.band_label(composite[-1], BANDS),
        "subs": [{"key": k, "name": name, "unit": unit, "raw": subs[k]["raw"][-1], "score": subs[k]["score"][-1],
                  "definition": DEFINITIONS[k]} for k, name, unit in SUBS],
    }
    data = {
        "dates": dates,
        "composite": composite,
        "subs": subs,
        "overlay": {"csiAll": [_r(float(v), 2) for v in csi["close"][sl]]},
        "bands": [dict(b) for b in BANDS],
        "latest": latest,
        "window": WINDOW,
        "minObs": MIN_OBS,
        "minSubs": MIN_SUBS,
        "start": dates[0],
        "nDays": len(dates),
    }
    _self_check(data, unrounded, allow_partial)
    return data


# --------------------------------------------------------------------------- I/O
def build() -> dict:
    store = market_state.MarketStore()
    update, agg, status = trend.load_market(store)
    csi = csindex_hist.get_index_history(CSI_ALL)
    a = sws.get_a_index()
    m = sse_margin.get_margin_history()
    allow_partial = env_flag(market_state.ENV_ALLOW_PARTIAL)
    data = compute_panel(csi, a, m, agg, allow_partial)
    lt = data["latest"]
    n_scores = {s["key"]: sum(1 for v in data["subs"][s["key"]]["score"] if v is not None) for s in lt["subs"]}
    log.info("恐贪指数: %s 日 %s → %s，最新 %s（%s）；子分 %s；有分天数 %s",
             format(data["nDays"], ","), data["start"], lt["date"], lt["composite"], lt["band"],
             "，".join(f"{s['name']} {s['score']}" for s in lt["subs"]),
             "，".join(f"{k} {format(v, ',')}" for k, v in n_scores.items()))
    bands_text = "、".join(f"{b['lo']}–{b['hi']} {b['label']}" for b in BANDS)
    notes = [
        f"综合分由六个日度子指标等权平均而来：每个子指标先在最近 {WINDOW} 个交易日（含当日）的窗口内算「≤ 当日值的样本占比」"
        f"得到 0–100 的分位分（含等号；窗口内有效样本不足 {MIN_OBS} 个时当日不计分），越高越偏贪婪，波动率方向相反取 100 − 分位。"
        f"当日至少 {MIN_SUBS} 个子指标有分才计算综合分，综合分保留 2 位小数、子分保留 1 位；序列从首个有综合分的日期起。",
        "子指标：" + "；".join(f"{name}——{DEFINITIONS[k]}" for k, name, _u in SUBS) + "。",
        "两融子指标用上海证券交易所公布的沪市融资余额（2010-03-31 起，之前该项为空），深交所只提供按日查询的单日报表、没有区间接口，"
        "因此未纳入深市；两融余额按 T+1 公布，最新一日通常比其它指标晚一个交易日。",
        "相对换手所用的流通市值在 2011-01-04 至 2012-04-16 为申万数据空档，这段子分为空，之后要等窗口内有效样本重新凑够 "
        f"{MIN_OBS} 个才恢复；期间综合分由其余子指标计算。",
        f"区间划分（左闭右开，100 归入最后一档）：{bands_text}。区间只是对分位高低的描述性标签。",
        "各数据源的发布时间：中证全指与申万Ａ指 当日 18:30 前后，Baostock 全市场日线 17:30–18:00 左右，上交所两融 次日上午；"
        "20:30 的自动更新纳入当日能拿到的数据，07:30 的再次运行补齐两融。",
    ] + trend.market_notes(store, update, status)
    return {
        "asOf": lt["date"],
        "sources": [
            "中证指数官网 csindex.com.cn（中证全指 000985 日收盘：波动率、RSI、叠加线）",
            "申万宏源研究 swsresearch.com（申万Ａ指 成交额与流通市值：换手率）",
            "上海证券交易所 sse.com.cn（融资融券汇总：沪市融资余额）",
            "Baostock baostock.com（沪深A股 全市场日线：MA50 站上率、涨跌家数）",
        ],
        "notes": notes,
        "data": data,
    }
