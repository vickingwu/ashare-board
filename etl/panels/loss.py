# -*- coding: utf-8 -*-
"""面板 loss —— 抄底数学题（纯算术，无外部数据）。

行 = 当前价格（以高点 100 为基准），列 = 你的买入价。
单元格 = (现价 − 买入价) ÷ 买入价，即持仓浮亏；现价高于买入价的格子留空（已盈利，不是本题关心的）。
附表：跌掉 d% 之后，回到原价需要上涨 d ÷ (100 − d)。
"""
from __future__ import annotations

from etl.common import today_str

PANEL_ID = "loss"
TITLE = "抄底数学题"
STATIC = True  # 不随行情变化，刷新页不把它算进「数据新鲜度」

CURRENT = [80, 70, 60, 50, 40, 30, 20, 10]
BUY = [100, 80, 70, 60, 50, 40]


def compute() -> dict:
    rows = [{"price": p, "drawdownPct": 100 - p} for p in CURRENT]
    matrix = []
    for cur in CURRENT:
        line = []
        for buy in BUY:
            line.append(round((cur - buy) / buy, 4) if cur <= buy else None)
        matrix.append(line)
    recovery = [
        {"dropPct": d, "gainPct": round(d / (100 - d) * 100, 1)} for d in range(10, 100, 10)
    ]
    return {"rows": rows, "cols": BUY, "matrix": matrix, "recovery": recovery}


def build() -> dict:
    return {
        "asOf": today_str(),
        "sources": ["纯数学推导，无外部数据"],
        "notes": [
            "把某只股票或指数的高点记为 100：每一行是它现在跌到的位置，每一列是你当初买入的位置。",
            "格子里的数字 =（现价 − 买入价）÷ 买入价，也就是这笔买入目前的浮亏；现价高于买入价的格子不填。",
            "这张表只是算术，和任何具体指数、任何行情数据都无关，所以不需要更新。",
        ],
        "data": compute(),
    }
