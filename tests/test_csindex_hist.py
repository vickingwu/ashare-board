# -*- coding: utf-8 -*-
"""csindex_hist 的离线测试：响应解析（周末行、peg、startDate 占位行）、空数据、异常码、
从最后缓存日起的增量合并、「收盘前丢弃当天」、缓存自愈与一次性清理。"""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

from etl import common, compute
from etl.common import PanelError
from etl.sources import csindex_hist

FX = Path(__file__).resolve().parent / "fixtures"


class ParsePerfTest(unittest.TestCase):
    def test_parse_fixture(self):
        parsed = csindex_hist.parse_perf((FX / "csindex_perf_000300_trim.json").read_bytes())
        self.assertEqual(parsed["code"], "000300")
        self.assertEqual(parsed["name"], "沪深300")
        self.assertEqual(parsed["nameFull"], "沪深300指数")
        rows = parsed["rows"]
        self.assertEqual(len(rows), 70)                       # 71 行去掉 20050101（周六）
        self.assertEqual(rows[0][0], "2005-01-04")
        self.assertEqual(rows[0][1], 982.79)
        self.assertIsNone(rows[0][2])                         # 2005 年没有 peg
        self.assertEqual(rows[-1], ("2026-10-16", 4170.61, 12.88))
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), len(dates))

    def test_full_fetch_with_start_unchanged(self):
        """全量路径：startDate=20050101 是周六，周末规则先丢掉它，占位规则不再碰 2005-01-04。"""
        body = (FX / "csindex_perf_000300_trim.json").read_bytes()
        rows = csindex_hist.parse_perf(body, start=csindex_hist.FULL_START, known_dates=())["rows"]
        self.assertEqual(rows, csindex_hist.parse_perf(body)["rows"])
        self.assertEqual(len(rows), 70)
        self.assertEqual(rows[0], ("2005-01-04", 982.79, None))

    def test_synthetic_start_row_dropped_on_incremental(self):
        """真实响应：startDate=20261005（国庆假期）→ 第 0 行日期 20261005、值照抄 20261008。"""
        body = (FX / "csindex_perf_000300_inc_20261005.json").read_bytes()
        raw = json.loads(body)["data"]
        self.assertEqual(raw[0]["tradeDate"], "20261005")
        self.assertEqual((raw[0]["close"], raw[0]["peg"]), (raw[1]["close"], raw[1]["peg"]))   # 占位行的特征
        known = {"2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
                 "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"}
        rows = csindex_hist.parse_perf(body, start="20261005", known_dates=known)["rows"]
        dates = [r[0] for r in rows]
        self.assertNotIn("2026-10-05", dates)
        self.assertEqual(dates, ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14",
                                 "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"])
        self.assertEqual(rows[0], ("2026-10-08", 4310.28, 13.09))
        self.assertEqual(rows[-1], ("2026-10-20", 4152.11, 12.91))
        # 不告诉解析器 startDate 时保持早期行为（只过滤周末），占位行会留下 —— 这正是修复前的 bug
        self.assertIn("2026-10-05", [r[0] for r in csindex_hist.parse_perf(body)["rows"]])

    def test_synthetic_start_row_on_full_fetch(self):
        """无缓存（全量）时：首行与下一行收盘相同 → 占位行丢弃；收盘不同 → 真实交易日保留。"""
        body = (FX / "csindex_perf_000300_inc_20261005.json").read_bytes()
        dates = [r[0] for r in csindex_hist.parse_perf(body, start="20261005", known_dates=())["rows"]]
        self.assertEqual(dates[0], "2026-10-08")
        self.assertEqual(len(dates), 9)
        distinct = json.dumps({"code": "200", "success": True, "data": [
            {"tradeDate": "20240102", "indexCode": "X", "indexNameCn": "x", "indexNameCnAll": "x", "close": 10.0, "peg": 1},
            {"tradeDate": "20240103", "indexCode": "X", "indexNameCn": "x", "indexNameCnAll": "x", "close": 11.0, "peg": 1},
        ]}).encode()
        rows = csindex_hist.parse_perf(distinct, start="20240102", known_dates=())["rows"]
        self.assertEqual([r[0] for r in rows], ["2024-01-02", "2024-01-03"])
        # 有缓存但 startDate 不在缓存里：不看数值，一律丢弃
        rows = csindex_hist.parse_perf(distinct, start="20240102", known_dates={"2023-12-29"})["rows"]
        self.assertEqual([r[0] for r in rows], ["2024-01-03"])
        # startDate 已在缓存里（正常增量）：保留，由合并去重
        rows = csindex_hist.parse_perf(distinct, start="20240102", known_dates={"2024-01-02"})["rows"]
        self.assertEqual([r[0] for r in rows], ["2024-01-02", "2024-01-03"])

    def test_empty_data(self):
        parsed = csindex_hist.parse_perf((FX / "csindex_perf_empty.json").read_bytes())
        self.assertEqual(parsed["rows"], [])
        self.assertEqual(csindex_hist.parse_perf((FX / "csindex_perf_empty.json").read_bytes(),
                                                 start="20261019", known_dates={"2026-10-19"})["rows"], [])

    def test_bad_status(self):
        with self.assertRaises(PanelError):
            csindex_hist.parse_perf(json.dumps({"code": "500", "msg": "err", "data": [], "success": False}).encode())
        with self.assertRaises(PanelError):
            csindex_hist.parse_perf(b"<html>maintenance</html>")

    def test_non_positive_close_and_peg_dropped(self):
        body = json.dumps({"code": "200", "success": True, "data": [
            {"tradeDate": "20240102", "indexCode": "X", "indexNameCn": "x", "indexNameCnAll": "x", "close": 0, "peg": 1},
            {"tradeDate": "20240103", "indexCode": "X", "indexNameCn": "x", "indexNameCnAll": "x", "close": 10.5, "peg": 0},
            {"tradeDate": "20240106", "indexCode": "X", "indexNameCn": "x", "indexNameCnAll": "x", "close": 11, "peg": 2},
        ]}).encode()
        rows = csindex_hist.parse_perf(body)["rows"]
        self.assertEqual(rows, [("2024-01-03", 10.5, None)])   # 0 收盘丢弃；peg 0 → None；周六丢弃


class GetHistoryTest(unittest.TestCase):
    """用假缓存和假请求跑 get_index_history 的合并逻辑。"""

    @staticmethod
    def _patched(cached, store, now_closed, today):
        stack = ExitStack()
        for target, repl in (("cache_load", lambda sub, key: cached),
                             ("cache_save", lambda sub, key, obj: store.update({key: obj})),
                             ("cache_is_fresh", lambda fa: False),
                             ("market_closed", lambda: now_closed),
                             ("today_str", lambda: today),
                             ("today_compact", lambda: today.replace("-", "")),
                             ("iso_now", lambda: today + "T10:00:00+08:00")):
            stack.enter_context(mock.patch.object(csindex_hist, target, repl))
        return stack

    def _run(self, cached, response_rows, now_closed=True, today="2026-10-19"):
        store = {}
        parsed = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "rows": response_rows}

        def fake_request(code, s, e, known_dates=()):
            store["req"] = (s, e)
            store["known"] = set(known_dates)
            return parsed

        with self._patched(cached, store, now_closed, today), \
                mock.patch.object(csindex_hist, "_request", fake_request):
            result = csindex_hist.get_index_history("000300")
        return result, store

    def _run_http(self, cached, body, now_closed=True, today="2026-10-21"):
        """走真实的 _request/parse_perf，只假装 HTTP：返回 body，记录请求 URL。"""
        store = {}

        def fake_http_get(url, **kw):
            store["url"] = url
            return body, {}

        with self._patched(cached, store, now_closed, today), \
                mock.patch.object(csindex_hist, "http_get", fake_http_get):
            result = csindex_hist.get_index_history("000300")
        return result, store

    def test_incremental_merge_newer_wins(self):
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "2026-10-10T17:00:00+08:00",
                  "dates": ["2026-10-08", "2026-10-09"], "close": [100.0, 101.0], "pe": [12.0, 12.1]}
        new_rows = [("2026-10-09", 101.5, 12.15), ("2026-10-16", 103.0, 12.3)]
        result, store = self._run(cached, new_rows)
        self.assertEqual(result["dates"], ["2026-10-08", "2026-10-09", "2026-10-16"])
        self.assertEqual(result["close"], [100.0, 101.5, 103.0])
        self.assertEqual(result["pe"], [12.0, 12.15, 12.3])
        self.assertEqual(store["req"], ("20261009", "20261019"))      # 从最后缓存日当天起，不再 − 15 天
        self.assertEqual(store["known"], {"2026-10-08", "2026-10-09"})
        self.assertIn("000300.json", store)

    def test_incremental_from_last_cached_day_real_response(self):
        """真实响应：startDate=20261019（最后缓存日）→ 第 0 行就是 10-19 真实行情，合并后无重复、新增 10-20。"""
        body = (FX / "csindex_perf_000300_inc_20261019.json").read_bytes()
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "2026-10-20T10:24:04+08:00",
                  "dates": ["2026-10-15", "2026-10-16", "2026-10-19"], "close": [4166.01, 4170.61, 4174.53],
                  "pe": [12.87, 12.88, 12.96]}
        result, store = self._run_http(cached, body, now_closed=False, today="2026-10-21")
        self.assertIn("startDate=20261019&endDate=20261021", store["url"])
        self.assertEqual(result["dates"], ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"])
        self.assertEqual(result["close"], [4166.01, 4170.61, 4174.53, 4152.11])
        self.assertEqual(result["pe"], [12.87, 12.88, 12.96, 12.91])
        self.assertEqual(len(set(result["dates"])), 4)

    def test_old_window_response_does_not_inject_holiday_row(self):
        """即使窗口又落到假期（startDate=20261005），占位行也进不了合并结果。"""
        body = (FX / "csindex_perf_000300_inc_20261005.json").read_bytes()
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "2026-10-20T10:24:04+08:00",
                  "dates": ["2026-09-30", "2026-10-08"], "close": [4357.62, 4310.28], "pe": [13.2, 13.09]}
        with mock.patch.object(csindex_hist, "http_get", lambda url, **kw: (body, {})):
            parsed = csindex_hist._request("000300", "20261005", "20261020", known_dates=set(cached["dates"]))
        self.assertEqual(parsed["rows"][0][0], "2026-10-08")
        dates, _ = compute.merge_series(cached["dates"], list(zip(cached["close"], cached["pe"])),
                                        [r[0] for r in parsed["rows"]], [(r[1], r[2]) for r in parsed["rows"]])
        self.assertNotIn("2026-10-05", dates)
        self.assertEqual(dates, ["2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14",
                                 "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"])

    def test_full_fetch_when_no_cache_and_empty_is_error(self):
        with self.assertRaises(PanelError):
            self._run(None, [])

    def test_full_fetch_requests_from_2005(self):
        rows = [("2005-01-04", 982.79, None), ("2005-01-05", 992.56, None)]
        result, store = self._run(None, rows)
        self.assertEqual(store["req"], ("20050101", "20261019"))
        self.assertEqual(store["known"], set())
        self.assertEqual(result["dates"], ["2005-01-04", "2005-01-05"])

    def test_incremental_empty_keeps_cache(self):
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "2026-10-10T17:00:00+08:00",
                  "dates": ["2026-10-08", "2026-10-09"], "close": [100.0, 101.0], "pe": [None, None]}
        with self.assertLogs("etl.csindex_hist", level="WARNING"):
            result, _ = self._run(cached, [])
        self.assertEqual(result["dates"], ["2026-10-08", "2026-10-09"])

    def test_today_row_dropped_before_close(self):
        rows = [("2026-10-16", 103.0, 12.3), ("2026-10-19", 104.0, 12.4)]
        result, _ = self._run(None, rows, now_closed=False)
        self.assertEqual(result["dates"], ["2026-10-16"])
        result2, _ = self._run(None, rows, now_closed=True)
        self.assertEqual(result2["dates"], ["2026-10-16", "2026-10-19"])

    def test_fresh_cache_returns_without_request(self):
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "x",
                  "dates": ["2026-10-16"], "close": [1.0], "pe": [None]}
        called = []
        with mock.patch.object(csindex_hist, "cache_load", lambda sub, key: cached), \
                mock.patch.object(csindex_hist, "cache_is_fresh", lambda fa: True), \
                mock.patch.object(csindex_hist, "_request", lambda *a, **kw: called.append(a)):
            result = csindex_hist.get_index_history("000300")
        self.assertIs(result, cached)
        self.assertEqual(called, [])

    def test_cached_weekend_rows_healed_on_load(self):
        """缓存里混进周六/周日的行：读取时丢弃并写回，再按最后一个真实日期发增量请求。"""
        cached = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "fetchedAt": "2026-10-10T17:00:00+08:00",
                  "dates": ["2026-10-09", "2026-10-10", "2026-10-13"], "close": [100.0, 100.0, 102.0], "pe": [None, None, None]}
        with self.assertLogs("etl.csindex_hist", level="WARNING") as cm:
            result, store = self._run(cached, [("2026-10-13", 102.0, None), ("2026-10-14", 103.0, None)])
        self.assertIn("周末", cm.output[0])
        self.assertEqual(result["dates"], ["2026-10-09", "2026-10-13", "2026-10-14"])
        self.assertEqual(store["req"], ("20261013", "20261019"))
        self.assertEqual(store["000300.json"]["dates"], ["2026-10-09", "2026-10-13", "2026-10-14"])


class RateLimitRetryTest(unittest.TestCase):
    """HTTP 403 / 连接重置 → 按 RATE_LIMIT_BACKOFF（30 → 60 → 120 → 240 s）长退避重试，共 5 次；其它 4xx 直接抛出；
    用完退避仍失败 → 熔断，直到 reset_circuit()（编排器第二轮前），而不是只开 60 秒。"""

    def setUp(self):
        csindex_hist.reset_circuit()

    def tearDown(self):
        csindex_hist.reset_circuit()

    def test_backoff_schedule_constants(self):
        self.assertEqual(tuple(csindex_hist.RATE_LIMIT_BACKOFF), (30, 60, 120, 240))
        self.assertFalse(hasattr(csindex_hist, "CIRCUIT_OPEN_SEC"))                     # 不再按秒数自动关闭
        self.assertFalse(csindex_hist.circuit_open())

    def test_403_backs_off_then_retries(self):
        body = (FX / "csindex_perf_000300_trim.json").read_bytes()
        calls, slept = [], []

        def fake_http_get(url, **kw):
            calls.append((url, kw.get("max_attempts")))
            if len(calls) == 1:
                raise PanelError("HTTP 403 " + url)
            return body, {}

        with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                mock.patch.object(csindex_hist.time, "sleep", lambda s: slept.append(s)), \
                self.assertLogs("etl.csindex_hist", level="WARNING") as cm:
            parsed = csindex_hist._request("000300", csindex_hist.FULL_START, "20261023")
        self.assertEqual(len(calls), 2)
        self.assertEqual([c[1] for c in calls], [1, 1])                                 # http_get 自己不再重试
        self.assertEqual(slept, [30])                                                    # 第一次失败后等 30 s
        self.assertEqual(len(parsed["rows"]), 70)
        self.assertIn("403", cm.output[0])
        self.assertFalse(csindex_hist.circuit_open())

    def test_connection_reset_uses_full_schedule_then_opens_circuit(self):
        """runner 2026-10-24 的情形：每次都是 Connection reset by peer → 等 30/60/120/240 s 共 5 次，仍失败则熔断，
        本轮后续的中证请求直接抛「暂不可达」（不发请求、不睡），直到 reset_circuit() 才再给一轮完整退避。"""
        calls, slept = [], []

        def fake_http_get(url, **kw):
            calls.append(url)
            raise PanelError("重试 1 次仍失败 (ConnectionError: ('Connection aborted.', "
                             "ConnectionResetError(104, 'Connection reset by peer'))) " + url)

        with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                mock.patch.object(csindex_hist.time, "sleep", lambda s: slept.append(s)), \
                mock.patch.object(csindex_hist.time, "monotonic", lambda: 1000.0), \
                self.assertLogs("etl.csindex_hist", level="WARNING") as cm:
            with self.assertRaises(PanelError) as ctx:
                csindex_hist._request("000300", csindex_hist.FULL_START, "20261023")
        self.assertEqual(len(calls), 5)
        self.assertEqual(slept, [30, 60, 120, 240])
        self.assertIn("Connection reset", str(ctx.exception))
        self.assertTrue(any("不再请求中证" in m for m in cm.output), cm.output)
        self.assertTrue(csindex_hist.circuit_open())
        self.assertEqual(csindex_hist._circuit_open_since, 1000.0)
        # 熔断中：直接抛「暂不可达」，不发请求、不睡——30 s 后、1 小时后都一样（不再按秒数自动关闭）
        for later in (1030.0, 1061.0, 1000.0 + 3600):
            with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                    mock.patch.object(csindex_hist.time, "sleep", lambda s: self.fail("熔断中不应退避")), \
                    mock.patch.object(csindex_hist.time, "monotonic", lambda: later):
                with self.assertRaises(PanelError) as ctx2:
                    csindex_hist._request("H00300", csindex_hist.FULL_START, "20261023")
            self.assertIn("暂不可达", str(ctx2.exception))
            self.assertIn("熔断", str(ctx2.exception))
            self.assertIn("000300 5 次尝试仍失败", str(ctx2.exception))
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(slept), 4)
        # 编排器第二轮前 reset_circuit()：又是一轮完整退避
        csindex_hist.reset_circuit()
        self.assertFalse(csindex_hist.circuit_open())
        with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                mock.patch.object(csindex_hist.time, "sleep", lambda s: slept.append(s)), \
                mock.patch.object(csindex_hist.time, "monotonic", lambda: 1061.0), \
                self.assertLogs("etl.csindex_hist", level="WARNING"):
            with self.assertRaises(PanelError):
                csindex_hist._request("H00300", csindex_hist.FULL_START, "20261023")
        self.assertEqual(len(calls), 10)
        self.assertEqual(slept, [30, 60, 120, 240] * 2)
        self.assertTrue(csindex_hist.circuit_open())

    def test_success_after_backoff_keeps_circuit_closed(self):
        body = (FX / "csindex_perf_000300_trim.json").read_bytes()
        calls = []

        def fake_http_get(url, **kw):
            calls.append(url)
            if len(calls) <= 2:
                raise PanelError("HTTP 429 " + url)
            return body, {}

        with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                mock.patch.object(csindex_hist.time, "sleep", lambda s: None), \
                self.assertLogs("etl.csindex_hist", level="WARNING"):
            parsed = csindex_hist._request("000300", csindex_hist.FULL_START, "20261023")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(parsed["rows"]), 70)
        self.assertFalse(csindex_hist.circuit_open())                                    # 没用完退避就不熔断

    def test_404_raises_immediately_without_retry(self):
        calls = []

        def fake_http_get(url, **kw):
            calls.append(url)
            raise PanelError("HTTP 404 " + url)

        with mock.patch.object(csindex_hist, "http_get", fake_http_get), \
                mock.patch.object(csindex_hist.time, "sleep", lambda s: self.fail("404 不应退避")):
            with self.assertRaises(PanelError) as ctx:
                csindex_hist._request("000300", csindex_hist.FULL_START, "20261023")
        self.assertIn("HTTP 404", str(ctx.exception))
        self.assertEqual(len(calls), 1)
        self.assertFalse(csindex_hist.circuit_open())                                    # 非网络错误不熔断


class PurgeDatesTest(unittest.TestCase):
    def test_purge_dates_removes_only_listed_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / csindex_hist.CACHE_SUB).mkdir()
            for code, dates in (("000300", ["2026-09-30", "2026-10-01", "2026-10-08"]),
                                ("H00300", ["2026-09-30", "2026-10-08"])):
                obj = {"code": code, "dates": dates, "close": [float(i) for i in range(len(dates))],
                       "pe": [None] * len(dates), "fetchedAt": "2026-10-20T10:24:04+08:00"}
                (root / csindex_hist.CACHE_SUB / f"{code}.json").write_text(json.dumps(obj), encoding="utf-8")
            with mock.patch.object(common, "CACHE_DIR", root):
                removed = csindex_hist.purge_dates(["2026-10-01", "2026-10-05"])
                self.assertEqual(removed, {"000300.json": 1, "H00300.json": 0})
                a = json.loads((root / csindex_hist.CACHE_SUB / "000300.json").read_text(encoding="utf-8"))
                self.assertEqual(a["dates"], ["2026-09-30", "2026-10-08"])
                self.assertEqual(a["close"], [0.0, 2.0])
                self.assertEqual(a["fetchedAt"], "2026-10-20T10:24:04+08:00")
                b = json.loads((root / csindex_hist.CACHE_SUB / "H00300.json").read_text(encoding="utf-8"))
                self.assertEqual(b["dates"], ["2026-09-30", "2026-10-08"])
                # 只处理指定代码
                self.assertEqual(csindex_hist.purge_dates(["2026-09-30"], codes=["H00300"]), {"H00300.json": 1})
                with redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(csindex_hist.main(["--purge-dates", "2026-10-08", "--codes", "000300"]), 0)
                self.assertIn("000300.json: 删除 1 行", out.getvalue())
                a = json.loads((root / csindex_hist.CACHE_SUB / "000300.json").read_text(encoding="utf-8"))
                self.assertEqual(a["dates"], ["2026-09-30"])


if __name__ == "__main__":
    unittest.main()
