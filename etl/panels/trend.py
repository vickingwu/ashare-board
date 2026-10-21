# -*- coding: utf-8 -*-
"""面板 trend —— 市场趋势温度：站上年线（MA250）的股票比例 + 申万一级行业 MACD 为正的比例。

两条「温度」序列：
1. 个股宽度（日度）：当日收盘 > 自身 MA250 的股票数 ÷ 当日有成交且已有 ≥ 250 个交易日记录的股票数（``pct250``），
   同口径的 MA50 版本 ``pct50``；数据来自 ``etl.market_state`` 的全市场日线库聚合 ``agg.json``
   （Baostock 沪深 A 股、后复权收盘，口径细节见 market_state 模块说明）。日度序列从分母首次 ≥ ``MIN_N250``（300 只）
   的那天起发布（2005 年初，当时约 1,300 只），之后分母低于阈值的日子保留日期但 ``pct250`` 为 null；
2. 行业趋势（月度 + 近 3 年日度）：31 个申万一级行业各自收盘序列的 MACD(12,26,9)，``DIF > 0`` 的行业占比为主序列
   ``pctIndustryMacdPos``；``MACD 柱 = 2 × (DIF − DEA) > 0`` 的占比另存为 ``pctHist`` 对照。月度点 = 每月最后一个
   有数据的交易日，最后一个月若未结束则 ``partialLast = true``；行业数随申万各指数起点变化（2000 年初 16 个、
   2014 年起 27 个、2022 年起 31 个）；801950 煤炭先经 ``sws.cut_series_start`` 截掉 2021-12-13 之前的行。

口径与坑：
* ``agg.json`` 由 market_state 按「当日有行的股票」统计，停牌日没有行、新股满 250 行前不进分母、含 ST；
  面板只做比例与对齐，不碰个股数据，输出里也没有任何个股明细；
* 中证全指（000985）收盘只作叠加线，按日期对齐到日度序列（缺日为 null）；
* 面板构建前先调用 ``market_state.update_incremental``（每进程只做一次，feargreed 复用结果）把库追到最新；
  预算用尽时库会落后几个交易日，面板照常出图并在说明里提示；``ASHARE_MARKET_ALLOW_PARTIAL=1``（开发用）
  跳过联网更新与 coldDone 检查、放宽分母自检，并在说明里标注「开发模式：部分股票」；
* 自检：所有比例 ∈ [0, 100]；日度日期严格递增且首日 ≤ 2005-03-31；2005-03-31 之前分母须达到 ≥ 1000、最新分母
  ≥ 4800（开发模式放宽）；latest.date == 日度末日；月度日期严格递增且月份互异、首月 ≤ 2000-03；最新行业数 == 31；
  行业日度末日 == 月度末日。首日分母本身不设 1000 的下限：2004-01-02 起有行的股票里只有约三分之一全年没有停牌日，
  其余在 2005-01-13 之后的几天里陆续满 250 行（实测部分库 98/309 → 3 个交易日后 90 %），所以日度序列的头几天
  分母只有几百只、之后才到 ~1,300。

数据形状：``{daily:{dates[], pct250[], pct50[], n[], above250[]}, monthly:{dates[], pctIndustryMacdPos[], pctHist[],
nIndustries[], partialLast}, industryDaily:{dates[], pct[], pctHist[]}, overlay:{dates[], csiAll[]},
latest:{date, pct250, pct50, n, above250, industryPct, industryPctHist, industryN, industryDate},
universe:{source, start, nStocksNow, nStocksEver, storeLastDate}}``。``compute_panel()`` 是纯函数；``build()`` 负责取数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from etl import compute, market_state
from etl.common import PanelError, env_flag, market_closed, today_str
from etl.sources import csindex_hist, sws

log = logging.getLogger("etl.trend")

PANEL_ID = "trend"
TITLE = "市场趋势温度"
COMPACT_JSON = True

MIN_N250 = 300               # 日度序列从 n250 首次 ≥ 300 的交易日起发布；之后低于它的日子 pct 为 null
MIN_N250_LATEST = 4800       # 最新一日的分母下限（全市场约 5,200 只上市公司）
MIN_N250_FIRST = 1000        # FIRST_DAILY_BY 之前分母须达到的下限（2005 年初约 1,300 只；首日本身可更小，见模块说明）
FIRST_DAILY_BY = "2005-03-31"
FIRST_MONTH_BY = "2000-03"
INDUSTRY_DAILY_DAYS = 750    # 行业日度序列只保留最近约 3 年
INDUSTRY_COUNT = sws.LEVEL1_COUNT
CSI_ALL = "000985"
UNIVERSE_SOURCE = "Baostock 沪深A股"


def _pct(num: int, den: int, nd: int) -> Optional[float]:
    return None if not den else round(num / den * 100.0, nd)


# --------------------------------------------------------------------------- 纯计算
def _daily(agg: Dict[str, list]) -> Dict[str, list]:
    """聚合 → 日度宽度序列：从 n250 首次 ≥ MIN_N250 起；分母不足的日子保留但比例为 None。"""
    dates = list(agg.get("dates") or [])
    if not dates:
        raise PanelError("全市场聚合为空")
    compute.assert_ascending(dates, "全市场聚合日期")
    n250, above250 = agg["n250"], agg["above250"]
    n50, above50 = agg["n50"], agg["above50"]
    for k in ("n250", "above250", "n50", "above50"):
        if len(agg[k]) != len(dates):
            raise PanelError(f"全市场聚合 {k} 长度 {len(agg[k])} ≠ 日期数 {len(dates)}")
    first = next((i for i, n in enumerate(n250) if n >= MIN_N250), None)
    if first is None:
        raise PanelError(f"全市场聚合里没有 n250 ≥ {MIN_N250} 的交易日（共 {len(dates)} 日，最大 n250 {max(n250)}）")
    out: Dict[str, list] = {"dates": dates[first:], "pct250": [], "pct50": [], "n": [int(x) for x in n250[first:]],
                            "above250": [int(x) for x in above250[first:]]}
    for i in range(first, len(dates)):
        out["pct250"].append(_pct(above250[i], n250[i], 2) if n250[i] >= MIN_N250 else None)
        out["pct50"].append(_pct(above50[i], n50[i], 2) if n50[i] >= MIN_N250 else None)
    return out


def _industry(l1: Dict[str, Any], names: Dict[str, str]) -> Dict[str, list]:
    """31 个一级行业的 MACD → 按日期的 {dates, pct(DIF>0 占比), pctHist(柱>0 占比), n(行业数)}。"""
    codes = l1.get("codes") or {}
    missing = [c for c in names if c not in codes]
    if missing:
        raise PanelError(f"申万一级行业历史里缺少 {len(missing)} 个指数: {', '.join(missing)}")
    n_ind: Dict[str, int] = {}
    pos_dif: Dict[str, int] = {}
    pos_hist: Dict[str, int] = {}
    for code, name in names.items():
        d, v = compute.drop_none(l1["dates"], codes[code]["close"])
        d, v = sws.cut_series_start(code, d, v)
        if len(v) <= compute.MACD_WARMUP:
            raise PanelError(f"{name}（{code}）只有 {len(v)} 个收盘，不足 MACD 预热期 {compute.MACD_WARMUP}")
        compute.assert_ascending(d, f"{name} 日期")
        dif, _dea, hist = compute.macd(v)
        for date, x, h in zip(d, dif, hist):
            if x is None or h is None:
                continue
            n_ind[date] = n_ind.get(date, 0) + 1
            if x > 0:
                pos_dif[date] = pos_dif.get(date, 0) + 1
            if h > 0:
                pos_hist[date] = pos_hist.get(date, 0) + 1
    dates = sorted(n_ind)
    return {
        "dates": dates,
        "pct": [round(pos_dif.get(d, 0) / n_ind[d] * 100.0, 1) for d in dates],
        "pctHist": [round(pos_hist.get(d, 0) / n_ind[d] * 100.0, 1) for d in dates],
        "n": [n_ind[d] for d in dates],
    }


def _check_pct(values: List[Optional[float]], what: str) -> None:
    for v in values:
        if v is not None and not (0.0 <= v <= 100.0):
            raise PanelError(f"{what} 出现 {v}，超出 [0, 100]")


def _self_check(data: Dict[str, Any], allow_partial: bool) -> None:
    daily, monthly, ind = data["daily"], data["monthly"], data["industryDaily"]
    _check_pct(daily["pct250"], "pct250")
    _check_pct(daily["pct50"], "pct50")
    _check_pct(ind["pct"], "行业 MACD 占比")
    _check_pct(ind["pctHist"], "行业 MACD 柱占比")
    _check_pct(monthly["pctIndustryMacdPos"], "月度行业 MACD 占比")
    compute.assert_ascending(daily["dates"], "日度日期")
    if not allow_partial:
        if daily["dates"][0] > FIRST_DAILY_BY:
            raise PanelError(f"日度序列首日 {daily['dates'][0]} 晚于 {FIRST_DAILY_BY}：全市场日线库历史不完整")
        early_max = max(n for d, n in zip(daily["dates"], daily["n"]) if d <= FIRST_DAILY_BY)
        if early_max < MIN_N250_FIRST:
            raise PanelError(f"{FIRST_DAILY_BY} 之前分母最多只有 {early_max} 只（< {MIN_N250_FIRST}）：2004 年起的历史不完整")
        if daily["n"][-1] < MIN_N250_LATEST:
            raise PanelError(f"最新一日 {daily['dates'][-1]} 分母只有 {daily['n'][-1]} 只（< {MIN_N250_LATEST}）：库不完整")
    if data["latest"]["date"] != daily["dates"][-1]:
        raise PanelError("latest.date 与日度末日不一致")
    compute.assert_ascending(monthly["dates"], "月度日期")
    months = [compute.month_key(d) for d in monthly["dates"]]
    if len(set(months)) != len(months):
        raise PanelError("月度序列有重复月份")
    if months[0] > FIRST_MONTH_BY:
        raise PanelError(f"月度行业序列首月 {months[0]} 晚于 {FIRST_MONTH_BY}：申万一级历史回填不完整")
    if monthly["nIndustries"][-1] != INDUSTRY_COUNT:
        raise PanelError(f"最新月份只有 {monthly['nIndustries'][-1]} 个行业有 MACD（应为 {INDUSTRY_COUNT}）")
    if ind["dates"][-1] != monthly["dates"][-1]:
        raise PanelError("行业日度末日与月度末日不一致")


def compute_panel(agg: Dict[str, list], l1: Dict[str, Any], names: Dict[str, str], csi: Dict[str, Any],
                  store_status: Dict[str, Any], allow_partial: bool = False) -> Dict[str, Any]:
    """agg = market_state 聚合；l1 = sws.get_level_history(LEVEL1)；names = 31 个一级 {code: name}；
    csi = csindex_hist.get_index_history("000985")；store_status = MarketStore.status() + nListedNow → data。"""
    if len(names) != INDUSTRY_COUNT:
        raise PanelError(f"申万一级行业应为 {INDUSTRY_COUNT} 个，拿到 {len(names)} 个")
    daily = _daily(agg)
    ind = _industry(l1, names)
    me_dates, me_pct = compute.month_end_sample(ind["dates"], ind["pct"])
    idx = {d: i for i, d in enumerate(ind["dates"])}
    monthly = {
        "dates": me_dates,
        "pctIndustryMacdPos": me_pct,
        "pctHist": [ind["pctHist"][idx[d]] for d in me_dates],
        "nIndustries": [ind["n"][idx[d]] for d in me_dates],
        "partialLast": not compute.is_last_weekday_of_month(me_dates[-1]),
    }
    industry_daily = {k: ind[k][-INDUSTRY_DAILY_DAYS:] for k in ("dates", "pct", "pctHist")}
    close_by_date = dict(zip(csi.get("dates") or [], csi.get("close") or []))
    csi_all = [None if close_by_date.get(d) is None else round(float(close_by_date[d]), 2) for d in daily["dates"]]
    latest = {
        "date": daily["dates"][-1],
        "pct250": daily["pct250"][-1],
        "pct50": daily["pct50"][-1],
        "n": daily["n"][-1],
        "above250": daily["above250"][-1],
        "industryPct": industry_daily["pct"][-1],
        "industryPctHist": industry_daily["pctHist"][-1],
        "industryN": ind["n"][-1],
        "industryDate": industry_daily["dates"][-1],
    }
    universe = {
        "source": UNIVERSE_SOURCE,
        "start": market_state.HISTORY_START,
        "nStocksNow": store_status.get("nListedNow"),
        "nStocksEver": store_status.get("nCodes"),
        "storeLastDate": store_status.get("lastDate") or store_status.get("aggLastDate"),
    }
    data = {
        "daily": daily,
        "monthly": monthly,
        "industryDaily": industry_daily,
        "overlay": {"dates": list(daily["dates"]), "csiAll": csi_all},
        "latest": latest,
        "universe": universe,
    }
    _self_check(data, allow_partial)
    return data


# --------------------------------------------------------------------------- I/O
def store_status_with_universe(store: market_state.MarketStore) -> Dict[str, Any]:
    """``store.status()`` 外加 ``nListedNow``（清单里 status == "1" 且未退市的代码数）。"""
    status = store.status()
    codes = (store.load_manifest() or {}).get("codes") or {}
    status["nListedNow"] = sum(1 for c in codes.values() if str(c.get("status")) == "1" and not (c.get("out") or ""))
    return status


def lagging_days(store: market_state.MarketStore, update: Dict[str, Any]) -> int:
    """库落后的交易日数：日历里 (lastDate, today] 内、按 16:00 规则应已入库的日子。"""
    cal = (store.load_calendar() or {}).get("dates") or []
    last = update.get("lastDate") or ""
    today = update.get("today") or today_str()
    closed = market_closed()
    return sum(1 for d in cal if last < d <= today and (d < today or closed))


def load_market(store: market_state.MarketStore) -> tuple:
    """面板共用的取库流程 → (update_result | None, agg, status)。

    ``ASHARE_MARKET_ALLOW_PARTIAL=1`` 时不联网、不检查 coldDone（开发模式）；否则先 ``update_incremental``
    （每进程只做一次，trend 与 feargreed 共用结果）。``agg.json`` 缺失 → PanelError。
    """
    allow_partial = env_flag(market_state.ENV_ALLOW_PARTIAL)
    update: Optional[Dict[str, Any]] = None
    if allow_partial:
        log.warning("%s=1：跳过全市场日线库的联网更新与 coldDone 检查（开发模式，数值只覆盖已入库的股票）",
                    market_state.ENV_ALLOW_PARTIAL)
    else:
        update = market_state.update_incremental(market_state.budget_from_env())
    agg = store.load_agg()
    if not agg["dates"]:
        raise PanelError("agg.json 不存在：先运行 python -m etl.market_state --rebuild")
    status = store_status_with_universe(store)
    return update, agg, status


def market_notes(store: market_state.MarketStore, update: Optional[Dict[str, Any]], status: Dict[str, Any]) -> List[str]:
    """库落后 / 开发模式的提示句（两块面板共用）。"""
    notes: List[str] = []
    if update and update.get("lagging"):
        n_lag = lagging_days(store, update)
        notes.append(f"全市场日线库落后 {n_lag} 个交易日（预算限制），下次运行继续补齐；当前数据截至 {update.get('lastDate')}。")
    if env_flag(market_state.ENV_ALLOW_PARTIAL):
        notes.append(f"开发模式：部分股票（日线库已入库 {status.get('nStocks')}/{status.get('nCodes')} 只，"
                     f"聚合截至 {status.get('aggLastDate')}），数值不代表全市场。")
    return notes


def build() -> dict:
    store = market_state.MarketStore()
    update, agg, status = load_market(store)
    l1 = sws.get_level_history(sws.LEVEL1)
    names = dict(sws.get_index_list(sws.LEVEL1))
    csi = csindex_hist.get_index_history(CSI_ALL)
    allow_partial = env_flag(market_state.ENV_ALLOW_PARTIAL)
    data = compute_panel(agg, l1, names, csi, status, allow_partial)
    lt = data["latest"]
    log.info("趋势温度: 日度 %s 日 %s → %s，站上 MA250 %s%%（%s/%s），MA50 %s%%；行业 MACD>0 %s%%（柱>0 %s%%，%d/%d，%s）；"
             "月度 %d 点 %s → %s%s；上市 %s 只",
             format(len(data["daily"]["dates"]), ","), data["daily"]["dates"][0], lt["date"], lt["pct250"],
             format(lt["above250"], ","), format(lt["n"], ","), lt["pct50"], lt["industryPct"], lt["industryPctHist"],
             round(lt["industryPct"] * lt["industryN"] / 100.0), lt["industryN"], lt["industryDate"],
             len(data["monthly"]["dates"]), data["monthly"]["dates"][0], data["monthly"]["dates"][-1],
             "（当月未结束）" if data["monthly"]["partialLast"] else "", data["universe"]["nStocksNow"])
    notes = [
        "统计范围为沪深两市全部 A 股（Baostock 覆盖范围：沪市 60 / 68 开头、深市 00 / 30 开头，含已退市公司在上市期间的历史，"
        "不含北交所与 B 股），日线自 2004-01-01 起，用后复权收盘，含 ST 股。",
        "MA250 = 该股最近 250 个有成交交易日的后复权收盘均值（约 50 周线）。某日的分母为当日有成交且截至当日已有 ≥ 250 个"
        "交易日记录的股票数：停牌日不计入，新股满 250 个交易日前不进分母，上市首日不计涨跌；MA50 口径相同。"
        f"日度序列从分母首次达到 {MIN_N250} 只的交易日起（2005 年 1 月中旬；头几天只有几百只，因为 2004 年有过停牌日的股票"
        "要晚几天才满 250 个交易日，随后升到约 1,300 只，2026 年约 5,000 只）。",
        "行业部分用申万 2021 版 31 个一级行业：对各行业自身的日收盘序列算 MACD(12,26,9)（EMA 以首值为种子，前 34 日为预热期），"
        "DIF > 0 记为趋势为正，正数行业占比即图中数值；MACD 柱（2 × (DIF − DEA)）> 0 的占比另存为对照序列。"
        "月度点取每月最后一个交易日，当月取最新可用日并标记为未结束；行业数随各指数起点变化（2000 年初 16 个、2014 年起 27 个、"
        "2022 年起 31 个），占比的分母是当日有 MACD 值的行业数。",
        "煤炭（801950）在申万接口里 2021-12-13 之前的行属于另一只指数，不进入该行业的 MACD 序列。",
        "中证全指（000985）收盘为叠加参考线，按日期对齐到日度序列。",
        "Baostock 在每个交易日 17:30–18:00 左右发布当日全市场数据，20:30 的自动更新纳入当日，07:30 的再次运行作为兜底；"
        "申万行业收盘与中证全指在当日 18:30 前后发布。库里缓存按交易日增量追加，每次运行只补缺失的日子。",
    ] + market_notes(store, update, status)
    return {
        "asOf": lt["date"],
        "sources": [
            "Baostock baostock.com（沪深A股 全市场日线：后复权收盘、涨跌幅、交易状态、ST 标记）",
            "申万宏源研究 swsresearch.com（申万一级行业日收盘）",
            "中证指数官网 csindex.com.cn（中证全指 000985 日收盘，叠加线）",
        ],
        "notes": notes,
        "data": data,
    }
