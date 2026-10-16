# -*- coding: utf-8 -*-
"""数据源：腾讯行情批量接口 ``https://qt.gtimg.cn/q=sh600519,sz000001,...``。

返回 GBK 文本，每行 ``v_sh600519="1~贵州茅台~600519~1315.27~...";``，按 ``~`` 切分共 88 段。
字段位置是经验值，没有官方文档，因此：
* 位置写成常量放在这里，只改一处；
* 每次构建都用三只参照股做量级校验（``verify_field_index``），字段漂移会让面板失败而不是出错图。

2026-10-16 探测证据（m0_work/probe_sources.txt）：
    贵州茅台 [44]=16441.95 [45]=16441.95   （全流通，两者相等）
    工商银行 [44]=22647.43 [45]=29938.13   （有 H 股，A 股流通市值 < 总市值 → [45] 是总市值）
    宁德时代 [44]=11669.83 [45]=12674.04
    [30]=20261016141853 为行情时间 YYYYMMDDHHMMSS
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

from etl.common import PanelError, cache_load, cache_save, http_get, today_compact

log = logging.getLogger("etl.tencent")

QUOTE_URL = "https://qt.gtimg.cn/q={symbols}"
BATCH_SIZE = 60

IDX_NAME = 1
IDX_CODE = 2
IDX_PRICE = 3
IDX_TIME = 30
IDX_MV_FLOAT = 44   # 流通市值（亿元）
IDX_MV_TOTAL = 45   # 总市值（亿元）
MIN_FIELDS = 47

_LINE_RE = re.compile(r'v_(\w+)="([^"]*)"')

# 参照股：(代码, 名称, 总市值下限, 总市值上限, 是否要求 总市值 > 流通市值)
REFERENCE = [
    ("600519", "贵州茅台", 10000.0, 30000.0, False),
    ("601398", "工商银行", 15000.0, 45000.0, True),
    ("300750", "宁德时代", 6000.0, 25000.0, False),
]


def to_symbol(code: str) -> str:
    """6 位代码 → 腾讯 symbol。6 开头沪市，0/3 深市，4/8/9 北交所（920xxx 为 2025 年起的新编码）。

    2026-10-16 实测：中证全指名单含 241 只 920xxx，腾讯以 ``bj920000`` 返回，
    字段数 87（沪深为 88），但 [30]/[44]/[45] 位置一致（m0_work/diag_bj_fields.txt）。
    """
    c = code.strip()
    if len(c) != 6 or not c.isdigit():
        raise ValueError(f"非 6 位数字代码: {code!r}")
    if c[0] == "6":
        return "sh" + c
    if c[0] in "03":
        return "sz" + c
    if c[0] in "489":
        return "bj" + c
    raise ValueError(f"无法判断交易所: {code!r}")


def _f(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _from_fields(fields: List[str]) -> dict:
    return {
        "name": fields[IDX_NAME],
        "price": _f(fields[IDX_PRICE]),
        "mvFloat": _f(fields[IDX_MV_FLOAT]),
        "mvTotal": _f(fields[IDX_MV_TOTAL]),
        "quoteTime": fields[IDX_TIME].strip(),
        "raw": fields,
    }


def parse_quote_text(text: str) -> Dict[str, dict]:
    """解析一批返回文本 → {code: {name, price, mvFloat, mvTotal, quoteTime, raw}}。"""
    out: Dict[str, dict] = {}
    for m in _LINE_RE.finditer(text):
        var, payload = m.group(1), m.group(2)
        if var.startswith("pv_none_match") or not payload:
            continue
        fields = payload.split("~")
        if len(fields) < MIN_FIELDS:
            continue
        code = fields[IDX_CODE].strip()
        if len(code) != 6:
            continue
        out[code] = _from_fields(fields)
    return out


def fetch_quotes(codes: Iterable[str], use_cache: bool = True) -> Dict[str, dict]:
    """批量取行情；当日缓存按 code 保存原始字段列表，缺哪些就只抓哪些，每批落盘一次。"""
    codes = list(dict.fromkeys(c for c in codes if c))
    key = f"quotes_{today_compact()}.json"
    cache: Dict[str, List[str]] = (cache_load("tencent", key) if use_cache else None) or {}

    todo: List[str] = []
    for c in codes:
        if c in cache:
            continue
        try:
            to_symbol(c)
        except ValueError:
            continue
        todo.append(c)

    batches = 0
    for i in range(0, len(todo), BATCH_SIZE):
        chunk = todo[i:i + BATCH_SIZE]
        url = QUOTE_URL.format(symbols=",".join(to_symbol(c) for c in chunk))
        body, _hdr = http_get(url, timeout=30)
        parsed = parse_quote_text(body.decode("gbk", errors="replace"))
        for c in chunk:
            if c in parsed:
                cache[c] = parsed[c]["raw"]
        cache_save("tencent", key, cache)
        batches += 1
        log.debug("腾讯批次 %d: 请求 %d 只，解析 %d 只", batches, len(chunk), len(parsed))

    quotes = {c: _from_fields(cache[c]) for c in codes if c in cache}
    missing = len(codes) - len(quotes)
    log.info("腾讯行情: 新抓 %d 批，共 %d 只有行情，%d 只缺失", batches, len(quotes), missing)
    return quotes


def verify_field_index(quotes: Dict[str, dict]) -> List[str]:
    """用三只参照股校验 [45] 仍是总市值（亿元）。不通过则整个面板失败。"""
    lines: List[str] = []
    problems: List[str] = []
    for code, name, lo, hi, need_gt in REFERENCE:
        q = quotes.get(code)
        tot = q.get("mvTotal") if q else None
        flt = q.get("mvFloat") if q else None
        desc = f"{name} {code} 总市值 {tot} 亿 / 流通 {flt} 亿"
        if tot is None or not (lo <= tot <= hi):
            problems.append(f"{desc}，期望 [{lo:.0f}, {hi:.0f}]")
        elif need_gt and (flt is None or not tot > flt):
            problems.append(f"{desc}，期望 总市值 > 流通市值")
        else:
            lines.append(f"校验 {name} {code} 总市值 {tot:.2f} 亿")
    if problems:
        raise PanelError("腾讯字段位置校验失败: " + "; ".join(problems))
    return lines


def latest_quote_date(quotes: Dict[str, dict]) -> Tuple[str, str]:
    """最新行情时间 → ('YYYY-MM-DD', 'HHMM')。"""
    ts = max((q.get("quoteTime") or "" for q in quotes.values()), default="")
    if len(ts) < 12 or not ts[:12].isdigit():
        raise PanelError(f"行情时间字段异常: {ts!r}")
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", ts[8:12]
