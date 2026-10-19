# -*- coding: utf-8 -*-
"""csindex_hist 的离线测试：响应解析（周末行、peg）、空数据、异常码、增量合并与「收盘前丢弃当天」。"""
import json
import unittest
from pathlib import Path
from unittest import mock

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

    def test_empty_data(self):
        parsed = csindex_hist.parse_perf((FX / "csindex_perf_empty.json").read_bytes())
        self.assertEqual(parsed["rows"], [])

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

    def _run(self, cached, response_rows, now_closed=True, today="2026-10-19"):
        store = {}
        parsed = {"code": "000300", "name": "沪深300", "nameFull": "沪深300指数", "rows": response_rows}
        with mock.patch.object(csindex_hist, "cache_load", lambda sub, key: cached), \
                mock.patch.object(csindex_hist, "cache_save", lambda sub, key, obj: store.update({key: obj})), \
                mock.patch.object(csindex_hist, "_request", lambda code, s, e: store.update({"req": (s, e)}) or parsed), \
                mock.patch.object(csindex_hist, "cache_is_fresh", lambda fa: False), \
                mock.patch.object(csindex_hist, "market_closed", lambda: now_closed), \
                mock.patch.object(csindex_hist, "today_str", lambda: today), \
                mock.patch.object(csindex_hist, "today_compact", lambda: today.replace("-", "")), \
                mock.patch.object(csindex_hist, "iso_now", lambda: today + "T10:00:00+08:00"):
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
        self.assertEqual(store["req"], ("20260924", "20261019"))      # 最后缓存日 − 15 天
        self.assertIn("000300.json", store)

    def test_full_fetch_when_no_cache_and_empty_is_error(self):
        with self.assertRaises(PanelError):
            self._run(None, [])

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
                mock.patch.object(csindex_hist, "_request", lambda *a: called.append(a)):
            result = csindex_hist.get_index_history("000300")
        self.assertIs(result, cached)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
