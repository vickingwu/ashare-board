# -*- coding: utf-8 -*-
"""baostock_src 的离线测试：代码转换、日线/横截面/基本资料解析、会话重登重试、翻页截断保护；不导入 baostock。"""
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from etl.common import PanelError
from etl.sources import baostock_src as bsrc

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class _FakeRS:
    """模拟 baostock 的 ResultData：error_code / error_msg / fields / next() / get_row_data()。"""

    def __init__(self, rows, fields=("date", "close"), code="0", msg="success"):
        self.error_code = code
        self.error_msg = msg
        self.fields = list(fields)
        self._rows = list(rows)
        self._i = 0

    def next(self):
        return self._i < len(self._rows)

    def get_row_data(self):
        r = self._rows[self._i]
        self._i += 1
        return r


class _FakeBS:
    """桩 baostock 模块：login/logout 计数，query_* 按 responses 队列依次返回。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.logins = 0
        self.logouts = 0
        self.calls = []

    def login(self):
        self.logins += 1
        return _FakeRS([], code="0")

    def logout(self):
        self.logouts += 1
        return _FakeRS([], code="0")

    def __getattr__(self, name):
        if name.startswith("query_"):
            def fn(*a, **kw):
                self.calls.append((name, a, kw))
                return self.responses.pop(0)
            return fn
        raise AttributeError(name)


def _session_with(fake):
    with mock.patch.object(bsrc, "_bs", lambda: fake), mock.patch.object(bsrc, "_sleep", lambda s: None):
        sess = bsrc.Session()
        sess.login()
    return sess


class ImportTest(unittest.TestCase):
    def test_module_import_does_not_import_baostock(self):
        self.assertIn("etl.sources.baostock_src", sys.modules)
        self.assertNotIn("baostock", sys.modules)
        self.assertNotIn("pandas", sys.modules)


class KeyTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(bsrc.to_key("sh.600519"), "sh600519")
        self.assertEqual(bsrc.to_key("SZ.000001"), "sz000001")
        self.assertEqual(bsrc.to_code6("sh600519"), "600519")
        self.assertEqual(bsrc.to_bs_code("sh600519"), "sh.600519")
        self.assertEqual(bsrc.to_bs_code(bsrc.to_key("sz.300750")), "sz.300750")
        self.assertEqual(bsrc.to_key(bsrc.to_bs_code("sh688981")), "sh688981")

    def test_bad_code(self):
        for bad in ("bj.430047", "sh.60051", "600519", "", "sh.60051x"):
            with self.assertRaises(PanelError, msg=bad):
                bsrc.to_key(bad)


class ParseHistoryTest(unittest.TestCase):
    def test_600519_fixture(self):
        fx = _fx("bs_k_sh600519_hfq.json")
        rows = bsrc.parse_history_rows(fx["fields"], fx["head"] + fx["tail"])
        self.assertEqual(len(rows), 10)
        d, close, pct, st = rows[0]
        self.assertEqual(d, "2005-01-04")
        self.assertAlmostEqual(close, 59.2704702, delta=1e-6)
        self.assertAlmostEqual(pct, -0.52, delta=1e-6)
        self.assertEqual(st, 0)
        self.assertEqual(rows[-1][0], "2026-10-20")
        self.assertAlmostEqual(rows[-1][1], 10214.5300131600, delta=1e-4)
        self.assertAlmostEqual(rows[-1][2], 0.5215, delta=1e-6)
        self.assertTrue(all(isinstance(r[1], float) and isinstance(r[2], float) and r[3] in (0, 1) for r in rows))

    def test_suspended_rows_dropped(self):
        fx = _fx("bs_k_sz000001_suspended.json")
        self.assertEqual(len(fx["rows"]), 6)
        rows = bsrc.parse_history_rows(fx["fields"], fx["rows"])
        self.assertEqual([r[0] for r in rows], ["2005-01-04", "2005-01-05", "2026-10-20"])
        self.assertAlmostEqual(rows[0][1], 193.8605683200, delta=1e-6)
        self.assertAlmostEqual(rows[0][2], -1.06, delta=1e-6)
        self.assertAlmostEqual(rows[-1][1], 1460.4634387200, delta=1e-6)

    def test_empty_pctchg_falls_back_to_preclose(self):
        fields = ["date", "close", "preclose", "pctChg", "tradestatus", "isST"]
        rows = [["2026-10-20", "10.5000", "10.0000", "", "1", "1"],
                ["2026-10-21", "10.5000", "", "", "1", "0"],              # preclose 也空 → 丢弃
                ["2026-10-22", "", "10.5000", "1.0", "1", "0"]]            # close 空 → 丢弃
        out = bsrc.parse_history_rows(fields, rows)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][2], 5.0, delta=1e-6)
        self.assertEqual(out[0][3], 1)

    def test_unsorted_or_duplicate_dates_rejected(self):
        fields = ["date", "close", "preclose", "pctChg", "tradestatus", "isST"]
        good = ["2026-10-20", "10", "9", "1", "1", "0"]
        with self.assertRaises(PanelError):
            bsrc.parse_history_rows(fields, [good, ["2026-10-19", "10", "9", "1", "1", "0"]])
        with self.assertRaises(PanelError):
            bsrc.parse_history_rows(fields, [good, list(good)])

    def test_missing_field_or_short_row(self):
        fields = ["date", "close", "preclose", "tradestatus", "isST"]
        with self.assertRaises(PanelError):
            bsrc.parse_history_rows(fields, [["2026-10-20", "10", "9", "1", "0"]])
        with self.assertRaises(PanelError):
            bsrc.parse_history_rows(["date", "close", "preclose", "pctChg", "tradestatus", "isST"], [["2026-10-20", "10"]])


class ParseCrossSectionTest(unittest.TestCase):
    def test_trimmed_fixture(self):
        fx = _fx("bs_daily_AStock_20261020_trim.json")
        self.assertEqual(len(fx["rows"]), 42)
        self.assertEqual(fx["n"], 5224)
        rows = bsrc.parse_cross_section_rows(fx["fields"], fx["rows"])
        self.assertEqual(len(rows), 40)
        keys = [r["key"] for r in rows]
        self.assertTrue(all(bsrc.KEY_RE.match(k) for k in keys))
        self.assertNotIn("sh688496", keys)
        self.assertNotIn("sz000016", keys)
        r = rows[0]
        self.assertEqual(r["key"], "sh600000")
        self.assertEqual(r["date"], "2026-10-20")
        self.assertAlmostEqual(r["close"], 9.76, delta=1e-9)
        self.assertAlmostEqual(r["preclose"], 9.78, delta=1e-9)
        self.assertAlmostEqual(r["pct"], -0.2045, delta=1e-9)
        self.assertEqual(r["is_st"], 0)
        st = next(x for x in rows if x["key"] == "sh600053")
        self.assertEqual(st["is_st"], 1)

    def test_missing_pctchg_field(self):
        fx = _fx("bs_daily_AStock_20261020_trim.json")
        fields = [f if f != "pctChg" else "pct_chg" for f in fx["fields"]]
        with self.assertRaises(PanelError):
            bsrc.parse_cross_section_rows(fields, fx["rows"])


class ParseStockBasicTest(unittest.TestCase):
    def _rows(self, n_stock, n_other=5):
        fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
        rows = [[f"sz.00{i:04d}", f"股票{i}", "2010-01-04", "", "1", "1"] for i in range(n_stock)]
        rows += [[f"sh.0000{i:02d}", f"指数{i}", "1991-07-15", "", "2", "1"] for i in range(n_other)]
        return fields, rows

    def test_filters_type_and_requires_min_rows(self):
        fields, rows = self._rows(3000)
        out = bsrc.parse_stock_basic(fields, rows)
        self.assertEqual(len(out), 3000)
        self.assertEqual(out[0], {"key": "sz000000", "name": "股票0", "ipo": "2010-01-04", "out": "", "status": "1"})
        fields, rows = self._rows(2999)
        with self.assertRaises(PanelError):
            bsrc.parse_stock_basic(fields, rows)

    def test_head_fixture_only_indexes(self):
        fx = _fx("bs_stock_basic_head.json")
        with self.assertRaises(PanelError):                                  # 20 行指数 + 10 行退市股 < 3000
            bsrc.parse_stock_basic(fx["fields"], fx["rows"] + fx["delisted_sample"])
        fields, rows = self._rows(3000)
        out = bsrc.parse_stock_basic(fields, rows + fx["delisted_sample"])
        self.assertEqual(len(out), 3010)
        delisted = next(x for x in out if x["key"] == "sh600001")
        self.assertEqual((delisted["name"], delisted["ipo"], delisted["out"], delisted["status"]),
                         ("邯郸钢铁", "1998-01-22", "2009-12-29", "0"))

    def test_fetch_stock_basic_via_session(self):
        fields, rows = self._rows(3000, 7)
        fake = _FakeBS([_FakeRS(rows, fields)])
        sess = _session_with(fake)
        out = bsrc.fetch_stock_basic(sess)
        self.assertEqual(len(out), 3000)
        self.assertEqual(fake.calls[0][0], "query_stock_basic")
        fake2 = _FakeBS([_FakeRS(rows[:10], fields)])
        with self.assertRaises(PanelError):
            bsrc.fetch_stock_basic(_session_with(fake2))


class SessionTest(unittest.TestCase):
    def test_rows_relogin_once_on_error_code(self):
        rows = [["2026-10-20", "9.76"], ["2026-10-21", "9.80"]]
        fake = _FakeBS([_FakeRS([], code="10001", msg="网络接收错误"), _FakeRS(rows)])
        with mock.patch.object(bsrc, "_bs", lambda: fake), mock.patch.object(bsrc, "_sleep", lambda s: None):
            with bsrc.Session() as sess:
                with self.assertLogs("etl.baostock", level="WARNING"):
                    fields, out = sess.rows("query_history_k_data_plus", "sh.600000", "date,close")
            self.assertFalse(sess.logged_in)
        self.assertEqual(fields, ["date", "close"])
        self.assertEqual(out, rows)
        self.assertEqual(sess.n_queries, 2)
        self.assertEqual(sess.n_relogins, 1)
        self.assertEqual(sess.n_logins, 2)
        self.assertEqual(fake.logins, 2)
        self.assertEqual(fake.logouts, 2)                                   # 重登一次 + 退出登出

    def test_rows_gives_up_after_retries(self):
        fake = _FakeBS([_FakeRS([], code="10001")] * bsrc.QUERY_RETRIES)
        sess = _session_with(fake)
        with mock.patch.object(bsrc, "_sleep", lambda s: None):
            with self.assertRaises(bsrc.BaostockError):
                sess.rows("query_trade_dates", start_date="2026-01-01", end_date="2026-01-31")
        self.assertEqual(sess.n_queries, bsrc.QUERY_RETRIES)
        self.assertEqual(sess.n_relogins, bsrc.QUERY_RETRIES - 1)

    def test_login_failure_raises(self):
        class _BadBS(_FakeBS):
            def login(self):
                self.logins += 1
                return _FakeRS([], code="10001", msg="login failed")

        fake = _BadBS([])
        with mock.patch.object(bsrc, "_bs", lambda: fake), mock.patch.object(bsrc, "_sleep", lambda s: None):
            with self.assertRaises(bsrc.BaostockError):
                bsrc.Session().login()
        self.assertEqual(fake.logins, bsrc.LOGIN_RETRIES)
        self.assertTrue(issubclass(bsrc.BaostockError, PanelError))

    def test_stdout_suppressed(self):
        class _NoisyBS(_FakeBS):
            def login(self):
                print("login success!")
                return super().login()

        fake = _NoisyBS([])
        buf = mock.MagicMock()
        with mock.patch.object(bsrc, "_bs", lambda: fake), mock.patch("sys.stdout", buf):
            bsrc.Session().login()
        buf.write.assert_not_called()


class FetchTest(unittest.TestCase):
    def test_history_truncation_guard(self):
        fields = ["date", "close", "preclose", "pctChg", "tradestatus", "isST"]

        def rows(n):
            d0 = date(2004, 1, 1)
            return [[(d0 + timedelta(days=i)).isoformat(), "10", "9", "1", "1", "0"] for i in range(n)]

        full = rows(2001)
        fake = _FakeBS([_FakeRS(full[:2000], fields), _FakeRS(full, fields)])
        sess = _session_with(fake)
        with self.assertLogs("etl.baostock", level="WARNING"):
            out = bsrc.fetch_history(sess, "sh600000", "2004-01-01", "2026-10-21")
        self.assertEqual(len(out), 2001)
        self.assertEqual(len(fake.calls), 2)
        name, args, kw = fake.calls[0]
        self.assertEqual(name, "query_history_k_data_plus")
        self.assertEqual(args, ("sh.600000", bsrc.HISTORY_FIELDS))
        self.assertEqual(kw, {"start_date": "2004-01-01", "end_date": "2026-10-21", "frequency": "d", "adjustflag": "1"})
        # 行数不是整页时只查一次
        fake2 = _FakeBS([_FakeRS(full[:1999], fields)])
        sess2 = _session_with(fake2)
        self.assertEqual(len(bsrc.fetch_history(sess2, "sh600000", "2004-01-01", "2026-10-21")), 1999)
        self.assertEqual(len(fake2.calls), 1)

    def test_history_retries_once_on_garbled_pages_then_raises(self):
        """2026-10-21 sh603856 实测：整页重查后拿到的结果里第二页重复出现（日期 2026-01-28 → 2025-02-17 不递增）。
        fetch_history 应把整个查询再来一遍（PARSE_RETRIES = 2）；第二次正常则返回，两次都坏则抛 PanelError（不是 BaostockError）。"""
        fields = ["date", "close", "preclose", "pctChg", "tradestatus", "isST"]
        d0 = date(2017, 1, 3)
        good = [[(d0 + timedelta(days=i)).isoformat(), "10", "9", "1", "1", "0"] for i in range(2230)]
        garbled = good[:2000] + good[2000:] + good[2000:]            # 第 2 页出现两次 → 日期回跳
        # 第 1 次：2000 行整页 → 重查 → 坏结果；第 2 次：2230 行（非整页）→ 正常
        fake = _FakeBS([_FakeRS(good[:2000], fields), _FakeRS(garbled, fields), _FakeRS(good, fields)])
        sess = _session_with(fake)
        with self.assertLogs("etl.baostock", level="WARNING") as cm:
            out = bsrc.fetch_history(sess, "sh603856", "2004-01-01", "2026-10-21")
        self.assertEqual(len(out), 2230)
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(any("解析失败" in m for m in cm.output), cm.output)
        self.assertEqual(sess.n_relogins, 0)                           # 解析失败不触发重新登录
        # 两次都坏 → PanelError 向上抛（冷启动只把这一只记为失败）
        fake2 = _FakeBS([_FakeRS(garbled, fields), _FakeRS(garbled, fields)])
        sess2 = _session_with(fake2)
        with self.assertLogs("etl.baostock", level="WARNING"):
            with self.assertRaises(PanelError) as err:
                bsrc.fetch_history(sess2, "sh603856", "2004-01-01", "2026-10-21")
        self.assertNotIsInstance(err.exception, bsrc.BaostockError)
        self.assertIn("不是严格递增", str(err.exception))
        self.assertEqual(len(fake2.calls), 2)
        self.assertEqual(bsrc.PARSE_RETRIES, 2)

    def test_trade_dates_and_cross_section(self):
        td = _fx("bs_trade_dates_tail.json")
        cs = _fx("bs_daily_AStock_20261020_trim.json")
        fake = _FakeBS([_FakeRS(td, ["calendar_date", "is_trading_day"]), _FakeRS(cs["rows"], cs["fields"]),
                        _FakeRS([], cs["fields"])])
        sess = _session_with(fake)
        dates = bsrc.fetch_trade_dates(sess, "2026-10-12", "2026-10-21")
        self.assertEqual(dates, ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16",
                                 "2026-10-19", "2026-10-20", "2026-10-21"])
        self.assertEqual(fake.calls[0][2], {"start_date": "2026-10-12", "end_date": "2026-10-21"})
        rows = bsrc.fetch_cross_section(sess, "2026-10-20")
        self.assertEqual(len(rows), 40)
        self.assertEqual(fake.calls[1][1], ("2026-10-20",))
        self.assertEqual(bsrc.fetch_cross_section(sess, "2026-10-21"), [])
        self.assertEqual(sess.n_queries, 3)

    def test_constants(self):
        self.assertEqual(bsrc.PAGE_SIZE, 2000)
        self.assertEqual(bsrc.HISTORY_START, "2004-01-01")
        self.assertEqual(bsrc.HISTORY_FIELDS, "date,close,preclose,pctChg,tradestatus,isST")
        self.assertEqual(bsrc.ADJUST_HFQ, "1")
        self.assertEqual(bsrc.SOCKET_TIMEOUT, 120)
        self.assertEqual((bsrc.LOGIN_RETRIES, bsrc.QUERY_RETRIES, bsrc.RETRY_DELAYS), (3, 3, (2, 4, 8)))


class ZzNoNetworkTest(unittest.TestCase):
    """按字母序最后执行：以上所有桩会话都不得触发真正的 baostock 导入（否则就是联网了）。"""

    def test_baostock_still_not_imported(self):
        self.assertNotIn("baostock", sys.modules)


if __name__ == "__main__":
    unittest.main()
