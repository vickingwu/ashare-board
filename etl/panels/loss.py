# -*- coding: utf-8 -*-
"""面板 loss —— 抄底数学题（纯算术，无外部数据）。

行 = 当前价格（以高点 100 为基准），列 = 你的买入价。
单元格 = (现价 − 买入价) ÷ 买入价，即持仓浮亏；现价高于买入价的格子留空（已盈利，不是本题关心的）。
附表：跌掉 d% 之后，回到原价需要上涨 d ÷ (100 − d)。

``asOf`` 是常量（表格定义的日期），不是运行日：表的内容与行情、日期都无关，若 asOf 每天变，``data/loss.json``
与 manifest 就会每天改写，周末 / 节假日的暖缓存空跑也会多出一个「自动更新」提交（2026-10-26 评审发现）。
表格内容若有改动，把 AS_OF 改成改动当天。
"""
from __future__ import annotations

PANEL_ID = "loss"
TITLE = "抄底数学题"
STATIC = True  # 不随行情变化，刷新页不把它算进「数据新鲜度」
AS_OF = "2026-10-16"  # 表格定义（M0）的日期；见模块说明

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
        "asOf": AS_OF,
        "sources": ["纯数学推导，无外部数据"],
        "notes": [
            "把某只股票或指数的高点记为 100：每一行是它现在跌到的位置，每一列是你当初买入的位置。",
            "格子里的数字 =（现价 − 买入价）÷ 买入价，也就是这笔买入目前的浮亏；现价高于买入价的格子不填。",
            "这张表只是算术，和任何具体指数、任何行情数据都无关，所以不需要更新。",
        ],
        "data": compute(),
    }
