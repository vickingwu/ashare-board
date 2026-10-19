# -*- coding: utf-8 -*-
"""给面板统一挑选某个指数的「月度 PE-TTM（整体法）」来源。

规则（m1_work/plan.md 决定 0.4-1）：每个指数只用一个来源，不混接。
* 乐咕乐股有历史的指数（``legulegu.KNOWN_CODES``：沪深300 2005-04 起、中证800 2007-01 起等）用乐咕；
* 其它指数（中证全指、红利低波、科创50、中证2000、中证A500）用中证指数官网日行情里的 ``peg``
  （2011 年中起才有值），日度取月末；
* 乐咕失败（HTTP 错误 / 页面没 _csrf / 空数据）时，该指数自动回退到中证 ``peg``，并在 ``sourceNote`` 里说明。

返回统一结构：{code, name, source, sourceNote, fallback, meDates[], mePe[], latestDate, latestPe}。
``meDates/mePe`` 是已完结月份的月末值；``latest*`` 是最近交易日的值（见 compute.split_latest）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from etl import compute
from etl.common import PanelError, _fmt_exc
from etl.sources import csindex_hist, legulegu

log = logging.getLogger("etl.pe_select")

NOTE_LEGULEGU = "PE 源：乐咕乐股 整体法滚动市盈率（月末）"
NOTE_CSINDEX = "PE 源：中证指数官网 滚动市盈率（日度取月末，2011 年起）"
NOTE_FALLBACK = "乐咕不可用，已回退到中证指数官网"


def get_monthly_pe(code: str, name: str, pe_code: Optional[str] = None) -> Dict[str, Any]:
    """按规则取月度 PE；没有任何 PE 历史时抛 PanelError。

    ``pe_code``：从中证取 peg 时实际请求的代码（默认 = code）。全收益与价格序列的 peg 相同，
    面板可以复用已经缓存的全收益序列。
    """
    dates = pe = None
    source = source_note = ""
    fallback = False
    if code in legulegu.KNOWN_CODES:
        try:
            lg = legulegu.get_index_pe(code)
        except PanelError as e:
            log.warning("乐咕 %s %s 失败，回退中证 peg: %s", code, name, _fmt_exc(e))
            lg = None
            fallback = True
        if lg:
            dates, pe = lg["dates"], lg["pe"]
            source, source_note = "legulegu", NOTE_LEGULEGU
        elif not fallback:
            log.warning("乐咕没有 %s %s 的数据，回退中证 peg", code, name)
            fallback = True
    if dates is None:
        hist = csindex_hist.get_index_history(pe_code or code)
        dates, pe = compute.drop_none(hist["dates"], hist["pe"])
        source = "csindex"
        source_note = NOTE_CSINDEX + ("；" + NOTE_FALLBACK if fallback else "")
    me_dates, me_pe, latest_date, latest_pe = compute.split_latest(dates, pe)
    if not me_dates or latest_pe is None:
        raise PanelError(f"{name} {code} 没有可用的 PE 历史（源 {source}）")
    if any(v is None or v <= 0 for v in me_pe) or latest_pe <= 0:
        raise PanelError(f"{name} {code} 的 PE 序列含非正值")
    compute.assert_ascending(me_dates, f"{name} PE 月末日期")
    log.info("%s %s PE: %s，%d 个月末（%s → %s），最新 %s = %.2f%s", code, name, source, len(me_dates),
             me_dates[0], me_dates[-1], latest_date, latest_pe, "（回退）" if fallback else "")
    return {
        "code": code, "name": name, "source": source, "sourceNote": source_note, "fallback": fallback,
        "meDates": me_dates, "mePe": me_pe, "latestDate": latest_date, "latestPe": latest_pe,
    }
