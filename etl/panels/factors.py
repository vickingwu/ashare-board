# -*- coding: utf-8 -*-
"""面板 factors —— 风格因子净值：六个中证风格 / 策略指数（红利、低波动、质量、动量、价值、成长）从共同起点起的日线，
外加同口径的中证全指基准（全收益 H00985、价格 000985）供前端算相对强弱。

因子 ↔ 指数（``FACTORS``，2026-10-23 在中证指数官网逐个核对，全部从 2005-01-04 起有日行情）：
* 红利   H00922 中证红利全收益（全收益；样本空间 沪深 A 股）；
* 低波动 930847 中证800行业中性低波动（**价格**；对应全收益代码 H30847 接口无数据）；
* 质量   931156 中证800质量成长（**价格**；H31156 无数据；另一候选 930860 中证盈利质量 2005-07 起、口径更窄，未选）；
* 动量   H20260 沪深300动量全收益（全收益；样本空间 沪深300，是中证唯一有长历史的 A 股动量指数）；
* 价值   H20356 中证800价值全收益；成长 H20355 中证800成长全收益。
两个价格指数不含股息再投资，长期会低估约等于股息率的回报；相对强弱按同口径相除：全收益因子 ÷ H00985，价格因子 ÷ 000985。

数据形状：``data = {dates[], start, series[]{key, name, code, kind ("tr"|"price"), universe, values[]}, bench{tr{code, name, values[]},
price{code, name, values[]}}, summary[]{key, name, code, kind, start, end, first, last, multiple, years, cagrAll, cagr10y, cagr5y,
cagr3y, maxDrawdown}, lagging[]}``。``start`` = 所有序列（含基准）首日的最大值；``dates`` = 自 start 起各序列交易日的并集；
``values`` 与 dates 等长（2 位小数，缺日为 null）。``COMPACT_JSON = True``，序列化体积 ≤ ``SIZE_BUDGET``（自检）。
自检：恰好 ``len(FACTORS)`` 个因子、每条 ≥ ``MIN_ROWS`` 行且收盘全部 > 0、日期严格递增、两个基准齐全。
``compute_panel()`` 是纯函数；``build()`` 负责取数（8 次 ``get_index_history``，同日第二次起全部走缓存）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import PanelError
from etl.sources.csindex_hist import get_index_history

log = logging.getLogger("etl.factors")

PANEL_ID = "factors"
TITLE = "风格因子净值"
COMPACT_JSON = True
SIZE_BUDGET = 1_000_000

BENCH_TR = ("H00985", "中证全指全收益")
BENCH_PRICE = ("000985", "中证全指")
# (key, name, code, kind, universe)
FACTORS: List[Tuple[str, str, str, str, str]] = [
    ("dividend", "红利", "H00922", "tr", "沪深 A 股"),
    ("lowvol", "低波动", "930847", "price", "中证800"),
    ("quality", "质量", "931156", "price", "中证800"),
    ("momentum", "动量", "H20260", "tr", "沪深300"),
    ("value", "价值", "H20356", "tr", "中证800"),
    ("growth", "成长", "H20355", "tr", "中证800"),
]
MIN_ROWS = 4000
WINDOWS = ((10, "cagr10y"), (5, "cagr5y"), (3, "cagr3y"))


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _check_history(label: str, h: Dict[str, Any]) -> None:
    if not h or not h.get("dates"):
        raise PanelError(f"{label} 没有行情")
    if len(h["dates"]) < MIN_ROWS:
        raise PanelError(f"{label} 只有 {len(h['dates'])} 行行情（应 ≥ {MIN_ROWS}），疑似数据不完整")
    compute.assert_ascending(h["dates"], f"{label} 日期")
    for d, v in zip(h["dates"], h["close"]):
        if v is None or v <= 0:
            raise PanelError(f"{label} {d} 收盘值非正: {v}")


def _aligned(pos: Dict[str, int], n: int, h: Dict[str, Any], start: str) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * n
    for d, v in zip(h["dates"], h["close"]):
        if d >= start:
            out[pos[d]] = round(v, 2)
    return out


def compute_panel(histories: Dict[str, Dict[str, Any]], bench_tr: Dict[str, Any], bench_price: Dict[str, Any]
                  ) -> Dict[str, Any]:
    """``histories`` = {code: {dates[], close[]}}（六个因子）、两个基准 → data。"""
    missing = [code for _k, _n, code, _kind, _u in FACTORS if code not in histories]
    if missing or len(histories) != len(FACTORS):
        raise PanelError(f"因子指数应恰好 {len(FACTORS)} 个，缺 {missing}，多 {sorted(set(histories) - {f[2] for f in FACTORS})}")
    for key, name, code, _kind, _u in FACTORS:
        _check_history(f"{name} {code}", histories[code])
    _check_history(f"{BENCH_TR[1]} {BENCH_TR[0]}", bench_tr)
    _check_history(f"{BENCH_PRICE[1]} {BENCH_PRICE[0]}", bench_price)

    all_h = [histories[f[2]] for f in FACTORS] + [bench_tr, bench_price]
    start = max(h["dates"][0] for h in all_h)
    dates = sorted({d for h in all_h for d in h["dates"] if d >= start})
    pos = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    series_out: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []
    lagging: List[str] = []
    for key, name, code, kind, universe in FACTORS:
        h = histories[code]
        vals = _aligned(pos, n, h, start)
        dd = [d for d in h["dates"] if d >= start]
        vv = [v for d, v in zip(h["dates"], h["close"]) if d >= start]
        years = compute.years_between(dd[0], dd[-1])
        entry: Dict[str, Any] = {
            "key": key, "name": name, "code": code, "kind": kind,
            "start": dd[0], "end": dd[-1], "first": round(vv[0], 2), "last": round(vv[-1], 2),
            "multiple": round(vv[-1] / vv[0], 4), "years": round(years, 2),
            "cagrAll": _r(compute.cagr(vv[0], vv[-1], years)),
        }
        for yrs, wkey in WINDOWS:
            entry[wkey] = _r(compute.window_cagr(dd, vv, yrs))
        entry["maxDrawdown"] = _r(compute.max_drawdown(vv))
        summary.append(entry)
        series_out.append({"key": key, "name": name, "code": code, "kind": kind, "universe": universe, "values": vals})
        if dd[-1] != dates[-1]:
            lagging.append(key)
    data = {
        "dates": dates,
        "start": start,
        "series": series_out,
        "bench": {
            "tr": {"code": BENCH_TR[0], "name": BENCH_TR[1], "values": _aligned(pos, n, bench_tr, start)},
            "price": {"code": BENCH_PRICE[0], "name": BENCH_PRICE[1], "values": _aligned(pos, n, bench_price, start)},
        },
        "summary": summary,
        "lagging": lagging,
    }
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > SIZE_BUDGET:
        raise PanelError(f"factors 数据体积 {size:,} B 超过预算 {SIZE_BUDGET:,} B")
    return data


def build() -> dict:
    histories: Dict[str, Dict[str, Any]] = {}
    for key, name, code, kind, _u in FACTORS:
        h = get_index_history(code)
        histories[code] = h
        log.info("%s %s（%s）: %d 行，%s → %s", name, code, "全收益" if kind == "tr" else "价格", len(h["dates"]),
                 h["dates"][0], h["dates"][-1])
    bench_tr = get_index_history(BENCH_TR[0])
    bench_price = get_index_history(BENCH_PRICE[0])
    data = compute_panel(histories, bench_tr, bench_price)
    as_of = data["dates"][-1]
    log.info("风格因子净值: %s → %s（%d 日）；%s", data["start"], as_of, len(data["dates"]),
             "；".join(f"{s['name']} ×{s['multiple']:.2f} 年化 {s['cagrAll']}% 最大回撤 {s['maxDrawdown']}%" for s in data["summary"]))
    kind_label = {"tr": "全收益", "price": "价格"}
    notes = [
        "因子与指数的对应：" + "；".join(f"{name} = {code} {kind_label[kind]}（样本空间 {universe}）"
                                       for _k, name, code, kind, universe in FACTORS)
        + "。六个指数都由中证指数公司编制发布，日行情从 2005-01-04 起。",
        "低波动与质量只有价格指数（对应的全收益代码在接口里没有数据），不含成分股分红再投资，长期回报会低估约等于股息率的部分；"
        "其余四个为全收益指数。比较时请留意表中的「口径」列。",
        f"相对强弱 = 因子指数 ÷ 同口径的中证全指：全收益因子除以 {BENCH_TR[1]}（{BENCH_TR[0]}），价格因子除以 {BENCH_PRICE[1]}"
        f"（{BENCH_PRICE[0]}），在时间窗起点归一为 100，高于 100 即跑赢中证全指。",
        f"共同起点 {data['start']}：图中每条线在所选时间窗（全部 / 10 年 / 5 年 / 3 年 / 1 年）的首日归一为 100，纵轴为对数坐标；"
        "表中累计倍数与年化按各自序列的首末日计算，10 年 / 5 年 / 3 年年化从最新日期同月同日往前推。",
        "更新方式：中证指数官网约每个交易日 18:30 前后发布当日行情，缓存在 cache/csindex_hist/ 并按日增量补齐；"
        "对该站的请求间隔 12 秒（± 1 秒随机抖动，避开其约 100 秒内十余个请求的突发限流），遇到限流（HTTP 403）或连接被重置按 "
        "30 → 60 → 120 → 240 秒等待后再试，仍失败则本次运行不再请求该站、保留上一版数据。",
    ]
    if data["lagging"]:
        by_key = {s["key"]: s for s in data["summary"]}
        notes.append("以下因子的最新日期早于其它序列（来源尚未更新到同一日）：" + "，".join(f"{by_key[k]['name']} {by_key[k]['end']}" for k in data["lagging"]))
    return {
        "asOf": as_of,
        "sources": ["中证指数有限公司 csindex.com.cn（风格 / 策略指数日行情：全收益或价格）"],
        "notes": notes,
        "data": data,
    }
