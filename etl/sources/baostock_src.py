# -*- coding: utf-8 -*-
"""数据源：Baostock（证券宝，``public-api.baostock.com:10030``）— 交易日历 / 证券基本资料 / 个股后复权日线 / 全市场单日横截面。

接口（2026-10-21 探测，m3_work/probe_sources.txt §A/§B；库版本 ``baostock==0.9.4``）：
* ``bs.login()`` 匿名登录（0.1–0.4 s），``bs.logout()``；两者都往 stdout 打印，这里一律静音；
  库的 socket **没有超时**，登录前必须 ``socket.setdefaulttimeout(SOCKET_TIMEOUT)``；
* ``query_trade_dates(start, end)`` → ``calendar_date, is_trading_day``，支持未来日期（到年底的开市标志都有）；
* ``query_stock_basic()`` → ``code, code_name, ipoDate, outDate, type, status``；``type == "1"`` 为股票
  （5,563 只沪深 A 股，含 339 只已退市、``outDate`` 非空；没有 ``bj.`` 北交所代码）；
* ``query_history_k_data_plus(code, fields, start, end, "d", adjustflag="1")`` 个股后复权日线，
  服务端每页 2,000 行、``rs.next()`` 自动翻页；停牌日也有行：``tradestatus == "0"``、close == preclose、
  pctChg 0 或空、turn 空 —— **一律丢弃**；``pctChg`` 是交易所口径的涨跌幅（原始收盘 ÷ 除权除息后昨收 − 1），
  后复权与不复权两个 adjustflag 的 pctChg 完全一致，后复权 close/preclose − 1 == pctChg（茅台 2026-06-26
  除息日亦然），所以日增量可以用 ``hfq(t) = hfq(t−1) × (1 + pctChg/100)`` 续链；
* ``query_daily_history_k_AStock(date)`` 全市场单日横截面（18 个字段，不分页；一天 5,224 行 25–55 s），
  Baostock 约 17:30–18:00 发布当天数据，之前请求当天、或请求周末/节假日都返回 0 行而不是错误。

口径与坑：
* 单会话、顺序查询、绝不并发；任何异常或非 ``"0"`` 的 ``error_code`` 都先重新登录再重试
  （``QUERY_RETRIES`` 次，间隔 ``RETRY_DELAYS``）；网络超时在库内被吞掉后表现为 ``error_code 10002007``；
* **不要调用 ``rs.get_data()``**（内部用 ``DataFrame.append``，pandas ≥ 2 已移除）；用 ``rs.next()`` +
  ``rs.get_row_data()`` 逐行取；``baostock`` 导入时会连带导入 pandas，本模块只在 ``_bs()`` 内懒加载，
  离线单测与其它面板不付这个代价；
* 翻页截断：``rs.next()`` 在下一页请求失败时静默返回 False，于是结果恰好停在整页边界；
  ``fetch_history`` 对「行数是 ``PAGE_SIZE`` 整数倍」的结果重查一次取较长者；
* 横截面在恰好 2,000 行时库会误触发翻页逻辑（把日期参数当页码改写）——2004–2010 年间理论上可能碰到，
  本项目只对近期日期（≥ 5,000 行）调用横截面，不受影响；
* 代码三种写法：Baostock ``sh.600519`` ↔ 本库 key ``sh600519`` ↔ 六位 ``600519``（``to_key/to_bs_code/to_code6``）。
"""
from __future__ import annotations

import contextlib
import io
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from etl import compute
from etl.common import PanelError

log = logging.getLogger("etl.baostock")

SERVER = "public-api.baostock.com:10030"   # 仅作说明，库内常量决定实际地址
SOCKET_TIMEOUT = 120
LOGIN_RETRIES = 3
QUERY_RETRIES = 3
RETRY_DELAYS = (2, 4, 8)
PAGE_SIZE = 2000
HISTORY_FIELDS = "date,close,preclose,pctChg,tradestatus,isST"
HISTORY_START = "2004-01-01"
ADJUST_HFQ = "1"
MIN_STOCK_BASIC = 3000
STOCK_TYPE = "1"

KEY_RE = re.compile(r"^(sh|sz)\d{6}$")
HistoryRow = Tuple[str, float, float, int]   # (date, 后复权 close, pctChg %, isST)


class BaostockError(PanelError):
    """Baostock 登录或查询失败（重试后仍失败）。"""


def _bs() -> Any:
    """懒加载 baostock（连带 pandas）；测试可替换为桩模块。"""
    import baostock as bs
    return bs


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _fmt_exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:200]


# --------------------------------------------------------------------------- 代码转换（纯函数）
def to_key(code: str) -> str:
    """``"sh.600519"`` / ``"SH600519"`` → ``"sh600519"``；格式不对抛 PanelError。"""
    c = str(code).strip().lower().replace(".", "")
    if not KEY_RE.match(c):
        raise PanelError(f"Baostock 代码格式异常: {code!r}")
    return c


def to_code6(key: str) -> str:
    """``"sh600519"`` → ``"600519"``。"""
    return key[-6:]


def to_bs_code(key: str) -> str:
    """``"sh600519"`` → ``"sh.600519"``。"""
    return key[:2] + "." + key[2:]


# --------------------------------------------------------------------------- 解析（纯函数）
def _num(s: Any) -> Optional[float]:
    """Baostock 的数值都是字符串；None / "" / 非数字 → None。"""
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


def _field_index(fields: Sequence[str], required: Sequence[str], what: str) -> Dict[str, int]:
    """按字段名定位列下标（永不写死位置）；缺字段抛 PanelError。"""
    idx = {str(f).strip(): i for i, f in enumerate(fields)}
    missing = [f for f in required if f not in idx]
    if missing:
        raise PanelError(f"Baostock {what} 缺少字段 {missing}，实际 fields={list(fields)}")
    return idx


def _row_values(r: Sequence[str], idx: Dict[str, int], n_fields: int, what: str
                ) -> Optional[Tuple[float, float, int]]:
    """一行 → (close, pct, is_st)；停牌（tradestatus != "1"）、close 不可解析或 ≤ 0 的行返回 None。

    pctChg 为空时用 (close/preclose − 1) × 100 代替（preclose 需 > 0，否则也丢弃）。
    """
    if len(r) < n_fields:
        raise PanelError(f"Baostock {what} 行长度 {len(r)} < 字段数 {n_fields}: {list(r)[:8]}")
    if str(r[idx["tradestatus"]]).strip() != "1":
        return None
    close = _num(r[idx["close"]])
    if close is None or close <= 0:
        return None
    pct = _num(r[idx["pctChg"]])
    if pct is None:
        preclose = _num(r[idx["preclose"]])
        if preclose is None or preclose <= 0:
            return None
        pct = (close / preclose - 1.0) * 100.0
    is_st = 1 if str(r[idx["isST"]]).strip() == "1" else 0
    return close, pct, is_st


def parse_history_rows(fields: Sequence[str], rows: Sequence[Sequence[str]]) -> List[HistoryRow]:
    """个股日线 → ``[(date, close, pct, is_st)]``，只保留交易日（tradestatus == "1"）且 close > 0 的行。

    日期必须严格递增（既排序又唯一），否则抛 PanelError。
    """
    idx = _field_index(fields, ("date", "close", "preclose", "pctChg", "tradestatus", "isST"), "个股日线")
    n_fields = len(fields)
    out: List[HistoryRow] = []
    for r in rows:
        v = _row_values(r, idx, n_fields, "个股日线")
        if v is None:
            continue
        out.append((str(r[idx["date"]]).strip(), v[0], v[1], v[2]))
    compute.assert_ascending([d for d, _c, _p, _s in out], "Baostock 个股日线日期")
    return out


def parse_cross_section_rows(fields: Sequence[str], rows: Sequence[Sequence[str]]) -> List[Dict[str, Any]]:
    """全市场单日横截面 → ``[{key, date, close, preclose, pct, is_st}]``，过滤规则同 ``parse_history_rows``。"""
    idx = _field_index(fields, ("date", "code", "close", "preclose", "pctChg", "tradestatus", "isST"), "全市场日线")
    n_fields = len(fields)
    out: List[Dict[str, Any]] = []
    for r in rows:
        v = _row_values(r, idx, n_fields, "全市场日线")
        if v is None:
            continue
        out.append({
            "key": to_key(r[idx["code"]]),
            "date": str(r[idx["date"]]).strip(),
            "close": v[0],
            "preclose": _num(r[idx["preclose"]]),
            "pct": v[1],
            "is_st": v[2],
        })
    return out


def parse_stock_basic(fields: Sequence[str], rows: Sequence[Sequence[str]]) -> List[Dict[str, str]]:
    """证券基本资料 → 只保留 ``type == "1"`` 的股票 ``[{key, name, ipo, out, status}]``。

    少于 ``MIN_STOCK_BASIC`` 只、或任一 key 不是 ``^(sh|sz)\\d{6}$`` 时抛 PanelError（接口变化或被截断）。
    """
    idx = _field_index(fields, ("code", "code_name", "ipoDate", "outDate", "type", "status"), "证券基本资料")
    out: List[Dict[str, str]] = []
    for r in rows:
        if str(r[idx["type"]]).strip() != STOCK_TYPE:
            continue
        out.append({
            "key": to_key(r[idx["code"]]),
            "name": str(r[idx["code_name"]]).strip(),
            "ipo": str(r[idx["ipoDate"]]).strip(),
            "out": str(r[idx["outDate"]]).strip(),
            "status": str(r[idx["status"]]).strip(),
        })
    if len(out) < MIN_STOCK_BASIC:
        raise PanelError(f"Baostock 证券基本资料只有 {len(out)} 只股票（应 ≥ {MIN_STOCK_BASIC}）")
    return out


# --------------------------------------------------------------------------- 会话（I/O）
class Session:
    """Baostock 单会话：登录、静音、超时、失败重登重试、逐行取数。上下文管理器，退出时登出。"""

    def __init__(self) -> None:
        self.n_queries = 0
        self.n_logins = 0
        self.n_relogins = 0
        self.logged_in = False
        self._mod: Any = None

    def __enter__(self) -> "Session":
        self.login()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.logout()
        return False

    def login(self) -> None:
        socket.setdefaulttimeout(SOCKET_TIMEOUT)
        if self._mod is None:
            self._mod = _bs()
        last = ""
        for attempt in range(LOGIN_RETRIES):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    lg = self._mod.login()
                code = str(getattr(lg, "error_code", ""))
                msg = str(getattr(lg, "error_msg", ""))
            except Exception as e:  # noqa: BLE001 — 库内异常类型不固定
                code, msg = "exception", _fmt_exc(e)
            if code == "0":
                self.logged_in = True
                self.n_logins += 1
                log.info("Baostock 登录成功（第 %d 次登录）", self.n_logins)
                return
            last = f"code={code} msg={msg}"
            if attempt < LOGIN_RETRIES - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                log.warning("Baostock 登录失败 %s，%ds 后重试 (%d/%d)", last, delay, attempt + 2, LOGIN_RETRIES)
                _sleep(delay)
        raise BaostockError(f"Baostock 登录失败 {last}")

    def logout(self) -> None:
        """登出；从不抛异常。"""
        if not self.logged_in or self._mod is None:
            return
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self._mod.logout()
        except Exception as e:  # noqa: BLE001
            log.debug("Baostock 登出异常（忽略）: %s", _fmt_exc(e))
        self.logged_in = False

    def relogin(self) -> None:
        self.logout()
        self.n_relogins += 1
        log.warning("Baostock 重新登录（第 %d 次）", self.n_relogins)
        self.login()

    def rows(self, fn_name: str, *args: Any, **kw: Any) -> Tuple[List[str], List[List[str]]]:
        """调用 ``bs.<fn_name>(*args, **kw)`` 并逐行取尽 → (fields, rows)。

        ``error_code != "0"``、返回 None 或任何异常：重新登录后重试，最多 ``QUERY_RETRIES`` 次。
        """
        if not self.logged_in:
            self.login()
        last_err = ""
        for attempt in range(QUERY_RETRIES):
            self.n_queries += 1
            try:
                fn = getattr(self._mod, fn_name)
                with contextlib.redirect_stdout(io.StringIO()):
                    rs = fn(*args, **kw)
                    if rs is None:
                        raise BaostockError(f"{fn_name} 返回 None")
                    code = str(rs.error_code)
                    if code != "0":
                        raise BaostockError(f"{fn_name} code={code} msg={getattr(rs, 'error_msg', '')}")
                    out: List[List[str]] = []
                    while rs.next():
                        out.append(list(rs.get_row_data()))
                    code = str(rs.error_code)
                    if code != "0":
                        raise BaostockError(f"{fn_name} 翻页时 code={code} msg={getattr(rs, 'error_msg', '')}")
                return list(rs.fields), out
            except Exception as e:  # noqa: BLE001
                last_err = _fmt_exc(e)
                if attempt < QUERY_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    log.warning("Baostock %s 失败 (%s)，%ds 后重新登录并重试 (%d/%d)",
                                fn_name, last_err, delay, attempt + 2, QUERY_RETRIES)
                    _sleep(delay)
                    self.relogin()
        raise BaostockError(f"Baostock {fn_name} 重试 {QUERY_RETRIES} 次仍失败: {last_err}")


# --------------------------------------------------------------------------- 取数（I/O，薄封装）
def fetch_trade_dates(sess: Session, start: str, end: str) -> List[str]:
    """[start, end] 内的交易日（``is_trading_day == "1"``），升序；支持未来日期。"""
    fields, rows = sess.rows("query_trade_dates", start_date=start, end_date=end)
    idx = _field_index(fields, ("calendar_date", "is_trading_day"), "交易日历")
    dates = [str(r[idx["calendar_date"]]).strip() for r in rows if str(r[idx["is_trading_day"]]).strip() == "1"]
    compute.assert_ascending(dates, "Baostock 交易日历")
    log.debug("Baostock 交易日历 %s→%s: %d 个交易日", start, end, len(dates))
    return dates


def fetch_stock_basic(sess: Session) -> List[Dict[str, str]]:
    """全部沪深 A 股（含已退市）``[{key, name, ipo, out, status}]``。"""
    fields, rows = sess.rows("query_stock_basic")
    stocks = parse_stock_basic(fields, rows)
    log.info("Baostock 证券基本资料: %d 行，其中股票 %d 只", len(rows), len(stocks))
    return stocks


def fetch_history(sess: Session, key: str, start: str, end: str) -> List[HistoryRow]:
    """一只股票 [start, end] 的后复权日线（已剔除停牌日）。

    行数恰为 ``PAGE_SIZE`` 的整数倍时疑似翻页被静默截断，重查一次并取较长的结果。
    """
    code = to_bs_code(key)
    fields, raw = sess.rows("query_history_k_data_plus", code, HISTORY_FIELDS, start_date=start, end_date=end,
                            frequency="d", adjustflag=ADJUST_HFQ)
    if raw and len(raw) % PAGE_SIZE == 0:
        log.warning("Baostock %s 返回 %d 行恰为整页，疑似翻页截断，重查一次", key, len(raw))
        fields2, raw2 = sess.rows("query_history_k_data_plus", code, HISTORY_FIELDS, start_date=start, end_date=end,
                                  frequency="d", adjustflag=ADJUST_HFQ)
        if len(raw2) >= len(raw):
            fields, raw = fields2, raw2
    return parse_history_rows(fields, raw)


def fetch_cross_section(sess: Session, date: str) -> List[Dict[str, Any]]:
    """某交易日全市场横截面（已剔除停牌行）；未发布或非交易日返回空列表。"""
    fields, raw = sess.rows("query_daily_history_k_AStock", date)
    if not raw:
        log.debug("Baostock 横截面 %s: 0 行", date)
        return []
    rows = parse_cross_section_rows(fields, raw)
    log.debug("Baostock 横截面 %s: %d 行，交易 %d 行", date, len(raw), len(rows))
    return rows
