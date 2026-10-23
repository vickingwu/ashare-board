# -*- coding: utf-8 -*-
"""sse_overview 的离线测试：每日概况解析、沪市 A 股合计、空结果、按日缓存（含空日标记）、往前回溯跳过周末。"""
import json
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from etl.common import PanelError
from etl.sources import sse_overview as so

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return (FX / name).read_bytes()


class ParseTest(unittest.TestCase):
    def test_fixture(self):
        parsed = so.parse_overview(_fx("sse_overview_20261022.json"))
        self.assertEqual(parsed["date"], "2026-10-22")
        self.assertEqual(sorted(parsed["rows"]), ["01", "02", "03", "11", "17"])
        self.assertEqual(parsed["rows"]["01"]["negoValue"], 481672.38)
        self.assertEqual(parsed["rows"]["01"]["listNum"], 1700.0)
        self.assertEqual(parsed["rows"]["11"]["negoValue"], 0.0)
        self.assertEqual(set(parsed["rows"]["03"]), {"listNum", "totalValue", "negoValue", "tradeAmt", "tradeVol", "negoToRate"})

    def test_a_share_totals(self):
        t = so.a_share_totals(so.parse_overview(_fx("sse_overview_20261022.json")))
        self.assertEqual(t["date"], "2026-10-22")
        self.assertAlmostEqual(t["negoValue"], 573200.34, delta=0.01)
        self.assertAlmostEqual(t["tradeAmt"], 7068.29, delta=0.01)
        self.assertAlmostEqual(t["turnoverPct"], 1.2331, delta=0.0005)
        self.assertEqual(t["listNum"], 2318)
        self.assertAlmostEqual(t["totalValue"], 501002.94 + 136022.72, delta=0.01)

    def test_empty_and_bad(self):
        self.assertIsNone(so.parse_overview(_fx("sse_overview_empty.json")))
        with self.assertRaises(PanelError):
            so.parse_overview(b"<html>")
        with self.assertRaises(PanelError):
            so.parse_overview(json.dumps({"pageHelp": {}}).encode())
        # 「-」→ None；缺 01 行 → 合计报错
        parsed = so.parse_overview({"result": [{"PRODUCT_CODE": "03", "TRADE_DATE": "20261022", "NEGO_VALUE": "-", "TRADE_AMT": "1.0"}]})
        self.assertIsNone(parsed["rows"]["03"]["negoValue"])
        with self.assertRaises(PanelError):
            so.a_share_totals(parsed)


class CacheTest(unittest.TestCase):
    def _run(self, fn, cache, fresh=True):
        calls = []

        def fake_get(url, timeout=30, headers=None):
            q = parse_qs(urlsplit(url).query)
            calls.append(q["SEARCH_DATE"][0])
            self.assertEqual(q["sqlId"][0], so.SQL_ID)
            self.assertEqual(q["PRODUCT_CODE"][0], so.PRODUCT_CODES)
            self.assertEqual(headers, so.HEADERS)
            date = q["SEARCH_DATE"][0]
            return (_fx("sse_overview_20261022.json") if date == "2026-10-22" else _fx("sse_overview_empty.json")), {}

        with mock.patch.object(so, "cache_load", lambda sub, key: cache.get(key)), \
                mock.patch.object(so, "cache_save", lambda sub, key, obj: cache.update({key: obj})), \
                mock.patch.object(so, "cache_is_fresh", lambda fa: fresh), \
                mock.patch.object(so, "iso_now", lambda: "2026-10-23T20:00:00+08:00"), \
                mock.patch.object(so, "http_get", fake_get):
            result = fn()
        return result, calls

    def test_latest_walks_back_skipping_weekend(self):
        cache = {}
        result, calls = self._run(lambda: so.get_latest_overview("2026-10-25"), cache)
        self.assertEqual(calls, ["2026-10-23", "2026-10-22"])            # 10-25 周日、10-24 周六不请求
        self.assertEqual(result["date"], "2026-10-22")
        self.assertEqual(cache["overview_20261023.json"]["empty"], True)
        self.assertEqual(cache["overview_20261022.json"]["date"], "2026-10-22")
        self.assertIn("rows", cache["overview_20261022.json"])

    def test_cached_date_no_request(self):
        cache = {"overview_20261022.json": {"fetchedAt": "x", "date": "2026-10-22",
                                           "rows": so.parse_overview(_fx("sse_overview_20261022.json"))["rows"]}}
        result, calls = self._run(lambda: so.get_overview("2026-10-22"), cache)
        self.assertEqual(calls, [])
        self.assertEqual(result["rows"]["01"]["negoValue"], 481672.38)

    def test_empty_marker_reused_while_fresh_then_reasked(self):
        cache = {"overview_20261023.json": {"empty": True, "fetchedAt": "x"}}
        result, calls = self._run(lambda: so.get_overview("2026-10-23"), cache, fresh=True)
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        result, calls = self._run(lambda: so.get_overview("2026-10-23"), cache, fresh=False)
        self.assertIsNone(result)
        self.assertEqual(calls, ["2026-10-23"])

    def test_no_data_within_window_returns_none(self):
        cache = {}
        with self.assertLogs("etl.sse_overview", level="WARNING"):
            result, calls = self._run(lambda: so.get_latest_overview("2026-10-30"), cache)
        self.assertIsNone(result)
        self.assertEqual(calls, ["2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27", "2026-10-26"])   # 10-25 周日


if __name__ == "__main__":
    unittest.main()
