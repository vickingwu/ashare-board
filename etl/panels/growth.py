# -*- coding: utf-8 -*-
"""面板 growth —— 不同事物的增长：M2、名义 GDP、A 股流通市值、中证全指全收益、红利低波全收益、70 城与一线二手住宅、黄金，
放在同一条月度轴（``AXIS_START`` = 2006-01 → 中证全指全收益最近一个完整月）上比较长期增长。

序列口径（``SERIES_META`` 顺序）：
* ``m2`` M2 期末值（亿元，月度，2008-01 起）与 ``gdp`` 名义 GDP（亿元）来自东方财富数据中心转载的统计局数据
  （``etl.sources.eastmoney_macro``；统计局 easyquery 对云端 IP 返回 403，这是记录在案的替代来源）。GDP 接口给的是**累计值**：
  当季值 = 本季累计 − 上季累计（一季度 = 累计值，``compute.cumulative_to_period``），再做四季度滚动求和
  （``compute.rolling_sum(…, 4)``），点位只放在季末月（03/06/09/12），其它月份为 null，首个点 2006-12；
* ``mv`` A 股流通市值（亿元）= 申万Ａ指成分流通市值（``sws.get_a_index().mv``）在每月最后一个交易日当天的值；申万自身在
  2011-01-04 → 2012-04-16 没有数据，对应的 2011-01 → 2012-03 月末为 null，不用月内更早的值代替、也不填补；
* ``csiAllTR`` / ``divLowVolTR`` 中证全指全收益 H00985、红利低波全收益 H20269 的月末收盘（``month_end_sample(complete_only=True)``，
  未结束的月份不进入）；
* ``house70`` / ``houseTier1`` 70 城二手住宅价格：接口只有环比（上月 = 100），按城市做链式定基（``HOUSE_BASE_MONTH`` 2010-12 = 100，
  ``compute.chain_link``），再对 70 城 / 北京·上海·广州·深圳 取等权算术平均（``equal_weight_mean``，每月至少 ``MIN_CITIES`` 个城市）。
  环比序列里有 null 的城市整条剔除并列入 ``houseDropped``；剩余城市 < ``MIN_CITIES`` 或缺一线城市 → PanelError；
* ``gold`` 黄金人民币价（元/克，**月均价**）分两段：``GOLD_SPLIT``（2016-12）起为上海黄金交易所 Au99.99 日收盘的月均
  （``sge_gold.monthly_averages(complete_only=True)``）；之前为 ``state/gold_seed.json`` 的 ``cnyPerGram``
  （世界银行月均美元金价 × 中间价月均 ÷ 31.1035 × 拼接系数 k，见 ``gold_history``）。种子最后一个月必须恰好是 GOLD_SPLIT
  的前一个月，拼接后首末月之间不得有 null。

输出 ``data = {months[], monthEnds[], start, end, baseMonth, series[], summary[], dropped[], houseDropped[]}``：
``series[] = {key, name, unit, kind ("level"|"price"|"index"), cadence ("monthly"|"quarterly"|"monthEnd"|"monthlyAvg"), source,
start, end, values[]}``（values 与 months 等长，原始水平值，前端自行归一）；``summary[] = {key, name, start, end, first, last,
multiple, years, cagrAll, cagr10y, note}``；``baseMonth`` = 八条序列同时有值的第一个月（信息项；GDP 只在季末月有点、流通市值
2012-03 之前有空档，所以实际为 2012-06）；``dropped`` = 未纳入的序列及原因。
自检：月份连续；每条序列 ≥ ``MIN_POINTS`` 个非空值且全部 > 0；summary 与 series 的 key 一致。
``compute_panel()`` 是纯函数；``build()`` 负责取数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import PanelError
from etl.sources import csindex_hist, eastmoney_macro, gold_history, sge_gold, sws

log = logging.getLogger("etl.growth")

PANEL_ID = "growth"
TITLE = "不同事物的增长"
COMPACT_JSON = False

AXIS_START = "2006-01"
CSI_ALL_TR = "H00985"
DIV_LOWVOL_TR = "H20269"
MIN_POINTS = 24
HOUSE_BASE_MONTH = "2010-12"
GOLD_SPLIT = "2016-12"
MIN_CITIES = 68
TIER1 = eastmoney_macro.TIER1
EASTMONEY_LABEL = "东方财富数据中心（统计局数据转载）"

# (key, name, unit, kind, cadence, source label)
SERIES_META: List[Tuple[str, str, str, str, str, str]] = [
    ("m2", "M2", "亿元", "level", "monthly", EASTMONEY_LABEL),
    ("gdp", "名义 GDP·四季度滚动", "亿元", "level", "quarterly", EASTMONEY_LABEL),
    ("mv", "A 股流通市值", "亿元", "level", "monthEnd", "申万宏源研究（申万Ａ指成分口径）"),
    ("csiAllTR", "中证全指全收益", "点", "price", "monthEnd", "中证指数 H00985"),
    ("divLowVolTR", "红利低波全收益", "点", "price", "monthEnd", "中证指数 H20269"),
    ("house70", "70 城二手住宅·等权", "2010-12 = 100", "index", "monthly", EASTMONEY_LABEL),
    ("houseTier1", "一线 4 城二手住宅·等权", "2010-12 = 100", "index", "monthly", EASTMONEY_LABEL),
    ("gold", "黄金人民币价", "元/克", "price", "monthlyAvg", "上海黄金交易所 Au99.99；2016-12 前为世界银行月均 × 中间价"),
]
DROPPED = [
    {"key": "deposits", "name": "人民币存款余额", "reason": "统计局接口不可达，无替代来源；与 M2 高度重合"},
    {"key": "income", "name": "城镇居民人均可支配收入", "reason": "统计局接口不可达，无替代来源"},
]
SERIES_NOTE = {
    "gdp": "季度点（季末月），当季值四季度滚动求和",
    "mv": "2011-01 → 2012-03 无数据；空档前后申万口径不同，不宜跨空档比较",
    "house70": "2010-12 = 100，城市环比链式定基后等权平均",
    "houseTier1": "2010-12 = 100，北京·上海·广州·深圳等权平均",
    "gold": "月均价；2016-12 前为世界银行月均 × 中间价 × k",
}


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _house_series(house: Dict[str, Any]) -> Tuple[List[Optional[float]], List[Optional[float]], List[str]]:
    """70 城环比 → (全部等权定基[], 一线等权定基[], 剔除的城市[])，与 ``house["months"]`` 等长。"""
    months = house["months"]
    if not months:
        raise PanelError("70 城数据没有任何月份")
    if months != compute.months_between(months[0], months[-1]):
        raise PanelError(f"70 城月份不连续: {months[0]} → {months[-1]} 共 {len(months)} 个")
    chained: Dict[str, List[float]] = {}
    dropped: List[str] = []
    for city in house["cities"]:
        seq = house["seq"][city]
        if len(seq) != len(months) or any(v is None or v <= 0 for v in seq):
            dropped.append(city)
            continue
        chained[city] = compute.chain_link(seq, 100.0)
    if len(chained) < MIN_CITIES:
        raise PanelError(f"70 城里环比完整的城市只有 {len(chained)} 个（应 ≥ {MIN_CITIES}），剔除 {dropped[:6]}")
    missing_tier1 = [c for c in TIER1 if c not in chained]
    if missing_tier1:
        raise PanelError(f"70 城缺少一线城市的完整环比: {missing_tier1}")
    all_idx = compute.equal_weight_mean([chained[c] for c in sorted(chained)], MIN_CITIES)
    tier1_idx = compute.equal_weight_mean([chained[c] for c in TIER1], len(TIER1))
    return [_r(v) for v in all_idx], [_r(v) for v in tier1_idx], dropped


def _gold_series(seed: Dict[str, Any], sge: Dict[str, Any], today: Optional[str]) -> Tuple[List[str], List[float]]:
    """种子月均（< GOLD_SPLIT）+ 上金所月均（≥ GOLD_SPLIT）→ (months[], values[])。"""
    seed_months = list(seed["months"])
    if not seed_months or seed_months[-1] != _prev_month(GOLD_SPLIT):
        raise PanelError(f"黄金种子最后一个月 {seed_months[-1] if seed_months else None} 应为 {_prev_month(GOLD_SPLIT)}（GOLD_SPLIT 的前一月）")
    months: List[str] = []
    values: List[float] = []
    for m, v in zip(seed_months, seed["cnyPerGram"]):
        if m < GOLD_SPLIT:
            months.append(m)
            values.append(_r(v))
    sge_months, sge_avg, _n = sge_gold.monthly_averages(sge, complete_only=True, today=today)
    for m, v in zip(sge_months, sge_avg):
        if m >= GOLD_SPLIT:
            months.append(m)
            values.append(_r(v))
    if not months or months[-1] < GOLD_SPLIT:
        raise PanelError(f"上金所没有 {GOLD_SPLIT} 起的月均价")
    compute.assert_ascending(months, "黄金月份")
    return months, values


def _first_last(months: List[str], values: List[Optional[float]]) -> Tuple[Optional[int], Optional[int]]:
    idx = [i for i, v in enumerate(values) if v is not None]
    return (idx[0], idx[-1]) if idx else (None, None)


def compute_panel(m2: Dict[str, Any], gdp: Dict[str, Any], a_index: Dict[str, Any], csi_tr: Dict[str, Any],
                  dlv_tr: Dict[str, Any], house: Dict[str, Any], seed: Dict[str, Any], sge: Dict[str, Any],
                  today: Optional[str] = None) -> Dict[str, Any]:
    """八个来源 → data（见模块说明）。``today`` 只用于判断上金所当前月是否完整（默认今天）。"""
    csi_d, csi_v = compute.month_end_sample(csi_tr["dates"], csi_tr["close"], complete_only=True)
    if not csi_d:
        raise PanelError("中证全指全收益没有完整月份")
    end = csi_d[-1][:7]
    if end < AXIS_START:
        raise PanelError(f"中证全指全收益最后完整月 {end} 早于轴起点 {AXIS_START}")
    months = compute.months_between(AXIS_START, end)
    month_ends = [compute.month_end_date(m) for m in months]

    values: Dict[str, List[Optional[float]]] = {}
    values["m2"] = [_r(v) for v in compute.align_months(months, m2["months"], m2["m2"])]
    q = compute.cumulative_to_period(gdp["quarterEnds"], gdp["cum"])
    rolling4 = compute.rolling_sum(q, 4)
    values["gdp"] = [_r(v) for v in compute.align_months(months, gdp["quarterEnds"], rolling4)]
    # 流通市值取每月最后一个交易日当天的值：该日申万没有流通市值就为 null（不用月内更早的值代替）
    mv_lookup = dict(zip(a_index["dates"], a_index["mv"]))
    mv_d, _ = compute.month_end_sample(a_index["dates"], a_index["dates"])
    values["mv"] = [_r(v) for v in compute.align_months(months, mv_d, [mv_lookup[d] for d in mv_d])]
    values["csiAllTR"] = [_r(v) for v in compute.align_months(months, csi_d, csi_v)]
    dlv_d, dlv_v = compute.month_end_sample(dlv_tr["dates"], dlv_tr["close"], complete_only=True)
    values["divLowVolTR"] = [_r(v) for v in compute.align_months(months, dlv_d, dlv_v)]
    house_all, house_t1, house_dropped = _house_series(house)
    values["house70"] = compute.align_months(months, house["months"], house_all)
    values["houseTier1"] = compute.align_months(months, house["months"], house_t1)
    gold_m, gold_v = _gold_series(seed, sge, today)
    values["gold"] = compute.align_months(months, gold_m, gold_v)

    series_out: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []
    for key, name, unit, kind, cadence, source in SERIES_META:
        vals = values[key]
        i0, i1 = _first_last(months, vals)
        present = [v for v in vals if v is not None]
        if i0 is None or len(present) < MIN_POINTS:
            raise PanelError(f"{name} 只有 {len(present)} 个月有值（应 ≥ {MIN_POINTS}）")
        bad = [months[i] for i, v in enumerate(vals) if v is not None and v <= 0]
        if bad:
            raise PanelError(f"{name} 有 {len(bad)} 个非正值，首个 {bad[0]}")
        if key == "gold" and any(v is None for v in vals[i0:i1 + 1]):
            raise PanelError(f"黄金月均价在 {months[i0]} → {months[i1]} 之间有缺月（两段拼接不连续）")
        start_m, end_m = months[i0], months[i1]
        first_v, last_v = vals[i0], vals[i1]
        pt_dates = [month_ends[i] for i, v in enumerate(vals) if v is not None]
        years = compute.years_between(month_ends[i0], month_ends[i1])
        series_out.append({"key": key, "name": name, "unit": unit, "kind": kind, "cadence": cadence, "source": source,
                           "start": start_m, "end": end_m, "values": vals})
        summary.append({
            "key": key, "name": name, "start": start_m, "end": end_m,
            "first": first_v, "last": last_v,
            "multiple": round(last_v / first_v, 4),
            "years": round(years, 2),
            "cagrAll": _r(compute.cagr(first_v, last_v, years)),
            "cagr10y": _r(compute.window_cagr(pt_dates, present, 10)),
            "note": SERIES_NOTE.get(key, "月末值" if cadence == "monthEnd" else ""),
        })
    if [s["key"] for s in summary] != [s["key"] for s in series_out]:
        raise PanelError("summary 与 series 的 key 不一致")
    base_month = next((m for i, m in enumerate(months) if all(values[k][i] is not None for k, *_ in SERIES_META)), None)
    return {
        "months": months,
        "monthEnds": month_ends,
        "start": months[0],
        "end": end,
        "baseMonth": base_month,
        "series": series_out,
        "summary": summary,
        "dropped": [dict(d) for d in DROPPED],
        "houseDropped": house_dropped,
    }


def build() -> dict:
    m2 = eastmoney_macro.get_m2()
    gdp = eastmoney_macro.get_gdp()
    house = eastmoney_macro.get_house_prices()
    a = sws.get_a_index()
    csi = csindex_hist.get_index_history(CSI_ALL_TR)
    dlv = csindex_hist.get_index_history(DIV_LOWVOL_TR)
    seed = gold_history.load_seed()
    sge = sge_gold.get_au9999()
    data = compute_panel(m2, gdp, a, csi, dlv, house, seed, sge)
    as_of = compute.month_end_date(data["end"])
    by_key = {s["key"]: s for s in data["summary"]}
    log.info("不同事物的增长: %s → %s（%d 个月），baseMonth %s；%s", data["start"], data["end"], len(data["months"]),
             data["baseMonth"],
             "；".join(f"{s['name']} {s['start']}→{s['end']} ×{s['multiple']:.2f} 年化 {s['cagrAll']}%" for s in data["summary"]))
    k = seed.get("k")
    overlap = seed.get("overlap") or ["", ""]
    notes = [
        "各序列口径：M2 为月末余额（亿元，2008-01 起）；名义 GDP 为当季值的四季度滚动求和（亿元），当季值由接口的累计值差分得到，"
        "点位放在季末月（3/6/9/12 月），其余月份为空，首个点 2006-12；A 股流通市值为申万Ａ指成分口径（亿元）在每月最后一个交易日的值，"
        "2011-01 → 2012-03 为申万数据空档，且空档前后申万的流通市值口径不同（2010-12 约 18.9 万亿，2012-04 约 7.8 万亿，"
        "此后的水平低于交易所公布的流通市值），这条线不宜跨空档比较；中证全指全收益、红利低波全收益为月末收盘；70 城与一线 4 城二手住宅为各城市环比"
        "（上月 = 100）链式定基到 2010-12 = 100 后的等权算术平均，2011-01 起；黄金为人民币金价月均（元/克）。",
        f"金价分两段拼接：{GOLD_SPLIT} 起为上海黄金交易所 Au99.99 日收盘的月均价；之前为世界银行 Pink Sheet 月均美元金价 × "
        f"中国货币网美元兑人民币中间价月均 ÷ 31.1035，再乘以两段在 {overlap[0]} → {overlap[1]}（{seed.get('overlapMonths')} 个月）重叠期的"
        f"几何均值比 k = {k}（上金所相对国际价的溢价与月均口径差）；早期一段为静态种子 state/gold_seed.json。",
        "M2、GDP、70 城房价的原始发布方是国家统计局；其数据查询接口对本看板的运行环境返回 403，这里取东方财富数据中心转载的同一组数据"
        "（货币供应量、国内生产总值、70 个大中城市住宅销售价格），字段口径与统计局公布值一致。",
        "未纳入：人民币存款余额（统计局接口不可达、无替代来源，且与 M2 高度重合）；城镇居民人均可支配收入（统计局接口不可达、无替代来源）。"
        + ("70 城里环比有缺失、整条剔除的城市：" + "、".join(data["houseDropped"]) + "。" if data["houseDropped"] else ""),
        "图中每条线在「全部」窗口以自己的第一个有值月份为 100（各序列起点不同：M2 2008-01、70 城 2011-01、GDP 2006-12，其余 2006-01）；"
        "「10 年 / 5 年」窗口以共同起点月为 100；纵轴为对数坐标，相同斜率即相同年化。表中倍数与年化按各自起点与最新月份计算。",
        "更新方式：M2 / GDP / 70 城每月 20 日之后的首次运行刷新一次（统计局数据在每月 10–20 日陆续发布，GDP 为季度）；"
        "金价每月首次运行取上月完整日收盘；指数与流通市值随日行情更新，月末值只在该月结束后进入。",
        "各序列截止月份：" + "，".join(f"{by_key[k]['name']} {by_key[k]['end']}" for k, *_ in SERIES_META) + f"；轴截止 {data['end']}。",
    ]
    return {
        "asOf": as_of,
        "sources": [
            "东方财富 数据中心 datacenter-web.eastmoney.com（M2、GDP 累计值、70 城二手住宅环比 —— 国家统计局接口不可达时的替代来源）",
            "申万宏源研究 swsresearch.com（申万Ａ指 流通市值）",
            "中证指数官网 csindex.com.cn（中证全指全收益 H00985、红利低波全收益 H20269）",
            "上海黄金交易所 sge.com.cn（Au99.99 日收盘）",
            "世界银行 Pink Sheet · 中国外汇交易中心 中间价（2006-01 → 2016-11 金价月均，静态种子 state/gold_seed.json）",
        ],
        "notes": notes,
        "data": data,
    }
