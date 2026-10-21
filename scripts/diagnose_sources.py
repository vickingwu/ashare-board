#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源连通性与速度诊断（给 GitHub Actions 的 diagnose-sources workflow 用，本地也能跑）。

用法：python scripts/diagnose_sources.py [--stocks N]
    依赖：pip install baostock requests（Baostock 会带上 pandas / numpy）。每一项都单独 try/except，
    任何一项失败都不影响其它项，退出码恒为 0——结论看打印出来的 SUMMARY 段。

检查项：
1. Baostock（public-api.baostock.com:10030，TCP 长连接）：DNS、TCP 连接、login 耗时；query_trade_dates；
   query_stock_basic 行数；3 只个股 2005 年起的后复权日线（行数、秒）；N 只个股连续查询的平均耗时；
   query_daily_history_k_AStock(日期) 全市场单日横截面（行数、字段、秒）。
2. HTTP：中证指数官网 index-perf、乐咕乐股 行业总览页、申万宏源研究 指数列表，各 1 次 GET 的状态码与耗时
   （优先走仓库的 etl.common.http_get，带限速与申万中间证书；导入失败则退回裸 requests）。
不读写 cache/，不写任何文件。
"""
from __future__ import annotations

import argparse
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


def probe_http() -> None:
    urls = [
        ("csindex index-perf", "https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000985&startDate=20261001&endDate=20261021",
         {"Referer": "https://www.csindex.com.cn/", "Accept": "application/json, text/plain, */*"}),
        ("legulegu sw-overview", "https://legulegu.com/stockdata/sw-industry-overview",
         {"Referer": "https://legulegu.com/", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}),
        ("sws index list", "https://www.swsresearch.com/institute-sw/api/index_publish/current/?page=1&page_size=200&indextype=%E4%B8%80%E7%BA%A7%E8%A1%8C%E4%B8%9A",
         {"Referer": "https://www.swsresearch.com/institute_sw/allIndex/releasedIndex", "Accept": "application/json, text/plain, */*"}),
    ]
    http_get = None
    try:
        from etl.common import http_get as _hg  # 带主机限速、重试与申万中间证书
        http_get = _hg
        say("http: using etl.common.http_get")
    except Exception as e:  # noqa: BLE001
        say(f"http: etl.common unavailable ({e!r}), using bare requests")
    for name, url, headers in urls:
        t0 = time.monotonic()
        try:
            if http_get is not None:
                body, hdr = http_get(url, timeout=120, headers=headers)
                status, n = 200, len(body)
            else:
                import requests
                r = requests.get(url, headers=headers, timeout=120)
                status, n = r.status_code, len(r.content)
            note(f"http {name}", f"status={status} bytes={n} ({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            note(f"http {name}", f"FAIL {type(e).__name__}: {str(e)[:160]} ({time.monotonic() - t0:.1f}s)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据源连通性与速度诊断")
    ap.add_argument("--stocks", type=int, default=10, help="连续查询多少只个股来估算速度（默认 10）")
    args = ap.parse_args(argv)
    say(f"python {sys.version.split()[0]} on {platform.platform()}")
    t_start = time.monotonic()
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
