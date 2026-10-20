# -*- coding: utf-8 -*-
"""面板 indheat —— 行业交易热度（只出数据，RPS 与 MA20 站上率在浏览器内计算）。

申万 31 个一级 + 131 个二级行业最近 ``DAYS`` 个交易日的收盘，二级带上它所属的一级（来自乐咕行业总览页），
一级另带 ``amtShare``（当日成交额占全部 A 股的 %）。前端据此算 N 日涨幅的同级排名分位（RPS）和
近 250 日收盘高于 20 日均线的天数占比（MA20 站上率），并支持从一级点进二级。

口径与坑：
* 共用一条 ``dates[]`` = 一级历史的最后 ``days`` 个交易日；二级按日期重排到这条轴上，缺日为 ``null``
  （二级只回填了约 900 个自然日，窗口最早的几天可能全部二级为 ``null``，前端要容忍开头的空值）；
* 一级任一序列空值超过 ``MAX_NULL_SHARE`` 直接失败；二级超过的剔除并列入 ``dropped``，剩余不足 ``MIN_LEVEL2`` 失败；
* 二级的上级必须能在父级映射里找到且是 31 个一级之一，否则 ``PanelError``（乐咕页面改版或申万新增行业）；
* 文件大：收盘保留 2 位小数并以紧凑 JSON 输出（``COMPACT_JSON = True``）；若仍超过 ``SIZE_BUDGET`` 字节，
  退到 ``FALLBACK_DAYS`` 并在 notes 里说明。``MIN_DAYS = 520`` 是对一级历史长度的要求（250 日 RPS 需 500 日、
  MA20 站上率需 269 日才能算满 250 列），历史不足直接失败；主动退到 500 日属于体积取舍，不受此限制；
* 申万 T+1 发布且偶有缺日（例 2026-10-15），这里不与其它日历对账。

数据形状：``{days, dates[], level1:[{code, name, closes[], amtShare[]}], level2:[{code, name, parent, closes[]}],
dropped:[{code, name, nullShare}], parentSource, periods[], columns[]}``。``compute_panel()`` 是纯函数。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from etl.common import PanelError
from etl.sources import legulegu, sws

log = logging.getLogger("etl.indheat")

PANEL_ID = "indheat"
TITLE = "行业交易热度"
COMPACT_JSON = True

DAYS = 600
FALLBACK_DAYS = 500
MIN_DAYS = 520
MAX_NULL_SHARE = 0.05
SIZE_BUDGET = 1_000_000
LEVEL1_COUNT = 31
MIN_LEVEL2 = 100
PERIODS = [3, 5, 10, 20, 60, 120, 250]
COLUMNS = [20, 60, 120, 250]
PARENT_SOURCE = "乐咕乐股 申万行业总览"


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return None if x is None else round(x, nd)


def _null_share(values: List[Optional[float]]) -> float:
    return sum(1 for v in values if v is None) / len(values) if values else 1.0


def _reindex(src_dates: List[str], src_vals: List[Optional[float]], dates: List[str]) -> List[Optional[float]]:
    """把 (src_dates, src_vals) 按日期重排到 dates 上；缺日为 None；保留 2 位小数。"""
    by_date = {d: v for d, v in zip(src_dates, src_vals) if v is not None}
    return [_r(by_date.get(d)) for d in dates]


def compute_panel(l1: Dict[str, Any], l2: Dict[str, Any], parent_map: Dict[str, Any], days: int) -> Dict[str, Any]:
    """l1 / l2 = sws.get_level_history 的返回；parent_map = legulegu.get_sw_parent_map 的返回 → data。"""
    all_dates = l1.get("dates") or []
    if len(all_dates) < MIN_DAYS:
        raise PanelError(f"申万一级历史只有 {len(all_dates)} 个交易日（< {MIN_DAYS}），不足以计算 250 日指标")
    dates = list(all_dates[-days:])
    offset = len(all_dates) - len(dates)

    level1: List[Dict[str, Any]] = []
    for code in sorted(l1.get("codes") or {}):
        s = l1["codes"][code]
        closes = [_r(v) for v in s["close"][offset:]]
        share = _null_share(closes)
        if share > MAX_NULL_SHARE:
            raise PanelError(f"一级行业 {s.get('name') or code}（{code}）在最近 {len(dates)} 日里缺 {share:.1%} 的收盘")
        level1.append({
            "code": code,
            "name": s.get("name") or code,
            "closes": closes,
            "amtShare": [_r(v) for v in s["amtShare"][offset:]],
        })
    if len(level1) != LEVEL1_COUNT:
        raise PanelError(f"申万一级行业应为 {LEVEL1_COUNT} 个，拿到 {len(level1)} 个")
    l1_codes = {e["code"] for e in level1}

    l2_dates = l2.get("dates") or []
    parents = parent_map.get("level2") or {}
    level2: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for code in sorted(l2.get("codes") or {}):
        s = l2["codes"][code]
        name = s.get("name") or code
        closes = _reindex(l2_dates, s["close"], dates)
        share = _null_share(closes)
        if share > MAX_NULL_SHARE:
            dropped.append({"code": code, "name": name, "nullShare": round(share * 100, 1)})
            continue
        parent = (parents.get(code) or {}).get("parent")
        if not parent:
            raise PanelError(f"二级行业 {name}（{code}）在父级映射里没有上级行业")
        if parent not in l1_codes:
            raise PanelError(f"二级行业 {name}（{code}）的上级 {parent} 不在 31 个一级行业里")
        level2.append({"code": code, "name": name, "parent": parent, "closes": closes})
    if len(level2) < MIN_LEVEL2:
        raise PanelError(f"可用的二级行业只有 {len(level2)} 个（< {MIN_LEVEL2}），已剔除 {len(dropped)} 个")

    return {
        "days": len(dates),
        "dates": dates,
        "level1": level1,
        "level2": level2,
        "dropped": dropped,
        "parentSource": PARENT_SOURCE,
        "periods": list(PERIODS),
        "columns": list(COLUMNS),
    }


def _compact_size(data: Dict[str, Any]) -> int:
    return len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build() -> dict:
    l1 = sws.get_level_history(sws.LEVEL1)
    l2 = sws.get_level_history(sws.LEVEL2)
    pm = legulegu.get_sw_parent_map()
    data = compute_panel(l1, l2, pm, DAYS)
    notes_extra: List[str] = []
    size = _compact_size(data)
    if size > SIZE_BUDGET:
        log.warning("indheat 紧凑 JSON %s 字节超过预算 %s，退到最近 %d 个交易日", format(size, ","), format(SIZE_BUDGET, ","), FALLBACK_DAYS)
        data = compute_panel(l1, l2, pm, FALLBACK_DAYS)
        size = _compact_size(data)
        notes_extra.append(f"文件体积限制，本次只保留最近 {FALLBACK_DAYS} 个交易日。")
    as_of = data["dates"][-1]
    log.info("行业热度: %d 日 %s → %s，一级 %d，二级 %d（剔除 %d），紧凑 JSON 约 %s 字节", data["days"], data["dates"][0], as_of,
             len(data["level1"]), len(data["level2"]), len(data["dropped"]), format(size, ","))

    notes = [
        "RPS（相对强度分位）：某行业 N 日涨幅在同一级别全部行业中的排名分位，0 = 最弱、100 = 最强，"
        "只在同级（一级之间或二级之间）比较；N 可选 3 / 5 / 10 / 20 / 60 / 120 / 250 个交易日。",
        "MA20 站上率：最近 250 个交易日里收盘价高于自身 20 日均线的天数占比，越高说明趋势越持续；表格可按 20 / 60 / 120 / 250 列显示。",
        "行业为申万 2021 版分类：一级 31 个，二级 131 个（其中 7 个二级未出现在申万的指数列表接口里，但日报表有数据，一并纳入）。"
        "点击一级行业名可穿透到它下属的二级，二级到一级的归属取自乐咕乐股的申万行业总览页。",
        f"只保留最近 {data['days']} 个交易日的收盘（{data['dates'][0]} 起），数据按日增量更新；"
        "申万按 T+1 发布，且个别交易日可能缺失（例如 2026-10-15），缺失日不补、不对齐其它日历。",
        "热力颜色为单色深浅：RPS 越高颜色越深；一级行业鼠标悬停可见当日成交额占全部 A 股的比例。",
    ]
    if data["dropped"]:
        notes.append("以下二级行业在窗口内缺失的收盘超过 5%，本次未纳入："
                     + "、".join(f"{d['name']}（{d['code']}）" for d in data["dropped"]) + "。")
    notes.extend(notes_extra)
    return {
        "asOf": as_of,
        "sources": [
            "申万宏源研究 swsresearch.com（申万指数分析·日报表：一级/二级行业收盘、成交额占比）",
            "乐咕乐股 legulegu.com（申万行业总览：二级→一级 归属）",
        ],
        "notes": notes,
        "data": data,
    }
