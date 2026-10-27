# -*- coding: utf-8 -*-
"""面板 symbols —— 证券代码表（个股走势图面板的离线联想表；不联网）。

口径：全市场日线库清单 ``cache/market/manifest.json``（Baostock 证券资料：沪深全部 A 股，含已退市，约 5,560 只）的
代码与简称，加上一份手工维护的主要指数 / ETF 清单（``EXTRA``）。输出紧凑的「数组的数组」::

    columns = [code, name, kind, market, out]
    rows    = [["600519", "贵州茅台", "stock", "sh", null], ..., ["000300", "沪深300", "index", "sh", null], ...]

``kind`` 为 ``stock`` / ``index`` / ``etf``；``market`` 为 ``sh`` / ``sz`` / ``bj``（清单目前没有北交所代码）；``out`` 是退市日
（在市为 ``null``），已退市的也保留——腾讯行情仍提供它们的历史，前端把它们排在联想列表最后并标「已退市」。没有简称的新股
``name`` 为 ``null``，前端显示代码。

为什么是静态表：腾讯的联想接口（smartbox）不带 CORS 头，页面无法直接请求；每敲一个字就远程查询也不礼貌。于是联想完全
在浏览器里对这张表做前缀 / 子串匹配；不在表里的代码仍可直接输入 6 位数字。

体积预算 ≤ 400 KB（``MAX_BYTES``，构建时断言；实际约 230 KB）；股票数 < ``MIN_STOCKS`` 视为清单不完整而失败。
``asOf`` 取清单的 ``lastDate``：编排器按字母序构建面板，``symbols`` 排在 ``trend``（当天更新日线库的面板）之前，所以同一次
运行里它的 ``asOf`` 可能比日线库最终日期早一个交易日；代码与简称不受影响。``STATIC = True``，不参与页面顶部「数据更新至」。
``932000``（中证2000）腾讯 K 线接口没有数据，故意不在 ``EXTRA`` 里。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

from etl.common import PanelError

PANEL_ID = "symbols"
TITLE = "证券代码表"
STATIC = True
COMPACT_JSON = True
MAX_BYTES = 400_000
MIN_STOCKS = 5_000
COLUMNS = ["code", "name", "kind", "market", "out"]
KIND_ORDER = {"stock": 0, "index": 1, "etf": 2}

# (腾讯代码, 简称, kind)。指数用腾讯的 sh000xxx / sz399xxx 写法；ETF 用上市地前缀。
EXTRA: List[Tuple[str, str, str]] = [
    # ---- 指数
    ("sh000001", "上证指数", "index"),
    ("sz399001", "深证成指", "index"),
    ("sz399006", "创业板指", "index"),
    ("sh000300", "沪深300", "index"),
    ("sh000905", "中证500", "index"),
    ("sh000852", "中证1000", "index"),
    ("sh000985", "中证全指", "index"),
    ("sh000688", "科创50", "index"),
    ("sh000510", "中证A500", "index"),
    ("sh000016", "上证50", "index"),
    ("sh000906", "中证800", "index"),
    ("sh000922", "中证红利", "index"),
    ("sh000015", "红利指数", "index"),
    ("sz399673", "创业板50", "index"),
    ("sz399005", "中小100", "index"),
    ("sz399303", "国证2000", "index"),
    # 932000 中证2000：腾讯 K 线接口返回空（2026-10-27 探测），不列
    # ---- ETF
    ("sh510300", "沪深300ETF", "etf"),
    ("sh510310", "沪深300ETF易方达", "etf"),
    ("sz159919", "沪深300ETF嘉实", "etf"),
    ("sh510500", "中证500ETF", "etf"),
    ("sh512100", "中证1000ETF", "etf"),
    ("sh563300", "中证2000ETF", "etf"),
    ("sz159915", "创业板ETF", "etf"),
    ("sh588000", "科创50ETF", "etf"),
    ("sh510050", "上证50ETF", "etf"),
    ("sh510880", "红利ETF", "etf"),
    ("sh515100", "红利低波100ETF", "etf"),
    ("sh512880", "证券ETF", "etf"),
    ("sh518880", "黄金ETF", "etf"),
    ("sh513100", "纳指ETF", "etf"),
    ("sz159941", "纳指ETF广发", "etf"),
    ("sh513500", "标普500ETF", "etf"),
    ("sh511010", "国债ETF", "etf"),
    ("sh510900", "H股ETF", "etf"),
]


def clean_name(s: Optional[str]) -> Optional[str]:
    """去掉所有空白、全角 ASCII 转半角（"万  科Ａ" → "万科A"）；空字符串 → None。"""
    if s is None:
        return None
    out = "".join(ch for ch in str(s) if not ch.isspace())
    out = "".join(chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch for ch in out)
    return out or None


def market_of(key: str) -> str:
    """``sh600519`` → ``sh``；前缀不是 sh / sz / bj 则报 PanelError。"""
    prefix = key[:2].lower()
    if prefix not in ("sh", "sz", "bj") or len(key) != 8 or not key[2:].isdigit():
        raise PanelError(f"证券代码表：清单里的代码 {key!r} 不是 sh/sz/bj + 6 位数字")
    return prefix


def compute_rows(codes: Dict[str, dict], extra: Sequence[Tuple[str, str, str]] = EXTRA) -> List[list]:
    """清单 codes{key: {name, out, ...}} → 行列表：先全部股票（按 市场、代码 排序），再 EXTRA；``market + code`` 重复即报错。"""
    rows: List[list] = []
    seen = set()
    for key in sorted(codes, key=lambda k: (market_of(k), k[2:])):
        entry = codes[key] or {}
        market = market_of(key)
        code = key[2:]
        if (market, code) in seen:
            raise PanelError(f"证券代码表：代码重复 {market}{code}")
        seen.add((market, code))
        out = (entry.get("out") or "").strip() or None
        rows.append([code, clean_name(entry.get("name")), "stock", market, out])
    for key, name, kind in extra:
        market = market_of(key)
        code = key[2:]
        if kind not in KIND_ORDER:
            raise PanelError(f"证券代码表：EXTRA 里 {key} 的 kind {kind!r} 不合法")
        if (market, code) in seen:
            raise PanelError(f"证券代码表：EXTRA 里 {key} 与清单重复")
        seen.add((market, code))
        rows.append([code, clean_name(name), kind, market, None])
    return rows


def compute(manifest: dict) -> dict:
    codes = manifest.get("codes") or {}
    rows = compute_rows(codes)
    counts = {
        "stock": sum(1 for r in rows if r[2] == "stock"),
        "index": sum(1 for r in rows if r[2] == "index"),
        "etf": sum(1 for r in rows if r[2] == "etf"),
        "delisted": sum(1 for r in rows if r[4]),
        "nameMissing": sum(1 for r in rows if r[1] is None),
    }
    data = {"columns": list(COLUMNS), "rows": rows, "counts": counts, "marketLastDate": manifest.get("lastDate")}
    check_size(data)
    return data


def check_size(data: dict) -> int:
    """紧凑 JSON 字节数 ≤ MAX_BYTES 且股票数 ≥ MIN_STOCKS，否则 PanelError；返回字节数。"""
    n_bytes = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if n_bytes > MAX_BYTES:
        raise PanelError(f"证券代码表 {n_bytes:,} B 超过预算 {MAX_BYTES:,} B")
    stocks = (data.get("counts") or {}).get("stock")
    if stocks is None:
        stocks = sum(1 for r in data.get("rows", []) if r[2] == "stock")
    if stocks < MIN_STOCKS:
        raise PanelError(f"证券代码表只有 {stocks} 只股票（< {MIN_STOCKS}），清单疑似不完整")
    return n_bytes


def build() -> dict:
    from etl import market_state

    manifest = market_state.ensure_store()
    data = compute(manifest)
    c = data["counts"]
    return {
        "asOf": manifest["lastDate"],
        "sources": ["全市场日线库清单（Baostock 证券资料：代码、简称、上市 / 退市日）", "手工维护的主要指数与 ETF 清单"],
        "notes": [
            f"沪深全部 A 股 {c['stock']:,} 只（含已退市 {c['delisted']} 只，腾讯行情仍提供其历史，联想列表里排在最后并标「已退市」）"
            f"，加 {c['index']} 个主要指数与 {c['etf']} 只 ETF；北交所代码不在表里，可直接输入 6 位数字查询。",
            "简称来自 Baostock 证券资料，刚上市、尚无简称的新股显示代码；指数 / ETF 的简称为手工维护。",
            "每次构建从本地全市场日线库的清单重新生成，不发网络请求；「数据截至」是该清单的最后交易日，同一次运行里可能比"
            "日线库最终日期早一个交易日（面板按字母序构建，symbols 在 trend 之前），不影响代码与简称。",
            "中证2000（932000）腾讯日线接口没有数据，未列入指数清单。",
        ],
        "data": data,
    }
