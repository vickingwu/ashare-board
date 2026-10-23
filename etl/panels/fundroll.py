# -*- coding: utf-8 -*-
"""面板 fundroll —— 基金指数滚动年化：中证偏股型基金指数（930950）的 1 / 3 / 5 / 7 年滚动年化收益，
以及它与非纯债债基、沪深300全收益、中证A500 和三个中证基金指数（股票 / 混合 / 债券）对齐后的净值序列。

口径：
* 中证基金指数以样本基金的复权单位净值编制（含分红再投资），由中证指数公司发布，接口返回从 2005-01-04 起的日行情
  （930950 / 930897 基日 2007-12-28 = 1000，更早没有数据）；
* 「偏债」替代：中证指数公司没有「偏债型基金指数」，用 930897 中证非纯债债券型基金指数近似——样本为可持有股票 / 可转债的
  债券型基金（一级 / 二级债基），在 ``SERIES`` 里命名为「非纯债债基（近似偏债）」；
* 滚动年化（``compute.rolling_annualized``）：n 个交易日为一个窗口（``WINDOWS`` 252 / 756 / 1260 / 1764 ≈ 1 / 3 / 5 / 7 年），
  年化 = (末 / 初)^(252 / n) − 1（%），窗口未满为 null；
* 对齐：所有序列以 ``MAIN`` 930950 的交易日为轴（``dates``），其它指数按日期查表、缺日为 null；收盘保留 2 位小数；
* 起点选择 ``START_OPTIONS`` 只是前端归一（= 100）的候选日期。

数据形状：``data = {dates[], windows{"1":252,"3":756,"5":1260,"7":1764}, rolling{"1":[],"3":[],"5":[],"7":[]}, series[]{key, name, code,
kind ("nav"|"tr"|"price"), extra, values[]}, startOptions[], latest{date, close, r1, r3, r5, r7}, stats{"1":{min, max, latest,
pctPositive, n}, …}}``。``COMPACT_JSON = True``，体积 ≤ ``SIZE_BUDGET``（自检）。
自检：主序列日期严格递增、≥ ``MIN_ROWS`` 行、收盘全部 > 0；每个滚动数组前 n 位为 null、其后 |r| < 200。
``compute_panel()`` 是纯函数；``build()`` 负责取数（7 次 ``get_index_history``）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import PanelError
from etl.sources.csindex_hist import get_index_history

log = logging.getLogger("etl.fundroll")

PANEL_ID = "fundroll"
TITLE = "基金指数滚动年化"
COMPACT_JSON = True
SIZE_BUDGET = 1_000_000

MAIN = "930950"
WINDOWS: Dict[str, int] = {"1": 252, "3": 756, "5": 1260, "7": 1764}
PERIODS = 252
# (key, name, code, kind, extra)
SERIES: List[Tuple[str, str, str, str, bool]] = [
    ("equityFund", "偏股基金", "930950", "nav", False),
    ("bondPlusFund", "非纯债债基（近似偏债）", "930897", "nav", False),
    ("csi300TR", "沪深300全收益", "H00300", "tr", False),
    ("a500", "中证A500（价格）", "000510", "price", False),
    ("stockFund", "股票基金", "H11021", "nav", True),
    ("mixedFund", "混合基金", "H11022", "nav", True),
    ("bondFund", "债券基金", "H11023", "nav", True),
]
START_OPTIONS = ["2008-01-01", "2014-01-01", "2019-01-01", "2024-01-01"]
MIN_ROWS = 4000
MAX_ABS_ROLLING = 200.0


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def compute_panel(histories: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """``histories`` = {code: {dates[], close[]}}（``SERIES`` 的 7 个代码）→ data。"""
    missing = [code for _k, _n, code, _kind, _x in SERIES if code not in histories]
    if missing:
        raise PanelError(f"基金指数缺少行情: {missing}")
    main = histories[MAIN]
    dates: List[str] = list(main["dates"])
    if len(dates) < MIN_ROWS:
        raise PanelError(f"{MAIN} 只有 {len(dates)} 行行情（应 ≥ {MIN_ROWS}），疑似数据不完整")
    compute.assert_ascending(dates, f"{MAIN} 日期")
    close: List[float] = []
    for d, v in zip(dates, main["close"]):
        if v is None or v <= 0:
            raise PanelError(f"{MAIN} {d} 收盘值非正: {v}")
        close.append(float(v))

    rolling: Dict[str, List[Optional[float]]] = {}
    stats: Dict[str, Dict[str, Any]] = {}
    for k, n in WINDOWS.items():
        arr = [_r(v) for v in compute.rolling_annualized(close, n, PERIODS)]
        if any(v is not None for v in arr[:n]):
            raise PanelError(f"{k} 年滚动年化前 {n} 位应为空")
        present = [v for v in arr if v is not None]
        bad = [v for v in present if abs(v) >= MAX_ABS_ROLLING]
        if bad:
            raise PanelError(f"{k} 年滚动年化出现 |r| ≥ {MAX_ABS_ROLLING}: {bad[0]}")
        rolling[k] = arr
        stats[k] = {
            "min": min(present) if present else None,
            "max": max(present) if present else None,
            "latest": arr[-1],
            "pctPositive": round(sum(1 for v in present if v > 0) / len(present) * 100.0, 1) if present else None,
            "n": len(present),
        }

    series_out: List[Dict[str, Any]] = []
    for key, name, code, kind, extra in SERIES:
        h = histories[code]
        compute.assert_ascending(h["dates"], f"{name} {code} 日期")
        lookup = {d: v for d, v in zip(h["dates"], h["close"]) if v is not None}
        vals = [None if lookup.get(d) is None else round(lookup[d], 2) for d in dates]
        if not any(v is not None for v in vals):
            raise PanelError(f"{name} {code} 与 {MAIN} 没有任何重叠交易日")
        series_out.append({"key": key, "name": name, "code": code, "kind": kind, "extra": extra, "values": vals})

    latest = {"date": dates[-1], "close": round(close[-1], 2)}
    for k in WINDOWS:
        latest[f"r{k}"] = rolling[k][-1]
    data = {
        "dates": dates,
        "windows": dict(WINDOWS),
        "rolling": rolling,
        "series": series_out,
        "startOptions": list(START_OPTIONS),
        "latest": latest,
        "stats": stats,
    }
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > SIZE_BUDGET:
        raise PanelError(f"fundroll 数据体积 {size:,} B 超过预算 {SIZE_BUDGET:,} B")
    return data


def build() -> dict:
    histories: Dict[str, Dict[str, Any]] = {}
    for key, name, code, kind, extra in SERIES:
        h = get_index_history(code)
        histories[code] = h
        log.info("%s %s（%s%s）: %d 行，%s → %s", name, code, {"nav": "基金净值", "tr": "全收益", "price": "价格"}[kind],
                 "，可选" if extra else "", len(h["dates"]), h["dates"][0], h["dates"][-1])
    data = compute_panel(histories)
    lt = data["latest"]
    log.info("基金指数滚动年化: %s → %s（%d 日），最新 %s 收盘 %.2f，滚动年化 1 年 %s%% / 3 年 %s%% / 5 年 %s%% / 7 年 %s%%",
             data["dates"][0], lt["date"], len(data["dates"]), lt["date"], lt["close"], lt["r1"], lt["r3"], lt["r5"], lt["r7"])
    notes = [
        "中证基金指数以样本基金的复权单位净值编制，含分红再投资，由中证指数公司发布；偏股基金指数（930950）的样本为股票型与偏股混合型基金，"
        f"基日 {data['dates'][0]} = 1000 点，是本面板滚动年化的计算对象。",
        "中证没有「偏债型基金指数」，这里用中证非纯债债券型基金指数（930897）近似：样本为可持有股票或可转债的债券型基金（一级 / 二级债基），"
        "图中标注「近似偏债」。股票基金 / 混合基金 / 债券基金三个中证基金指数（H11021 / H11022 / H11023）默认隐藏，可在图例里打开。",
        "滚动年化：以 n 个交易日为窗口（252 日 ≈ 1 年、756 ≈ 3 年、1260 ≈ 5 年、1764 ≈ 7 年），年化 = (窗口末值 ÷ 窗口初值)^(252 ÷ n) − 1，"
        "每个交易日向前看一个窗口；窗口未满的日期为空。读数里的「近 N 年年化」即最新一日的窗口值。",
        "净值图把所选起点（2008-01 / 2014-01 / 2019-01 / 2024-01 之后的第一个交易日）归一为 100，纵轴为对数坐标；沪深300全收益与中证A500（价格指数，"
        "不含股息）作对照。",
        "常见商业数据库里的基金指数代码（如 885xxx 系列）不公开，这里全部用中证指数公司公开发布的基金指数与宽基指数；"
        "数据来自中证指数官网的指数日行情接口，缓存在 cache/csindex_hist/ 并按日增量补齐，约每个交易日 18:30 前后更新。",
    ]
    return {
        "asOf": lt["date"],
        "sources": ["中证指数有限公司 csindex.com.cn（中证基金指数、沪深300全收益、中证A500 日行情）"],
        "notes": notes,
        "data": data,
    }
