# -*- coding: utf-8 -*-
"""数据源：中证指数官网 指数日行情 ``csindex-home/perf/index-perf``（价格指数与全收益指数通用）。

接口（2026-10-19 探测，m1_work/probe_sources.txt）：
* ``GET ?indexCode={code}&startDate=YYYYMMDD&endDate=YYYYMMDD``，需带 ``Referer``；
  返回 ``{"code":"200","msg":"Success","data":[...],"success":true}``，注意 ``code`` 是字符串；
* 不限区间：从 20050101 到今天一次返回全部（沪深300 约 5,290 行、1.8 MB）；
* 每行 ``tradeDate``（YYYYMMDD）、``close``、``peg``（滚动市盈率，价格与全收益序列上数值相同），
  其它字段不用；
* **第 0 行的日期永远等于 ``startDate``**，哪怕那天不是交易日：此时它是一行「占位」，各字段
  （开高低收、peg）照抄 ``startDate`` 之后第一个交易日的值。实测：全量请求的第 0 行是 ``20050101``
  （周六）、值与 20050104 相同；``startDate=20261001`` / ``20261005``（国庆假期）时第 0 行
  日期就是 10-01 / 10-05、值与 10-08 相同（2026-10-20/21，pages_work/fix_probe_csindex.txt）。
  ``startDate`` 是交易日时第 0 行就是那天的真实行情。``endDate`` 一侧没有这种占位行；
* 只给已收盘的交易日；接口不存在的代码返回 ``data: []`` 而不是错误状态。

占位行的处理（``parse_perf`` 的 ``start`` / ``known_dates``）：周末日期一律丢弃；此外若第 0 行日期
== ``startDate`` 且这一天不在已缓存的日期里，则丢弃——唯一的例外是全量请求（无缓存）且该行收盘价
与下一行不同（说明 ``startDate`` 本身就是真实交易日）。离线无法知道节假日，这条规则不依赖日历。

缓存 ``cache/csindex_hist/{code}.json`` 保存整条历史；增量请求从 **最后缓存日当天** 起到今天——
最后缓存日是已知的真实交易日，接口第 0 行就是它的真实行情，按日期合并时同日新值覆盖旧值，
不会再有落在假期上的 ``startDate``。读缓存时顺手丢掉周末日期的行（自愈）。同一天内按
``cache_is_fresh`` 规则不重复请求。历史上合并进缓存的假日占位行用
``python -m etl.sources.csindex_hist --purge-dates 2026-10-01`` 一次性清掉。

限流（2026-10-23 观察）：2 秒间隔下连续 16 个全史请求（约 45 秒）在第 16 个收到 HTTP 403，几分钟后自愈；
结合 10-21 的「8 秒 12 个 → 403」，实际上限约每分钟 15 个请求。对策：主机最小间隔 3 秒
（``common.HOST_MIN_INTERVAL``），且 ``_request`` 在 ``http_get`` 抛出 ``HTTP 403`` 时暂停 ``RATE_LIMIT_SLEEP``
（90 秒）后**只重试一次**；``http_get`` 本身仍保持「4xx 不重试」。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Collection, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from etl import common, compute
from etl.common import (PanelError, cache_is_fresh, cache_load, cache_save, http_get, iso_now,
                        market_closed, today_compact, today_str)

log = logging.getLogger("etl.csindex_hist")

PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
HEADERS = {"Referer": "https://www.csindex.com.cn/", "Accept": "application/json, text/plain, */*"}
FULL_START = "20050101"
CACHE_SUB = "csindex_hist"
TIMEOUT = 60
# 突发限流（HTTP 403）后的暂停秒数，之后只重试一次；测试里打成 0
RATE_LIMIT_SLEEP = 90

Row = Tuple[str, float, Optional[float]]   # (YYYY-MM-DD, close, pe|None)


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def drop_start_placeholder(rows: List[Row], start: Optional[str], known_dates: Collection[str]) -> List[Row]:
    """丢掉接口为 ``startDate`` 补出来的占位首行。

    规则：``rows``（已升序、已去掉周末）的第 0 行日期 == ``start`` 且该日期不在 ``known_dates``
    （缓存里已有的交易日）时丢弃；例外：``known_dates`` 为空（全量请求）且第 0 行收盘价与第 1 行不同
    ——这说明 ``startDate`` 是真实交易日而不是照抄下一交易日的占位行。``start`` 为 None 时不做处理。
    """
    if not start or not rows or rows[0][0] != _iso(start) or rows[0][0] in known_dates:
        return rows
    if not known_dates and len(rows) >= 2 and rows[0][1] != rows[1][1]:
        return rows
    log.debug("csindex: 丢弃 startDate=%s 的占位行（%s）", start, "值与下一行相同" if len(rows) >= 2 else "单行")
    return rows[1:]


def parse_perf(body: bytes, start: Optional[str] = None, known_dates: Collection[str] = ()) -> Dict[str, Any]:
    """解析接口响应 → {code, name, nameFull, rows:[(date, close, pe)]}。空 data → rows=[]。

    给出请求用的 ``start``（YYYYMMDD）与缓存里已有的 ``known_dates`` 时，按 ``drop_start_placeholder``
    丢掉占位首行；不给则只做周末过滤（与早期版本一致）。
    """
    try:
        j = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PanelError(f"csindex perf 不是 JSON: {type(e).__name__}: {body[:120]!r}")
    if not isinstance(j, dict) or str(j.get("code")) != "200" or not j.get("success"):
        raise PanelError(f"csindex perf 返回异常: {str(j)[:200]}")
    data = j.get("data") or []
    by_date: Dict[str, Tuple[float, Optional[float]]] = {}
    code = name = name_full = ""
    for r in data:
        td = str(r.get("tradeDate") or "")
        if len(td) != 8 or not td.isdigit():
            continue
        d = _iso(td)
        if compute.is_weekend(d):
            continue
        close = r.get("close")
        if close is None or close <= 0:
            continue
        pe = r.get("peg")
        pe = float(pe) if isinstance(pe, (int, float)) and pe > 0 else None
        by_date[d] = (float(close), pe)
        if not code:
            code = str(r.get("indexCode") or "")
            name = str(r.get("indexNameCn") or "")
            name_full = str(r.get("indexNameCnAll") or "")
    rows: List[Row] = [(d, by_date[d][0], by_date[d][1]) for d in sorted(by_date)]
    rows = drop_start_placeholder(rows, start, known_dates)
    return {"code": code, "name": name, "nameFull": name_full, "rows": rows}


def _request(code: str, start: str, end: str, known_dates: Collection[str] = ()) -> Dict[str, Any]:
    url = PERF_URL + "?" + urlencode({"indexCode": code, "startDate": start, "endDate": end})
    try:
        body, _hdr = http_get(url, timeout=TIMEOUT, headers=HEADERS)
    except PanelError as e:
        if "HTTP 403" not in str(e):
            raise
        log.warning("csindex %s: HTTP 403（突发限流），%d s 后重试一次", code, RATE_LIMIT_SLEEP)
        time.sleep(RATE_LIMIT_SLEEP)
        body, _hdr = http_get(url, timeout=TIMEOUT, headers=HEADERS)
    parsed = parse_perf(body, start=start, known_dates=known_dates)
    log.debug("csindex perf %s %s-%s: %d 字节，%d 行", code, start, end, len(body), len(parsed["rows"]))
    return parsed


def _heal_cached(code: str, cached: Dict[str, Any]) -> Dict[str, Any]:
    """读缓存时的自愈：丢掉日期落在周六/周日的行（交易所周末从不开市）。有改动就写回。"""
    keep = [i for i, d in enumerate(cached["dates"]) if not compute.is_weekend(d)]
    if len(keep) == len(cached["dates"]):
        return cached
    dropped = [d for d in cached["dates"] if compute.is_weekend(d)]
    log.warning("csindex %s: 缓存里有 %d 个周末日期的行，已丢弃: %s", code, len(dropped), dropped[:5])
    for k in ("dates", "close", "pe"):
        cached[k] = [cached[k][i] for i in keep]
    cache_save(CACHE_SUB, f"{code}.json", cached)
    return cached


def purge_dates(dates: Collection[str], codes: Optional[Collection[str]] = None) -> Dict[str, int]:
    """一次性清理：从 ``cache/csindex_hist/*.json`` 删除指定日期（YYYY-MM-DD）的行，返回 {文件名: 删除行数}。

    用途：早期版本的增量窗口是「最后缓存日 − 15 天」，``startDate`` 落在工作日假期时会把接口的
    占位行（如 2026-10-01）合并进缓存；离线无法判断节假日，所以按日期显式删除。
    """
    targets = set(dates)
    removed: Dict[str, int] = {}
    for path in sorted((common.CACHE_DIR / CACHE_SUB).glob("*.json")):
        if codes and path.stem not in codes:
            continue
        obj = common.read_json(path, default=None)
        if not obj or not obj.get("dates"):
            continue
        keep = [i for i, d in enumerate(obj["dates"]) if d not in targets]
        n = len(obj["dates"]) - len(keep)
        if n:
            for k in ("dates", "close", "pe"):
                obj[k] = [obj[k][i] for i in keep]
            cache_save(CACHE_SUB, path.name, obj)
        removed[path.name] = n
    return removed


def _validate(code: str, dates: List[str], close: List[float]) -> None:
    if not dates:
        raise PanelError(f"csindex {code} 没有任何行情")
    compute.assert_ascending(dates, f"csindex {code} 日期")
    bad = [d for d, v in zip(dates, close) if v is None or v <= 0]
    if bad:
        raise PanelError(f"csindex {code} 有 {len(bad)} 个非正收盘值，首个 {bad[0]}")


def get_index_history(code: str, use_cache: bool = True) -> Dict[str, Any]:
    """取某指数全历史日线 → {code, name, nameFull, dates[], close[], pe[], start, fetchedAt}。"""
    key = f"{code}.json"
    cached = cache_load(CACHE_SUB, key) if use_cache else None
    if cached and cached.get("dates"):
        cached = _heal_cached(code, cached)
    if cached and cached.get("dates"):
        if cache_is_fresh(cached.get("fetchedAt")):
            log.info("csindex %s: 缓存命中 %d 行，截至 %s", code, len(cached["dates"]), cached["dates"][-1])
            return cached
        # 从最后缓存日当天起：它是已知交易日，接口第 0 行就是它的真实行情，合并时同日去重；
        # 不再往前多取 N 天，避免 startDate 落在工作日假期上拿到占位行
        start = cached["dates"][-1].replace("-", "")
        incremental = True
    else:
        cached = None
        start = FULL_START
        incremental = False

    parsed = _request(code, start, today_compact(), known_dates=set(cached["dates"]) if cached else ())
    new_rows = parsed["rows"]
    if not new_rows:
        if not incremental:
            raise PanelError(f"csindex {code} 返回空数据（代码不存在或接口变化）")
        log.warning("csindex %s: 增量区间 %s 起无新数据，沿用缓存", code, start)

    old_dates = cached["dates"] if cached else []
    old_vals = list(zip(cached["close"], cached["pe"])) if cached else []
    dates, pairs = compute.merge_series(old_dates, old_vals,
                                        [r[0] for r in new_rows], [(r[1], r[2]) for r in new_rows])
    if dates and dates[-1] == today_str() and not market_closed():
        dates, pairs = dates[:-1], pairs[:-1]
    close = [p[0] for p in pairs]
    pe = [p[1] for p in pairs]
    _validate(code, dates, close)

    result = {
        "code": parsed["code"] or (cached or {}).get("code") or code,
        "name": parsed["name"] or (cached or {}).get("name") or code,
        "nameFull": parsed["nameFull"] or (cached or {}).get("nameFull") or code,
        "start": dates[0],
        "fetchedAt": iso_now(),
        "dates": dates,
        "close": close,
        "pe": pe,
    }
    cache_save(CACHE_SUB, key, result)
    added = len(dates) - len(old_dates)
    log.info("csindex %s %s: 新增 %d 行，共 %d 行，截至 %s", code, result["name"], added, len(dates), dates[-1])
    return result


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m etl.sources.csindex_hist --purge-dates 2026-10-01 [2026-10-05] [--codes 000300,H00300]``"""
    import argparse

    ap = argparse.ArgumentParser(description="中证指数日行情缓存的一次性清理")
    ap.add_argument("--purge-dates", nargs="+", required=True, metavar="YYYY-MM-DD",
                    help="从 cache/csindex_hist/*.json 删除这些日期的行（历史上合并进来的假日占位行）")
    ap.add_argument("--codes", default="", help="只处理这些代码（逗号分隔），留空 = 全部缓存文件")
    args = ap.parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or None
    removed = purge_dates(args.purge_dates, codes)
    for name, n in removed.items():
        print(f"{name}: 删除 {n} 行")
    print(f"共 {len(removed)} 个文件，删除 {sum(removed.values())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
