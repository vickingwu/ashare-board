#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源连通性与速度诊断（给 GitHub Actions 的 diagnose-sources workflow 用，本地也能跑）。

用法：python scripts/diagnose_sources.py [--stocks N] [--no-baostock]
    依赖：pip install baostock requests（Baostock 会带上 pandas / numpy）。每一项都单独 try/except，
    任何一项失败都不影响其它项，退出码恒为 0——结论看打印出来的 SUMMARY 段。``--no-baostock`` 跳过第 1 项，
    只跑 HTTP 连通性（workflow 输入 ``http_only``）。

检查项：
1. Baostock（public-api.baostock.com:10030，TCP 长连接）：DNS、TCP 连接、login 耗时；query_trade_dates；
   query_stock_basic 行数；3 只个股 2005 年起的后复权日线（行数、秒）；N 只个股连续查询的平均耗时；
   query_daily_history_k_AStock(日期) 全市场单日横截面（行数、字段、秒）。
2. HTTP：中证指数官网 index-perf、乐咕乐股 行业总览页、申万宏源研究 指数列表、东方财富数据中心 GDP 报表（打印 count）、
   上海黄金交易所 Dailyhq（POST，打印 time 行数）、上交所 每日概况（打印 result 行数），各 1 次请求的状态码与耗时
   （优先走仓库的 etl.common.http_get / http_post，带限速与申万中间证书；导入失败则退回裸 requests）。
不读写 cache/，不写任何文件。
"""
from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUMMARY: list = []


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def note(key: str, value) -> None:
    SUMMARY.append((key, value))
    say(f"{key}: {value}")


def drain(rs) -> list:
    """ResultData → list[list]，不经过 pandas（rs.get_data() 在 pandas ≥ 2 上会因 DataFrame.append 被移除而报错）。"""
    out = []
    if rs is None:
        return out
    while rs.error_code == "0" and rs.next():
        out.append(rs.get_row_data())
    return out


def probe_baostock(n_stocks: int) -> None:
    import importlib.metadata as md

    for pkg in ("baostock", "pandas", "numpy"):
        try:
            note(f"pkg {pkg}", md.version(pkg))
        except md.PackageNotFoundError:
            note(f"pkg {pkg}", "not installed")

    socket.setdefaulttimeout(90)
    import baostock as bs
    import baostock.common.contants as cons

    host, port = cons.BAOSTOCK_SERVER_IP, cons.BAOSTOCK_SERVER_PORT
    t0 = time.monotonic()
    try:
        ip = socket.gethostbyname(host)
        note("bs dns", f"{host} -> {ip} ({time.monotonic() - t0:.2f}s)")
    except Exception as e:  # noqa: BLE001
        note("bs dns", f"FAIL {e!r}")
    t0 = time.monotonic()
    try:
        s = socket.create_connection((host, port), timeout=30)
        s.close()
        note("bs tcp", f"connect ok ({time.monotonic() - t0:.2f}s)")
    except Exception as e:  # noqa: BLE001
        note("bs tcp", f"FAIL after {time.monotonic() - t0:.1f}s: {e!r}")
        return

    t0 = time.monotonic()
    lg = bs.login()
    note("bs login", f"code={lg.error_code} msg={lg.error_msg} ({time.monotonic() - t0:.1f}s)")
    if lg.error_code != "0":
        return

    today = date.today().isoformat()
    try:
        t0 = time.monotonic()
        rs = bs.query_trade_dates(start_date="2005-01-01", end_date=today)
        rows = drain(rs)
        trading = [r[0] for r in rows if len(r) > 1 and r[1] == "1"]
        note("bs trade_dates", f"code={rs.error_code} rows={len(rows)} trading={len(trading)} last={trading[-1] if trading else None} ({time.monotonic() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        note("bs trade_dates", f"EXC {e!r}")

    try:
        t0 = time.monotonic()
        rs = bs.query_stock_basic()
        rows = drain(rs)
        fi = {f: i for i, f in enumerate(rs.fields)}
        a = [r for r in rows if r[fi["type"]] == "1"] if rows else []
        listed = sum(1 for r in a if r[fi["status"]] == "1")
        note("bs stock_basic", f"code={rs.error_code} rows={len(rows)} type1={len(a)} listed={listed} ({time.monotonic() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        note("bs stock_basic", f"EXC {e!r}")

    fields = "date,code,close,preclose,pctChg,turn,tradestatus,isST"
    for code in ("sh.600519", "sz.000001", "sz.300750"):
        try:
            t0 = time.monotonic()
            rs = bs.query_history_k_data_plus(code, fields, start_date="2005-01-01", end_date=today, frequency="d", adjustflag="1")
            rows = drain(rs)
            note(f"bs k {code}", f"code={rs.error_code} msg={rs.error_msg} rows={len(rows)} last={rows[-1] if rows else None} ({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            note(f"bs k {code}", f"EXC {e!r}")

    codes = ["sh.600000", "sh.600036", "sh.601318", "sz.000002", "sz.000858", "sz.002475", "sz.300059", "sh.688981", "sh.603288", "sz.002594",
             "sh.600276", "sh.600900", "sz.000333", "sz.002415", "sh.601012", "sz.300015", "sh.600030", "sz.000568", "sh.601888", "sz.002714"][:n_stocks]
    t_all = time.monotonic()
    per = []
    for c in codes:
        t0 = time.monotonic()
        try:
            rs = bs.query_history_k_data_plus(c, fields, start_date="2005-01-01", end_date=today, frequency="d", adjustflag="1")
            n = len(drain(rs))
            per.append((c, n, round(time.monotonic() - t0, 2), rs.error_code))
        except Exception as e:  # noqa: BLE001
            per.append((c, -1, round(time.monotonic() - t0, 2), repr(e)))
    total = time.monotonic() - t_all
    note("bs k sequential", f"{len(codes)} stocks in {total:.1f}s = {total / max(1, len(codes)):.2f}s/stock; {per}")

    if hasattr(bs, "query_daily_history_k_AStock"):
        for d in ("2026-10-20", "2015-07-08", "2005-01-04"):
            try:
                t0 = time.monotonic()
                rs = bs.query_daily_history_k_AStock(d)
                rows = drain(rs)
                note(f"bs daily_AStock {d}", f"code={rs.error_code} msg={rs.error_msg} rows={len(rows)} fields={rs.fields} sample={rows[:2]} ({time.monotonic() - t0:.1f}s)")
            except Exception as e:  # noqa: BLE001
                note(f"bs daily_AStock {d}", f"EXC {e!r}")
    else:
        note("bs daily_AStock", "function not available in this baostock version")

    try:
        bs.logout()
    except Exception:  # noqa: BLE001
        pass


def _em_count(body: bytes) -> str:
    j = json.loads(body.decode("utf-8"))
    return f"count={(j.get('result') or {}).get('count')}"


def _sge_rows(body: bytes) -> str:
    j = json.loads(body.decode("utf-8"))
    return f"rows={len(j.get('time') or [])}"


def _sse_rows(body: bytes) -> str:
    j = json.loads(body.decode("utf-8"))
    return f"rows={len(j.get('result') or [])}"


def probe_http() -> None:
    # (名称, 方法, URL, headers, 表单 data | None, 额外摘要函数 | None)
    urls = [
        ("csindex index-perf", "GET", "https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000985&startDate=20261001&endDate=20261021",
         {"Referer": "https://www.csindex.com.cn/", "Accept": "application/json, text/plain, */*"}, None, None),
        ("legulegu sw-overview", "GET", "https://legulegu.com/stockdata/sw-industry-overview",
         {"Referer": "https://legulegu.com/", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}, None, None),
        ("sws index list", "GET", "https://www.swsresearch.com/institute-sw/api/index_publish/current/?page=1&page_size=200&indextype=%E4%B8%80%E7%BA%A7%E8%A1%8C%E4%B8%9A",
         {"Referer": "https://www.swsresearch.com/institute_sw/allIndex/releasedIndex", "Accept": "application/json, text/plain, */*"}, None, None),
        ("eastmoney GDP", "GET", "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_ECONOMY_GDP&columns=ALL&pageNumber=1&pageSize=5&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB",
         {"Referer": "https://data.eastmoney.com/cjsj/", "Accept": "application/json, text/javascript, */*; q=0.01"}, None, _em_count),
        ("sge Dailyhq", "POST", "https://www.sge.com.cn/graph/Dailyhq",
         {"Referer": "https://www.sge.com.cn/sjzx/mrhq", "X-Requested-With": "XMLHttpRequest", "Origin": "https://www.sge.com.cn",
          "Accept": "application/json, text/javascript, */*; q=0.01"}, {"instid": "Au99.99"}, _sge_rows),
        ("sse overview", "GET", "https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&PRODUCT_CODE=01,02,03,11,17&type=inParams&SEARCH_DATE=2026-10-22",
         {"Referer": "https://www.sse.com.cn/", "Accept": "application/json, text/javascript, */*; q=0.01"}, None, _sse_rows),
    ]
    http_get = http_post = None
    try:
        from etl.common import http_get as _hg, http_post as _hp  # 带主机限速、重试与申万中间证书
        http_get, http_post = _hg, _hp
        say("http: using etl.common.http_get / http_post")
    except Exception as e:  # noqa: BLE001
        say(f"http: etl.common unavailable ({e!r}), using bare requests")
    for name, method, url, headers, data, summarize in urls:
        t0 = time.monotonic()
        try:
            if http_get is not None:
                if method == "POST":
                    body, hdr = http_post(url, data=data, timeout=120, headers=headers)
                else:
                    body, hdr = http_get(url, timeout=120, headers=headers)
                status, n = 200, len(body)
            else:
                import requests
                if method == "POST":
                    r = requests.post(url, data=data, headers=headers, timeout=120)
                else:
                    r = requests.get(url, headers=headers, timeout=120)
                status, n, body = r.status_code, len(r.content), r.content
            extra = ""
            if summarize is not None and status == 200:
                try:
                    extra = " " + summarize(body)
                except Exception as e:  # noqa: BLE001
                    extra = f" parse-FAIL {type(e).__name__}"
            note(f"http {name}", f"status={status} bytes={n} ({time.monotonic() - t0:.1f}s){extra}")
        except Exception as e:  # noqa: BLE001
            note(f"http {name}", f"FAIL {type(e).__name__}: {str(e)[:160]} ({time.monotonic() - t0:.1f}s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据源连通性与速度诊断")
    ap.add_argument("--stocks", type=int, default=10, help="连续查询多少只个股来估算速度（默认 10）")
    ap.add_argument("--no-baostock", action="store_true", help="跳过 Baostock，只跑 HTTP 连通性")
    args = ap.parse_args(argv)
    say(f"python {sys.version.split()[0]} on {platform.platform()}")
    t_start = time.monotonic()
    if args.no_baostock:
        note("baostock", "skipped (--no-baostock)")
    else:
        try:
            probe_baostock(args.stocks)
        except Exception as e:  # noqa: BLE001
            note("baostock", f"EXC {e!r}")
    try:
        probe_http()
    except Exception as e:  # noqa: BLE001
        note("http", f"EXC {e!r}")
    print("\n===== SUMMARY =====")
    for k, v in SUMMARY:
        print(f"{k}: {v}")
    print(f"total {time.monotonic() - t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
