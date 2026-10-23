# -*- coding: utf-8 -*-
"""面板 investors —— 投资者结构：沪市两融余额、沪市 A 股流通市值与换手率、全 A 流通市值与成交额的卡片，
加一张「沪市融资融券余额 / 融资余额 + 中证全指」的日线图。

来源与范围（卡片的 ``scope`` 字段逐一标注，分子分母同口径相除）：
* 两融：上交所 融资融券汇总（``sse_margin.get_margin_history``，2010-03-31 起，T+1 上午发布）—— **沪市口径**；
  深市只有按日报表接口、历史需逐日请求，未纳入；
* 沪市 A 股流通市值 / 成交额：上交所 每日概况（``sse_overview``，PRODUCT_CODE 01 + 03 + 11，当日收盘后晚间可查）；
  ``marginRatio`` = 沪市两融余额 ÷ 沪市 A 股流通市值 × 100，只在概况日期 == 两融最后一日时计算，否则为 null 并注明；
* 全 A 流通市值 / 成交额：申万Ａ指（``sws.get_a_index``，申万口径，沪深两市全部 A 股）；``aTurnover20`` = 20 日均成交额 ÷
  当日流通市值 × 100；
* 叠加线：中证全指 000985 收盘按日期对齐到两融日期（缺日为 null）。

未纳入（``dropped``，原因写在 notes）：新增投资者（中证登无机器可读序列、上交所月报为 PDF）、公募基金规模（中基协统计页无固定文件地址）、
北向持股（港交所只有逐股页面）、深市两融（按日报表接口）。

数据形状：``data = {cards[]{key, label, value, unit, date, scope, note}, chart{dates[], total[], rzye[], csiAll[]}, latest{date, total,
rzye, ratioPct}, dropped[]{key, name, reason}}``；卡片顺序见 ``CARD_ORDER``。``COMPACT_JSON = True``。
自检：两融日期严格递增且 ≥ ``MIN_MARGIN_ROWS`` 行；``total[-1] ≥ rzye[-1] > 0``；``marginRatio`` 存在时落在 ``RATIO_RANGE``（%）内；
叠加线非空比例 ≥ ``MIN_CSI_COVERAGE``。``compute_panel()`` 是纯函数；``build()`` 负责取数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from etl import compute
from etl.common import PanelError
from etl.sources import csindex_hist, sse_margin, sse_overview, sws

log = logging.getLogger("etl.investors")

PANEL_ID = "investors"
TITLE = "投资者结构"
COMPACT_JSON = True

CSI_ALL = "000985"
MIN_MARGIN_ROWS = 3900
RATIO_RANGE = (0.3, 10.0)
MA_DAYS = 20
MIN_CSI_COVERAGE = 0.9
SCOPE_SH = "沪市"
SCOPE_SH_A = "沪市 A 股"
SCOPE_ALL_A = "全 A（申万口径）"
CARD_ORDER = ["marginTotal", "marginFin", "marginRatio", "sseNego", "sseTurnover", "aMv", "aAmount", "aTurnover20"]
NO_OVERVIEW_NOTE = "当日概况未取到"
DROPPED = [
    {"key": "newInvestors", "name": "新增投资者", "reason": "中证登没有机器可读的月度序列（2023-08 起停更），上交所统计月报为 PDF"},
    {"key": "fundAum", "name": "公募基金规模", "reason": "中基协统计页改版后没有固定的文件地址"},
    {"key": "northbound", "name": "北向持股", "reason": "港交所只提供逐股持股页面，没有官方的合计序列"},
    {"key": "szMargin", "name": "深市两融", "reason": "深交所只提供按日报表接口，历史需逐日请求"},
]


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _card(key: str, label: str, value: Optional[float], unit: str, date: Optional[str], scope: str, note: str
          ) -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "unit": unit, "date": date, "scope": scope, "note": note}


def compute_panel(margin: Dict[str, Any], overview: Optional[Dict[str, Any]], a_index: Dict[str, Any],
                  csi: Dict[str, Any]) -> Dict[str, Any]:
    """``margin`` = sse_margin 的 {dates, rzye, total}；``overview`` = sse_overview.a_share_totals() 或 None；
    ``a_index`` = sws.get_a_index()；``csi`` = 000985 的 {dates, close} → data。"""
    dates: List[str] = list(margin["dates"])
    if len(dates) < MIN_MARGIN_ROWS:
        raise PanelError(f"沪市两融只有 {len(dates)} 行（应 ≥ {MIN_MARGIN_ROWS}），疑似数据不完整")
    compute.assert_ascending(dates, "沪市两融日期")
    total = [_r(v) for v in margin["total"]]
    rzye = [_r(v) for v in margin["rzye"]]
    if len(total) != len(dates) or len(rzye) != len(dates):
        raise PanelError("沪市两融三列长度不一致")
    m_date = dates[-1]
    if total[-1] is None or rzye[-1] is None or not (total[-1] >= rzye[-1] > 0):
        raise PanelError(f"沪市两融最新值异常: 融资融券余额 {total[-1]}，融资余额 {rzye[-1]}")

    ratio: Optional[float] = None
    ratio_note = NO_OVERVIEW_NOTE
    ov_date = overview.get("date") if overview else None
    nego = overview.get("negoValue") if overview else None
    amt = overview.get("tradeAmt") if overview else None
    if overview is not None and ov_date == m_date and nego:
        ratio = _r(total[-1] / nego * 100.0)
        ratio_note = "沪市融资融券余额 ÷ 沪市 A 股流通市值"
        if not (RATIO_RANGE[0] <= ratio <= RATIO_RANGE[1]):
            raise PanelError(f"两融余额 / 流通市值 = {ratio}% 超出 {RATIO_RANGE}：{total[-1]} / {nego}")
    elif overview is not None and ov_date != m_date:
        ratio_note = f"{NO_OVERVIEW_NOTE}（概况最近为 {ov_date}，两融为 {m_date}）"
    sse_turnover = compute.safe_div_pct(amt, nego, 2) if overview else None

    a_dates = a_index["dates"]
    a_mv = a_index["mv"]
    a_amt = a_index["amount"]
    a_date = a_dates[-1] if a_dates else None
    mv_last = a_mv[-1] if a_mv else None
    amt_last = a_amt[-1] if a_amt else None
    ma = compute.rolling_mean(a_amt, MA_DAYS)[-1] if len(a_amt) >= MA_DAYS else None
    turnover20 = compute.safe_div_pct(ma, mv_last, 2) if mv_last else None

    cards = [
        _card("marginTotal", "沪市融资融券余额", total[-1], "亿元", m_date, SCOPE_SH, "融资余额 + 融券余额，上交所 T+1 发布"),
        _card("marginFin", "沪市融资余额", rzye[-1], "亿元", m_date, SCOPE_SH, "上交所 T+1 发布"),
        _card("marginRatio", "两融余额 ÷ 流通市值", ratio, "%", m_date, SCOPE_SH_A, ratio_note),
        _card("sseNego", "沪市 A 股流通市值", _r(nego), "亿元", ov_date, SCOPE_SH_A,
              "上交所每日概况，主板 + 科创板 + 存托凭证" if overview else NO_OVERVIEW_NOTE),
        _card("sseTurnover", "沪市 A 股当日换手率", sse_turnover, "%", ov_date, SCOPE_SH_A,
              "成交额 ÷ 流通市值 × 100" if overview else NO_OVERVIEW_NOTE),
        _card("aMv", "全 A 流通市值", _r(mv_last), "亿元", a_date, SCOPE_ALL_A,
              "申万Ａ指成分流通市值" if mv_last is not None else "申万流通市值当日缺失"),
        _card("aAmount", "全 A 成交额", _r(amt_last), "亿元", a_date, SCOPE_ALL_A, "申万Ａ指成分日成交金额"),
        _card("aTurnover20", "20 日均成交额 ÷ 流通市值", turnover20, "%", a_date, SCOPE_ALL_A,
              f"最近 {MA_DAYS} 个交易日成交额均值 ÷ 当日流通市值 × 100" if turnover20 is not None else "申万流通市值当日缺失"),
    ]
    if [c["key"] for c in cards] != CARD_ORDER:
        raise PanelError("卡片顺序与 CARD_ORDER 不一致")

    csi_lookup = {d: v for d, v in zip(csi["dates"], csi["close"]) if v is not None}
    csi_all = [None if csi_lookup.get(d) is None else round(csi_lookup[d], 2) for d in dates]
    coverage = sum(1 for v in csi_all if v is not None) / len(dates)
    if coverage < MIN_CSI_COVERAGE:
        raise PanelError(f"中证全指叠加线只覆盖两融日期的 {coverage:.1%}（应 ≥ {MIN_CSI_COVERAGE:.0%}）")

    return {
        "cards": cards,
        "chart": {"dates": dates, "total": total, "rzye": rzye, "csiAll": csi_all},
        "latest": {"date": m_date, "total": total[-1], "rzye": rzye[-1], "ratioPct": ratio},
        "dropped": [dict(d) for d in DROPPED],
    }


def build() -> dict:
    m = sse_margin.get_margin_history()
    m_date = m["dates"][-1]
    ov = sse_overview.get_latest_overview(m_date)
    totals = sse_overview.a_share_totals(ov) if ov is not None else None
    if totals is None:
        log.warning("上交所每日概况 %s 及之前 %d 天都没有数据，marginRatio 置空", m_date, sse_overview.MAX_BACK_DAYS)
    elif totals["date"] != m_date:
        log.warning("上交所每日概况最近为 %s，两融为 %s，日期不一致，marginRatio 置空", totals["date"], m_date)
    a = sws.get_a_index()
    csi = csindex_hist.get_index_history(CSI_ALL)
    data = compute_panel(m, totals, a, csi)
    lt = data["latest"]
    log.info("投资者结构: 两融 %s → %s（%d 日），融资融券余额 %.2f 亿元，融资余额 %.2f 亿元，两融/沪市流通市值 %s%%；%s",
             data["chart"]["dates"][0], lt["date"], len(data["chart"]["dates"]), lt["total"], lt["rzye"], lt["ratioPct"],
             "，".join(f"{c['label']} {c['value']}{c['unit']}（{c['date']}）" for c in data["cards"][3:]))
    notes = [
        "卡片按来源分两组口径：「沪市」两组数字来自上海证券交易所（融资融券汇总、每日概况），「全 A」来自申万Ａ指（沪深两市全部 A 股，"
        "不含北交所与 B 股）；比值类卡片的分子分母取同一口径：两融余额 ÷ 流通市值用沪市两融对沪市 A 股流通市值（主板 + 科创板 + 存托凭证），"
        "20 日均成交额 ÷ 流通市值用申万Ａ指的成交额对申万口径流通市值。",
        "两融余额为沪市口径（融资融券余额 = 融资余额 + 融券余额）；深市两融未纳入——深交所只提供按日报表接口，历史序列需逐日请求。"
        "图中的融资融券余额与融资余额为上交所 2010-03-31 起的日度数据，中证全指收盘按日期对齐作叠加线。",
        "发布时间：上交所两融汇总在 T+1 上午公布，当日 07:53 的构建把前一交易日的两融补齐；每日概况在当日收盘后晚间可查，"
        "构建时取两融最后一日的概况，取不到时比值卡片留空并注明；申万Ａ指约 18:30 前后更新。",
        "未纳入及原因：" + "；".join(f"{d['name']} —— {d['reason']}" for d in DROPPED) + "。",
        "更新方式：两融与申万按日增量缓存（cache/sse/、cache/sws/），每日概况按日期缓存（每次构建最多 1 次请求），中证全指日行情缓存在 cache/csindex_hist/。",
    ]
    return {
        "asOf": m_date,
        "sources": [
            "上海证券交易所 sse.com.cn（融资融券汇总：沪市融资余额 / 融资融券余额；每日概况：沪市 A 股流通市值、成交额）",
            "申万宏源研究 swsresearch.com（申万Ａ指 成交额与流通市值）",
            "中证指数官网 csindex.com.cn（中证全指 000985 收盘，叠加线）",
        ],
        "notes": notes,
        "data": data,
    }
