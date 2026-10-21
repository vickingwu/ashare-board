# -*- coding: utf-8 -*-
"""数据源：申万宏源研究 ``www.swsresearch.com`` 申万指数 — 指数列表 / 指数分析日报表 / 日线 trend。

三个接口（2026-10-19 探测，m2_work/probe_sources.txt §0–§4）：
1. 列表 ``GET /institute-sw/api/index_publish/current/?page=1&page_size=200&indextype=一级行业``
   → ``{"code":"200","data":{"count","results":[{"swindexcode","swindexname",...}]}}``；
   一级行业恰好 31 个（申万 2021 版）。二级行业这里只有 124 个，分析接口与乐咕都是 131 个，
   所以二级的集合以分析接口返回为准，列表接口只用来拿 31 个一级的权威名单。
2. 分析日报表 ``GET /institute-sw/api/index_analysis/index_analysis_report/?page=1&page_size=10000
   &index_type=一级行业&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&type=DAY&swindexcode=all``
   → 每行 ``swindexcode, swindexname, bargaindate("YYYY-MM-DDT08:00:00+08:00"，取前 10 位),
   closeindex, bargainsumrate(成交额占全部 A 股 %), negotiablessharesum1(流通市值 亿元),
   turnoverrate(申万口径换手率 %)`` — **所有数值都是字符串**，缺失为 null；行序为日期降序。
   ``page_size=10000`` 有效（全部 A 指 6,464 行一页返回，2.1 MB，约 25 s）；单次请求 5–26 s，
   偶发 502 由 ``http_get`` 重试。行业收盘价一律来自此接口（二级的 trend 序列可能早已停更，如 801011）。
3. 日线 ``GET /institute-sw/api/index_publish/trend/?swindexcode=801003&period=DAY`` → 全历史浮点数组，
   没有日期区间参数（每次 1.5 MB）。只对 801003 申万Ａ指 使用：``bargainsum`` = 成交额（亿元），
   ``bargainamount`` = 成交量（亿股）。第 0 行 1999-12-30 成交额为 0，调用方丢弃 amount ≤ 0 的行。

口径与坑：
* 申万Ａ指 = 沪深两市全部 A 股（不含北交所、B 股）；其 ``negotiablessharesum1``（流通市值）在
  2011-01-04 → 2012-04-16 共 307 日为 null，是申万自身的数据空档，**不得填补**；
* 申万 ``turnoverrate`` 不是 成交额/流通市值，换手率由面板自算，此字段只作参考；
* 同一代码可能被申万复用：煤炭（801950）在分析接口里 2021-12-13 之前的行属于另一只小指数（见 ``SERIES_START``），
  用到行业收盘序列的面板（swheat、trend）一律先经 ``cut_series_start`` 截掉起点之前的行；
* 申万 T+1 发布且偶有缺日（2026-10-15 缺失），因此增量窗口取「最后缓存日 − 30 天」让迟到的日期得以补入，
  且不与中证交易日历对账；
* 服务器只发叶子证书，依赖 ``etl/certs/`` 里的中间证书（见 common.ca_bundle_path）。

缓存 ``cache/sws/``：
* ``analysis_l1.json`` / ``analysis_l2.json`` / ``analysis_801003.json`` =
  ``{level, code, fetchedAt, dates[], codes:{code:{name, close[], amtShare[], mv[], turnoverSw[]}}}``，
  一份文件共用一条 ``dates[]``，某指数某日无行时四个数组该位为 null；
  一级从 1999-12-30 起按 440 天窗口回填（约 23 次请求，一次性），二级从 今天 − 900 天 起按 100 天窗口回填，
  之后每天一个增量窗口；
* ``trend_801003.json`` = ``{code, fetchedAt, dates[], close[], amount[], volume[]}``，不新鲜时整份重下；
* ``list_一级行业.json`` = 当日列表。同日复用遵循 ``cache_is_fresh``；今天的行在 16:00 前丢弃。
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import compute
from etl.common import (PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now,
                        market_closed, today_str)

log = logging.getLogger("etl.sws")

HOST = "https://www.swsresearch.com"
LIST_URL = HOST + "/institute-sw/api/index_publish/current/"
TREND_URL = HOST + "/institute-sw/api/index_publish/trend/"
ANALYSIS_URL = HOST + "/institute-sw/api/index_analysis/index_analysis_report/"
REFERER_LIST = HOST + "/institute_sw/allIndex/releasedIndex"
REFERER_ANALYSIS = HOST + "/institute_sw/allIndex/analysisIndex"
ACCEPT_JSON = "application/json, text/plain, */*"

LEVEL1 = "一级行业"
LEVEL2 = "二级行业"
MARKET = "市场表征"
A_INDEX = "801003"
A_INDEX_NAME = "申万Ａ指"
LEVEL1_COUNT = 31

PAGE_SIZE = 10000
LIST_PAGE_SIZE = 200
MAX_PAGES = 10
TIMEOUT = 180
CACHE_SUB = "sws"
L1_START = "1999-12-30"
L2_BACKFILL_DAYS = 900
WINDOW_DAYS = {LEVEL1: 440, LEVEL2: 100, MARKET: 10000}
INCREMENTAL_DAYS = 30
LEVEL_FILE = {LEVEL1: "analysis_l1.json", LEVEL2: "analysis_l2.json"}
A_INDEX_FILE = "analysis_801003.json"

FIELDS = ("close", "amtShare", "mv", "turnoverSw")
TrendRow = Tuple[str, float, float, float]   # (date, close, amount 亿元, volume 亿股)

# 按代码截掉起点之前的行：接口在这个日期之前用同一代码返回的是另一只指数。
# 801950 煤炭：2014-02-21 → 2017-01-20 的行流通市值只有 231.56 → 417.51 亿元、成交额占比 0.26 → 0.11 %，
# 2017-01-23 → 2021-12-10 无记录，2021-12-13 重新开始时流通市值 3416.68 亿元（同日 801960 石油石化也从这里起算）。
# 前一段不是煤炭一级行业，不能标成煤炭的 2015 / 2016 年涨跌幅，也不能进煤炭的 MACD 序列。
SERIES_START: Dict[str, str] = {"801950": "2021-12-13"}


def cut_series_start(code: str, dates: List[str], values: list) -> Tuple[List[str], list]:
    """丢掉 ``SERIES_START[code]`` 之前的行 → (dates, values)；代码不在表里时原样返回两个新列表。纯函数。"""
    start = SERIES_START.get(code)
    if not start:
        return list(dates), list(values)
    kept = [(d, v) for d, v in zip(dates, values) if d >= start]
    return [d for d, _ in kept], [v for _, v in kept]


# --------------------------------------------------------------------------- 解析（纯函数）
def _num(s: Any) -> Optional[float]:
    """接口的数值都是字符串；None / "" / 非数字 → None。"""
    if s is None or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _as_json(body: Any, what: str) -> Dict[str, Any]:
    """bytes/str → dict（已是 dict 则原样返回）；非 JSON 或 code != 200 抛 PanelError。"""
    if isinstance(body, dict):
        j = body
    else:
        try:
            text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
            j = json.loads(text)
        except (UnicodeDecodeError, ValueError) as e:
            snippet = body[:120] if isinstance(body, (bytes, bytearray)) else str(body)[:120]
            raise PanelError(f"申万{what}返回的不是 JSON: {type(e).__name__}: {snippet!r}")
    if not isinstance(j, dict) or str(j.get("code")) != "200":
        raise PanelError(f"申万{what}返回异常: {str(j)[:200]}")
    return j


def parse_list(body: Any) -> List[Tuple[str, str]]:
    """列表接口 → [(code, name)]，保持接口顺序。"""
    j = _as_json(body, "指数列表")
    data = j.get("data") or {}
    out: List[Tuple[str, str]] = []
    for r in data.get("results") or []:
        code = str(r.get("swindexcode") or "").strip()
        name = str(r.get("swindexname") or "").strip()
        if code:
            out.append((code, name))
    return out


def parse_trend(body: Any) -> List[TrendRow]:
    """trend 接口 → [(date, close, amount, volume)] 升序、按日期去重（后者覆盖）；close ≤ 0 的行丢弃。

    amount（bargainsum，亿元）与 volume（bargainamount，亿股）保留 4 位小数；
    amount 为 0 的行（基日）保留，由调用方决定是否丢弃。
    """
    j = _as_json(body, "日线")
    data = j.get("data")
    if not isinstance(data, list):
        raise PanelError(f"申万日线缺少 data 数组: {str(j)[:200]}")
    by_date: Dict[str, TrendRow] = {}
    for r in data:
        d = str(r.get("bargaindate") or "")[:10]
        close = _num(r.get("closeindex"))
        if len(d) != 10 or close is None or close <= 0:
            continue
        amount = _num(r.get("bargainsum"))
        volume = _num(r.get("bargainamount"))
        by_date[d] = (d, close, round(amount, 4) if amount is not None else 0.0,
                      round(volume, 4) if volume is not None else 0.0)
    return [by_date[d] for d in sorted(by_date)]


def parse_analysis(body: Any) -> Tuple[int, List[Dict[str, Any]]]:
    """分析接口 → (count, rows)；row = {code, name, date, close, amtShare, mv, turnoverSw}（数值 float|None）。"""
    j = _as_json(body, "指数分析")
    data = j.get("data") or {}
    results = data.get("results")
    if not isinstance(results, list):
        raise PanelError(f"申万指数分析缺少 results: {str(j)[:200]}")
    count = data.get("count")
    count = int(count) if isinstance(count, (int, float)) or (isinstance(count, str) and count.isdigit()) else len(results)
    rows: List[Dict[str, Any]] = []
    for r in results:
        code = str(r.get("swindexcode") or "").strip()
        d = str(r.get("bargaindate") or "")[:10]
        if not code or len(d) != 10:
            continue
        rows.append({
            "code": code,
            "name": str(r.get("swindexname") or "").strip(),
            "date": d,
            "close": _num(r.get("closeindex")),
            "amtShare": _num(r.get("bargainsumrate")),
            "mv": _num(r.get("negotiablessharesum1")),
            "turnoverSw": _num(r.get("turnoverrate")),
        })
    return count, rows


def _merge_rows(cached: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]
                ) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """把分析行并进缓存：日期取并集并升序，各指数四个数组与 dates 对齐（缺日为 None），同日新值覆盖旧值。

    返回 (dates, codes)，codes = {code: {name, close[], amtShare[], mv[], turnoverSw[]}}。纯函数。
    """
    names: Dict[str, str] = {}
    cells: Dict[str, Dict[str, Tuple]] = {}
    if cached:
        old_dates = cached.get("dates") or []
        for code, s in (cached.get("codes") or {}).items():
            names[code] = s.get("name") or ""
            m = cells.setdefault(code, {})
            arrays = [s.get(f) or [] for f in FIELDS]
            for i, d in enumerate(old_dates):
                cell = tuple(a[i] if i < len(a) else None for a in arrays)
                if any(v is not None for v in cell):
                    m[d] = cell
    for r in rows:
        code = r["code"]
        if r.get("name"):
            names[code] = r["name"]
        names.setdefault(code, "")
        cells.setdefault(code, {})[r["date"]] = (r["close"], r["amtShare"], r["mv"], r["turnoverSw"])
    dates = sorted({d for m in cells.values() for d in m})
    codes: Dict[str, Dict[str, Any]] = {}
    for code in sorted(cells):
        m = cells[code]
        entry: Dict[str, Any] = {"name": names.get(code, "")}
        for k, f in enumerate(FIELDS):
            entry[f] = [m[d][k] if d in m else None for d in dates]
        codes[code] = entry
    return dates, codes


# --------------------------------------------------------------------------- I/O
def _get_json(url: str, params: Dict[str, Any], referer: str, what: str = "接口") -> Dict[str, Any]:
    full = url + "?" + urlencode(params)
    body, _hdr = http_get(full, timeout=TIMEOUT, headers={"Referer": referer, "Accept": ACCEPT_JSON})
    j = _as_json(body, what)
    log.debug("申万 %s %d 字节 %s", what, len(body), full)
    return j


def get_index_list(index_type: str, use_cache: bool = True) -> List[Tuple[str, str]]:
    """某类指数的 [(code, name)]（接口顺序）。一级行业必须恰好 31 个，否则 PanelError。当日缓存。"""
    key = f"list_{index_type}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cached.get("items") and cache_is_fresh(cached.get("fetchedAt")):
        items = [(str(c), str(n)) for c, n in cached["items"]]
        log.info("申万列表 %s: 缓存命中 %d 个", index_type, len(items))
        return items
    j = _get_json(LIST_URL, {"page": 1, "page_size": LIST_PAGE_SIZE, "indextype": index_type}, REFERER_LIST, "指数列表")
    items = parse_list(j)
    if index_type == LEVEL1 and len(items) != LEVEL1_COUNT:
        raise PanelError(f"申万一级行业应为 {LEVEL1_COUNT} 个，接口返回 {len(items)} 个")
    if not items:
        raise PanelError(f"申万列表 {index_type} 为空")
    cache_save(CACHE_SUB, key, {"indexType": index_type, "fetchedAt": iso_now(), "items": [list(t) for t in items]})
    log.info("申万列表 %s: 下载 %d 个", index_type, len(items))
    return items


def fetch_analysis_rows(index_type: str, code: str, start: str, end: str) -> List[Dict[str, Any]]:
    """一个日期窗口的分析行；``count > 已取行数`` 时翻页（最多 MAX_PAGES 页）。"""
    rows: List[Dict[str, Any]] = []
    page = 1
    while True:
        j = _get_json(ANALYSIS_URL, {
            "page": page, "page_size": PAGE_SIZE, "index_type": index_type,
            "start_date": start, "end_date": end, "type": "DAY", "swindexcode": code,
        }, REFERER_ANALYSIS, "指数分析")
        count, got = parse_analysis(j)
        rows.extend(got)
        if not got or len(rows) >= count or page >= MAX_PAGES:
            if len(rows) < count:
                log.warning("申万分析 %s %s→%s: count=%d 但只取到 %d 行", index_type, start, end, count, len(rows))
            break
        page += 1
    return rows


def _windows(start: str, end: str, days: int) -> List[Tuple[str, str]]:
    """把 [start, end] 切成每段 ``days`` 个自然日的闭区间。"""
    out: List[Tuple[str, str]] = []
    s = compute.parse_date(start)
    e = compute.parse_date(end)
    while s <= e:
        w_end = min(s + timedelta(days=days - 1), e)
        out.append((s.isoformat(), w_end.isoformat()))
        s = w_end + timedelta(days=1)
    return out


def fetch_analysis_range(index_type: str, code: str, start: str, end: str) -> List[Dict[str, Any]]:
    """按 ``WINDOW_DAYS[index_type]`` 切窗口依次抓取并拼接。"""
    rows: List[Dict[str, Any]] = []
    for w_start, w_end in _windows(start, end, WINDOW_DAYS.get(index_type, 440)):
        got = fetch_analysis_rows(index_type, code, w_start, w_end)
        log.info("申万分析 %s %s→%s: %s 行", index_type, w_start, w_end, format(len(got), ","))
        rows.extend(got)
    return rows


def _validate_history(label: str, dates: List[str], codes: Dict[str, Dict[str, Any]]) -> None:
    if not dates:
        raise PanelError(f"申万 {label} 没有任何数据")
    compute.assert_ascending(dates, f"申万 {label} 日期")
    for code, s in codes.items():
        bad = [d for d, v in zip(dates, s["close"]) if v is not None and v <= 0]
        if bad:
            raise PanelError(f"申万 {label} {code} 有 {len(bad)} 个非正收盘值，首个 {bad[0]}")
        for f in FIELDS:
            if len(s[f]) != len(dates):
                raise PanelError(f"申万 {label} {code}.{f} 长度 {len(s[f])} ≠ 日期数 {len(dates)}")


def _analysis_history(index_type: str, code: str, fname: str, backfill_start: str, label: str,
                      use_cache: bool = True) -> Dict[str, Any]:
    """分析接口的增量全历史缓存（一级 / 二级 / 单个指数共用）。"""
    cached = cache_load(CACHE_SUB, fname) if use_cache else None
    if cached and cached.get("dates"):
        if cache_is_fresh(cached.get("fetchedAt")):
            log.info("申万 %s: 缓存命中 %d 日 × %d 个指数，截至 %s", label, len(cached["dates"]),
                     len(cached.get("codes") or {}), cached["dates"][-1])
            return cached
        last = cached["dates"][-1]
        start = (compute.parse_date(last) - timedelta(days=INCREMENTAL_DAYS)).isoformat()
        incremental = True
    else:
        cached = None
        start = backfill_start
        incremental = False

    today = today_str()
    rows = fetch_analysis_range(index_type, code, start, today)
    if not rows:
        if not incremental:
            raise PanelError(f"申万 {label} 全量抓取 {start}→{today} 返回空（接口变化或参数不对）")
        log.warning("申万 %s: 增量区间 %s 起无新数据，沿用缓存", label, start)

    dates, codes = _merge_rows(cached, rows)
    if dates and dates[-1] == today and not market_closed():
        dates = dates[:-1]
        for s in codes.values():
            for f in FIELDS:
                s[f] = s[f][:-1]
    _validate_history(label, dates, codes)

    result = {"level": index_type, "code": code, "fetchedAt": iso_now(), "dates": dates, "codes": codes}
    cache_save(CACHE_SUB, fname, result)
    added = len(dates) - len(cached["dates"]) if cached else len(dates)
    log.info("申万 %s: 新增 %d 个交易日，共 %d 日 × %d 个指数，截至 %s", label, added, len(dates), len(codes), dates[-1])
    return result


def get_level_history(index_type: str, use_cache: bool = True) -> Dict[str, Any]:
    """某级行业全部指数的日度分析数据 → {level, code, fetchedAt, dates[], codes:{code:{name, close[], amtShare[], mv[], turnoverSw[]}}}。

    一级从 L1_START 回填；二级从 今天 − L2_BACKFILL_DAYS 回填；之后每次只抓 [最后缓存日 − 30 天, 今天]。
    """
    if index_type not in LEVEL_FILE:
        raise PanelError(f"不支持的申万行业级别: {index_type!r}")
    if index_type == LEVEL1:
        backfill = L1_START
    else:
        backfill = (compute.parse_date(today_str()) - timedelta(days=L2_BACKFILL_DAYS)).isoformat()
    return _analysis_history(index_type, "all", LEVEL_FILE[index_type], backfill, index_type, use_cache)


def get_trend(code: str = A_INDEX, use_cache: bool = True) -> Dict[str, Any]:
    """trend 接口全历史 → {code, fetchedAt, dates[], close[], amount[], volume[]}；amount ≤ 0 的行与收盘前的今天丢弃。"""
    key = f"trend_{code}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cached.get("dates") and cache_is_fresh(cached.get("fetchedAt")):
        log.info("申万日线 %s: 缓存命中 %d 行，截至 %s", code, len(cached["dates"]), cached["dates"][-1])
        return cached
    j = _get_json(TREND_URL, {"swindexcode": code, "period": "DAY"}, REFERER_LIST, "日线")
    rows = [r for r in parse_trend(j) if r[2] > 0]
    if rows and rows[-1][0] == today_str() and not market_closed():
        rows = rows[:-1]
    if not rows:
        raise PanelError(f"申万日线 {code} 没有成交额 > 0 的行")
    result = {
        "code": code,
        "fetchedAt": iso_now(),
        "dates": [r[0] for r in rows],
        "close": [r[1] for r in rows],
        "amount": [r[2] for r in rows],
        "volume": [r[3] for r in rows],
    }
    compute.assert_ascending(result["dates"], f"申万日线 {code} 日期")
    cache_save(CACHE_SUB, key, result)
    log.info("申万日线 %s: 下载 %d 行，%s → %s", code, len(rows), rows[0][0], rows[-1][0])
    return result


def get_a_index(use_cache: bool = True) -> Dict[str, Any]:
    """申万Ａ指 日度：trend 的 成交额/成交量/收盘 + 分析接口的 流通市值/申万换手率，以 trend 日期为主键左连接。

    → {code, name, fetchedAt, dates[], close[], amount[], volume[], mv[], turnoverSw[]}；
    分析接口缺该日时 mv / turnoverSw 为 None（2011-01-04 → 2012-04-16 整段为 None）。
    """
    trend = get_trend(A_INDEX, use_cache)
    an = _analysis_history(MARKET, A_INDEX, A_INDEX_FILE, L1_START, A_INDEX_NAME, use_cache)
    series = (an.get("codes") or {}).get(A_INDEX)
    if not series:
        raise PanelError(f"申万分析 {A_INDEX_NAME} 缓存里没有 {A_INDEX}")
    idx = {d: i for i, d in enumerate(an["dates"])}
    mv: List[Optional[float]] = []
    tsw: List[Optional[float]] = []
    for d in trend["dates"]:
        i = idx.get(d)
        mv.append(series["mv"][i] if i is not None else None)
        tsw.append(series["turnoverSw"][i] if i is not None else None)
    missing = sum(1 for v in mv if v is None)
    log.info("申万Ａ指: 成交额 %s 日；流通市值缺失 %s 日", format(len(mv), ","), format(missing, ","))
    return {
        "code": A_INDEX,
        "name": A_INDEX_NAME,
        "fetchedAt": iso_now(),
        "dates": list(trend["dates"]),
        "close": list(trend["close"]),
        "amount": list(trend["amount"]),
        "volume": list(trend["volume"]),
        "mv": mv,
        "turnoverSw": tsw,
    }
