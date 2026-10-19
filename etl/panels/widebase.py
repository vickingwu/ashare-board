# -*- coding: utf-8 -*-
"""面板 widebase —— 宽基指数全收益净值。

把主要宽基指数的全收益日线（含股息再投资）放在一张图上比较长期走势。
中证A500 / 中证2000 / 科创50 没有公开的全收益指数，用价格指数代替并标注 ``kind: "price"``。

数据形状（决定 0.4-3）：所有序列共用一条 ``dates[]``（中证各指数交易日历相同），
每条序列的 ``values[]`` 与之等长，起点之前为 ``null``；收盘值保留 2 位小数。
``compute()`` 是纯函数（无 I/O）；``build()`` 负责抓数。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from etl import compute
from etl.common import PanelError
from etl.sources.csindex_hist import get_index_history

log = logging.getLogger("etl.widebase")

PANEL_ID = "widebase"
TITLE = "宽基指数全收益净值"

# (价格指数代码, 全收益指数代码或 None, 名称)
SERIES: List[Tuple[str, Optional[str], str]] = [
    ("000300", "H00300", "沪深300"),
    ("000905", "H00905", "中证500"),
    ("000852", "H00852", "中证1000"),
    ("000906", "H00906", "中证800"),
    ("000985", "H00985", "中证全指"),
    ("H30269", "H20269", "红利低波"),
    ("000510", None, "中证A500"),
    ("932000", None, "中证2000"),
    ("000688", None, "科创50"),
]
MIN_ROWS = 250
WINDOWS = ((10, "cagr10y"), (5, "cagr5y"), (3, "cagr3y"))


def _window_cagr(dates: List[str], values: List[Optional[float]], years: int) -> Optional[float]:
    """以 end − N 年（同月同日）当天或之后的第一个有值日为起点算年化；序列不够长返回 None。"""
    end_i = len(dates) - 1
    target = compute.shift_years(dates[end_i], -years)
    i = compute.first_index_on_or_after(dates, target)
    if i is None or values[i] is None:
        return None
    # 起点必须落在目标日附近：序列首日比目标日晚 30 天以上说明历史不够长
    if (compute.parse_date(dates[i]) - compute.parse_date(target)).days > 30:
        return None
    return compute.cagr(values[i], values[end_i], compute.years_between(dates[i], dates[end_i]))


def compute_panel(histories: List[Dict[str, Any]]) -> Dict[str, Any]:
    """histories: [{code, trCode, name, kind, dates[], close[]}] → data。"""
    if not histories:
        raise PanelError("没有任何指数行情")
    all_dates = sorted({d for h in histories for d in h["dates"]})
    pos = {d: i for i, d in enumerate(all_dates)}
    series_out = []
    summary = []
    for h in histories:
        if len(h["dates"]) < MIN_ROWS:
            raise PanelError(f"{h['name']} 只有 {len(h['dates'])} 行行情，疑似数据不完整")
        compute.assert_ascending(h["dates"], f"{h['name']} 日期")
        values: List[Optional[float]] = [None] * len(all_dates)
        for d, v in zip(h["dates"], h["close"]):
            if v is None or v <= 0:
                raise PanelError(f"{h['name']} {d} 收盘值非正: {v}")
            values[pos[d]] = round(v, 2)
        dd, vv = compute.drop_none(h["dates"], h["close"])
        first_v, last_v = vv[0], vv[-1]
        years_all = compute.years_between(dd[0], dd[-1])
        entry = {
            "code": h["code"], "trCode": h.get("trCode"), "name": h["name"], "kind": h["kind"],
            "start": dd[0], "end": dd[-1], "first": round(first_v, 2), "last": round(last_v, 2),
            "multiple": round(last_v / first_v, 4),
            "years": round(years_all, 2),
            "cagrAll": _r(compute.cagr(first_v, last_v, years_all)),
            "maxDrawdown": _r(compute.max_drawdown(vv)),
        }
        for n, key in WINDOWS:
            entry[key] = _r(_window_cagr(dd, vv, n))
        summary.append(entry)
        series_out.append({"code": h["code"], "trCode": h.get("trCode"), "name": h["name"], "kind": h["kind"],
                           "start": dd[0], "end": dd[-1], "values": values})
    return {"dates": all_dates, "series": series_out, "summary": summary}


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def build() -> dict:
    histories = []
    for code, tr, name in SERIES:
        h = get_index_history(tr or code)
        histories.append({"code": code, "trCode": tr, "name": name, "kind": "tr" if tr else "price",
                          "dates": h["dates"], "close": h["close"]})
        log.info("%s %s（%s）: %d 行，%s → %s", name, tr or code, "全收益" if tr else "价格", len(h["dates"]),
                 h["dates"][0], h["dates"][-1])
    data = compute_panel(histories)
    as_of = data["dates"][-1]
    notes = [
        "全收益指数把成分股分红按除息日再投入指数，价格指数不含分红；长期比较应看全收益。"
        "中证A500、中证2000、科创50 没有公开的全收益指数，图中用价格指数代替，标注「价格」，长期会低估它们的真实回报。",
        "每条线的起点是该指数自己的基日或有数据的首日：沪深300 / 中证500 / 中证1000 / 中证800 / 中证全指 2005-01-04，"
        "红利低波 2005-12-30，中证2000 2013-12-31，科创50 2019-12-31；中证A500 于 2024 年发布，2005–2024 为官方回溯值。",
        "图上每条线在当前时间窗的起点归一为 100，纵轴为对数坐标，相同斜率即相同年化收益；"
        "切换「全部 / 10年 / 5年 / 3年 / 1年」会重新以窗口首日为 100。",
        "表中「累计倍数」与「年化」按各自起点计算，起点不同的指数之间不能直接比大小；10 年 / 5 年 / 3 年年化从同月同日往前推。",
        "创业板指全收益（399606）腾讯接口没有返回数据，暂未纳入。",
        "数据来自中证指数官网的指数日行情接口，缓存在 cache/csindex_hist/ 并按日增量补齐；运行 make update 即可刷新。",
    ]
    lagging = [s["name"] + " " + s["end"] for s in data["summary"] if s["end"] != as_of]
    if lagging:
        notes.append("以下序列最后日期晚于其它指数：" + "，".join(lagging))
    return {
        "asOf": as_of,
        "sources": ["中证指数有限公司 csindex.com.cn（指数日行情：全收益 / 价格）"],
        "notes": notes,
        "data": data,
    }
