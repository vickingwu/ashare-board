# -*- coding: utf-8 -*-
"""面板 valuation —— 主要宽基指数估值分位。

每个指数：当前整体法 PE-TTM 落在自身月末 PE 历史中的位置（含等号分位，0–100），
分别按 全历史 / 近 120 个月末 / 近 60 个月末 计算；月末样本不足 60 个的标 ``shortSample``。
PE 来源按指数不同（见 etl/sources/pe_select.py）：乐咕乐股（2005 起）或中证指数官网 peg（2011 起）。

``compute_one()`` 是纯函数；``build()`` 逐个指数抓数，单个指数失败只记一条说明，不拖垮面板。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import PanelError, _fmt_exc
from etl.sources import pe_select

log = logging.getLogger("etl.valuation")

PANEL_ID = "valuation"
TITLE = "主要宽基指数估值分位"

# (代码, 名称, 取中证 peg 时实际请求的代码)
INDEXES: List[Tuple[str, str, Optional[str]]] = [
    ("000300", "沪深300", None),
    ("000905", "中证500", None),
    ("000852", "中证1000", None),
    ("000906", "中证800", None),
    ("000016", "上证50", None),
    ("000985", "中证全指", "H00985"),
    ("000688", "科创50", None),
    ("932000", "中证2000", None),
    ("000510", "中证A500", None),
    ("H30269", "红利低波", "H20269"),
]
MIN_SURVIVORS = 5
MIN_MONTHS = 12
SHORT_MONTHS = 60
WIN_10Y, WIN_5Y = 120, 60


def compute_one(me_dates: List[str], me_pe: List[float], latest_date: str, latest_pe: float) -> Dict[str, Any]:
    """月末 PE 序列 + 最新值 → 分位与统计。"""
    n = len(me_dates)
    if n < MIN_MONTHS:
        raise PanelError(f"月末样本只有 {n} 个（< {MIN_MONTHS}）")
    compute.assert_ascending(me_dates, "PE 月末日期")
    if any(v is None or v <= 0 for v in me_pe) or latest_pe is None or latest_pe <= 0:
        raise PanelError("PE 含非正值")
    w10 = compute.trailing_window(me_pe, WIN_10Y)
    w5 = compute.trailing_window(me_pe, WIN_5Y)
    return {
        "peNow": round(latest_pe, 2),
        "peDate": latest_date,
        "sampleStart": me_dates[0],
        "sampleEnd": me_dates[-1],
        "nMonths": n,
        "pctAll": round(compute.percentile_rank(me_pe, latest_pe), 1),
        "pct10y": None if w10 is None else round(compute.percentile_rank(w10, latest_pe), 1),
        "pct5y": None if w5 is None else round(compute.percentile_rank(w5, latest_pe), 1),
        "min": round(min(me_pe), 2),
        "max": round(max(me_pe), 2),
        "median": round(compute.median(me_pe), 2),
        "shortSample": n < SHORT_MONTHS,
        "monthly": {"dates": list(me_dates), "values": [round(v, 2) for v in me_pe]},
    }


def build() -> dict:
    indexes = []
    skipped = []
    sources_used = set()
    for code, name, pe_code in INDEXES:
        try:
            sel = pe_select.get_monthly_pe(code, name, pe_code=pe_code)
            one = compute_one(sel["meDates"], sel["mePe"], sel["latestDate"], sel["latestPe"])
        except PanelError as e:
            log.warning("%s %s 跳过: %s", code, name, _fmt_exc(e))
            skipped.append(f"{name}：PE 历史不可用，未纳入（{_fmt_exc(e)}）")
            continue
        one.update({"code": code, "name": name, "source": sel["source"], "sourceNote": sel["sourceNote"],
                    "fallback": sel["fallback"]})
        sources_used.add(sel["source"])
        indexes.append(one)
        log.info("%s %s: PE %.2f（%s）全历史 %s%% 近10年 %s%% 近5年 %s%%，%d 个月末自 %s", code, name, one["peNow"],
                 one["peDate"], one["pctAll"], one["pct10y"], one["pct5y"], one["nMonths"], one["sampleStart"])
    if len(indexes) < MIN_SURVIVORS:
        raise PanelError(f"只有 {len(indexes)} 个指数取到 PE（< {MIN_SURVIVORS}）: {'; '.join(skipped)}")

    as_of = max(i["peDate"] for i in indexes)
    sources = []
    if "legulegu" in sources_used:
        sources.append("乐咕乐股 legulegu.com（指数整体法滚动市盈率，月度）")
    if "csindex" in sources_used:
        sources.append("中证指数有限公司 csindex.com.cn（指数日行情 peg 字段）")
    notes = [
        "PE 口径为滚动市盈率（整体法）：成分股总市值合计 ÷ 最近四个季度净利润合计；不是成分股 PE 的简单平均。",
        "分位 = 历史月末 PE 中「小于或等于当前值」的个数 ÷ 月末个数，范围 0–100；全历史、近 10 年（120 个月末）、"
        "近 5 年（60 个月末）三个窗口分别计算，历史不够长的窗口留空。",
        "当前值取最近交易日的 PE，与之比较的历史只含已完结月份的月末值；当月不计入样本。",
        "月末样本不足 60 个的指数标「样本短」：中证2000（PE 自 2023-06）、中证A500（自 2024-09）、科创50 分位的参考意义有限。",
        "沪深300 / 中证500 / 中证1000 / 中证800 / 上证50 的 PE 来自乐咕乐股（最早 2005-04 起）；"
        "中证全指、红利低波、科创50、中证2000、中证A500 来自中证指数官网日行情（2011 年中以后才有值）。"
        "两个来源的计算细节不同（乐咕约低 4–10%），同一指数只用一个来源，不混接。",
        "分位低只说明当前 PE 相对自身历史偏低，盈利下滑时 PE 会被动抬高，解读时要结合盈利趋势。",
    ]
    notes.extend(skipped)
    data = {"indexes": indexes, "windows": {"all": "全历史", "10y": WIN_10Y, "5y": WIN_5Y}, "shortSampleMonths": SHORT_MONTHS}
    return {"asOf": as_of, "sources": sources, "notes": notes, "data": data}
