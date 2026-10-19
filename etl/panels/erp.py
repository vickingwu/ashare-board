# -*- coding: utf-8 -*-
"""面板 erp —— 股债性价比。

ERP（股权风险溢价，百分点）= 盈利收益率 − 10 年期国债收益率，其中 盈利收益率 = 100 / PE；
比值 = 盈利收益率 ÷ 国债收益率。股票代表用 中证800（沪深300 + 中证500，PE 历史自 2007-01 起），
国债收益率用 中债国债收益率曲线 10 年期（中债登，2006-03 起）。

月末取值：PE 取已完结月份的月末，国债收益率取同月最后一个交易日；最新一点为最近交易日的 PE
与当日或之前最近一日的国债收益率。``compute_panel()`` 是纯函数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from etl import compute
from etl.common import PanelError, _fmt_exc
from etl.sources import chinabond, pe_select

log = logging.getLogger("etl.erp")

PANEL_ID = "erp"
TITLE = "股债性价比"

PRIMARY = ("000906", "中证800", "H00906")
FALLBACK = ("000300", "沪深300", "H00300")
MIN_MONTHS = 120
Y_MIN, Y_MAX = 0.0, 10.0


def compute_panel(pe_me_dates: List[str], pe_me: List[float], y_dates: List[str], y_vals: List[float],
                  latest_pe_date: str, latest_pe: float) -> Dict[str, Any]:
    """月末 PE + 国债日序列 + 最新 PE → ERP 面板数据。"""
    if any(not (Y_MIN < y < Y_MAX) for y in y_vals):
        bad = next(y for y in y_vals if not (Y_MIN < y < Y_MAX))
        raise PanelError(f"国债收益率超出 ({Y_MIN}, {Y_MAX}): {bad}")
    if any(p is None or p <= 0 for p in pe_me) or latest_pe is None or latest_pe <= 0:
        raise PanelError("PE 含非正值")
    months, vals, dates = compute.align_by_month({"pe": (pe_me_dates, pe_me), "y10": (y_dates, y_vals)})
    if len(months) < MIN_MONTHS:
        raise PanelError(f"PE 与国债收益率对齐后只有 {len(months)} 个月（< {MIN_MONTHS}）")
    pe = vals["pe"]
    y10 = vals["y10"]
    ey = [100.0 / p for p in pe]
    erp = [e - y for e, y in zip(ey, y10)]
    ratio = [e / y for e, y in zip(ey, y10)]
    out_dates = dates["pe"]
    compute.assert_ascending(out_dates, "ERP 月末日期")

    i_y = compute.last_index_on_or_before(y_dates, latest_pe_date)
    if i_y is None:
        raise PanelError(f"没有 {latest_pe_date} 或之前的国债收益率")
    l_y10 = y_vals[i_y]
    l_ey = 100.0 / latest_pe
    l_erp = l_ey - l_y10
    mean, std = compute.mean_std(erp)
    r_mean, _ = compute.mean_std(ratio)
    summary = {
        "latest": {
            "date": latest_pe_date, "pe": round(latest_pe, 2), "ey": round(l_ey, 4),
            "y10": round(l_y10, 4), "y10Date": y_dates[i_y],
            "erp": round(l_erp, 4), "ratio": round(l_ey / l_y10, 4),
        },
        "erpPct": round(compute.percentile_rank(erp, l_erp), 1),
        "ratioPct": round(compute.percentile_rank(ratio, l_ey / l_y10), 1),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "bands": {"p1": round(mean + std, 4), "m1": round(mean - std, 4),
                  "p2": round(mean + 2 * std, 4), "m2": round(mean - 2 * std, 4)},
        "ratioMean": round(r_mean, 4),
        "sampleStart": out_dates[0],
        "sampleEnd": out_dates[-1],
        "nMonths": len(months),
    }
    data = {
        "dates": out_dates,
        "pe": [round(p, 2) for p in pe],
        "ey": [round(e, 4) for e in ey],
        "y10": [round(y, 4) for y in y10],
        "erp": [round(e, 4) for e in erp],
        "ratio": [round(r, 4) for r in ratio],
        "summary": summary,
    }
    # 自检：JSON 里的 erp 与 ey − y10 的差只来自四舍五入
    for a, e, y in zip(data["erp"], data["ey"], data["y10"]):
        if abs(a - (e - y)) > 2e-4:
            raise PanelError("ERP 序列与 盈利收益率 − 国债收益率 不一致")
    return data


def _try_pe(code: str, name: str, pe_code: str) -> Optional[Dict[str, Any]]:
    try:
        return pe_select.get_monthly_pe(code, name, pe_code=pe_code)
    except PanelError as e:
        log.warning("%s %s PE 不可用: %s", code, name, _fmt_exc(e))
        return None


def build() -> dict:
    notes_extra: List[str] = []
    sel = _try_pe(*PRIMARY)
    if sel is None:
        sel = _try_pe(*FALLBACK)
        if sel is None:
            raise PanelError("中证800 与 沪深300 的 PE 都不可用")
        notes_extra.append("中证800 PE 本次不可用，股票端改用沪深300。")
    y = chinabond.get_cgb10y()
    log.info("国债 10Y: %d 行，%s → %s，最新 %.4f%%", len(y["dates"]), y["start"], y["end"], y["yield"][-1])
    data = compute_panel(sel["meDates"], sel["mePe"], y["dates"], y["yield"], sel["latestDate"], sel["latestPe"])
    data["equity"] = {"code": sel["code"], "name": sel["name"], "peSource": sel["source"], "peSourceNote": sel["sourceNote"]}
    s = data["summary"]
    log.info("ERP 最新 %s: PE %.2f EY %.2f%% 10Y %.2f%% ERP %.2f pp（分位 %.0f%%）比值 %.2f（分位 %.0f%%），%d 个月自 %s",
             s["latest"]["date"], s["latest"]["pe"], s["latest"]["ey"], s["latest"]["y10"], s["latest"]["erp"],
             s["erpPct"], s["latest"]["ratio"], s["ratioPct"], s["nMonths"], s["sampleStart"])

    pe_src = ("乐咕乐股 legulegu.com（%s 整体法滚动市盈率）" % sel["name"]) if sel["source"] == "legulegu" \
        else ("中证指数有限公司 csindex.com.cn（%s 日行情 peg）" % sel["name"])
    notes = [
        "ERP = 盈利收益率 − 10 年期国债收益率，单位是百分点；盈利收益率 = 100 ÷ PE，即每 100 元市值对应的年度净利润。"
        "比值 = 盈利收益率 ÷ 国债收益率，比值越高，持有股票相对持有国债的回报优势越大。",
        f"股票端用{sel['name']}的滚动市盈率（整体法）；中证800 = 沪深300 + 中证500，覆盖沪深两市绝大部分市值，"
        "比沪深300 更接近「全市场」，且 PE 历史足够长（中证全指的 PE 只能追溯到 2011 年）。",
        f"样本为 {s['sampleStart'][:7]} 至 {s['sampleEnd'][:7]} 的月末值（{s['nMonths']} 个月），起点受中证800 PE 历史限制；"
        "国债收益率曲线从 2006-03 起有数据。最新一点为最近交易日的 PE 与当日或之前最近一日的国债收益率。",
        "均值、±1σ、±2σ 为全样本统计；分位 = 历史月末 ERP 中「小于或等于当前值」的占比。",
        "ERP 同时受 PE 与利率影响：利率下行会抬高 ERP，即便股票估值没有变化；比较不同年代的 ERP 时要注意利率水平的差异。",
        "PE 口径为整体法（市值加权），与按成分股等权平均得到的 PE 差别很大，看其它来源的数字时先对口径。",
    ]
    notes.extend(notes_extra)
    return {
        "asOf": s["latest"]["date"],
        "sources": [pe_src, "中债登 yield.chinabond.com.cn（中债国债收益率曲线 10 年期）"],
        "notes": notes,
        "data": data,
    }
