# -*- coding: utf-8 -*-
"""eastmoney_macro 的离线测试：信封解析、M2 / GDP / 70 城整形、翻页、月度缓存规则、增量 filter 合并。"""
import json
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from etl.common import PanelError
from etl.sources import eastmoney_macro as em

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return (FX / name).read_bytes()


def _rows(name):
    return em.parse_response(_fx(name))[2]


class ParseTest(unittest.TestCase):
    def test_envelope(self):
        count, pages, rows = em.parse_response(_fx("em_currency_supply_trim.json"))
        self.assertEqual((count, pages), (225, 1))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["REPORT_DATE"], "2026-09-01 00:00:00")

    def test_empty_result_and_bad_payloads(self):
        self.assertEqual(em.parse_response(_fx("em_empty.json")), (0, 0, []))
        with self.assertRaises(PanelError):
            em.parse_response(b"<html>403</html>")
        with self.assertRaises(PanelError):
            em.parse_response(json.dumps({"success": False, "code": 9999, "message": "err"}).encode())
        with self.assertRaises(PanelError):
            em.parse_response({"success": True, "code": 0, "result": {"data": "x"}})

    def test_rows_to_m2_trim_has_gap(self):
        rows = _rows("em_currency_supply_trim.json")
        with self.assertRaises(PanelError):
            em.rows_to_m2(rows)                                 # 2008-03 → 2026-07 之间缺月

    def test_rows_to_m2_contiguous(self):
        rows = _rows("em_currency_supply_trim.json")
        first = [r for r in rows if r["REPORT_DATE"] < "2009"]   # 2008-01..03
        out = em.rows_to_m2(first)
        self.assertEqual(out["months"], ["2008-01", "2008-02", "2008-03"])
        self.assertEqual(out["m2"][0], 417846.17)
        self.assertEqual(len(out["m1"]), 3)
        self.assertEqual(len(out["m0"]), 3)
        last = [r for r in rows if r["REPORT_DATE"] >= "2026"]
        out2 = em.rows_to_m2(last + [last[0]])                  # 重复行 → 后者覆盖
        self.assertEqual(out2["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(out2["m2"][-1], 3596289.7)

    def test_rows_to_gdp(self):
        out = em.rows_to_gdp(_rows("em_gdp_trim.json"))
        self.assertEqual(out["quarterEnds"][:4], ["2006-03-31", "2006-06-30", "2006-09-30", "2006-12-31"])
        self.assertEqual(out["cum"][:4], [47831.7, 101247.0, 158067.6, 222578.4])
        self.assertEqual(out["quarterEnds"][-1], "2026-09-30")
        self.assertEqual(out["cum"][-1], 1071071.2)
        self.assertEqual(len(out["cum"]), 7)
        with self.assertRaises(PanelError):
            em.rows_to_gdp([{"REPORT_DATE": "2026-02-01 00:00:00", "DOMESTICL_PRODUCT_BASE": 1.0}])

    def test_rows_to_house(self):
        with mock.patch.object(em, "MIN_CITIES", 6):
            out = em.rows_to_house(_rows("em_house_price_trim.json"))
        self.assertEqual(out["months"], ["2026-08", "2026-09"])
        self.assertEqual(len(out["cities"]), 6)
        for c in em.TIER1:
            self.assertIn(c, out["cities"])
        self.assertEqual(out["seq"]["北京"], [99.9, 100.1])

    def test_rows_to_house_requires_tier1_and_min_cities(self):
        rows = [r for r in _rows("em_house_price_trim.json") if r["CITY"] != "深圳"]
        with mock.patch.object(em, "MIN_CITIES", 1):
            with self.assertRaises(PanelError):
                em.rows_to_house(rows)
        with self.assertRaises(PanelError):
            em.rows_to_house(_rows("em_house_price_trim.json"))   # 6 < MIN_CITIES 68
        broken = [dict(r, SECOND_HOUSE_SEQUENTIAL=None) if r["CITY"] == "北京" and r["REPORT_DATE"].startswith("2026-09") else r
                  for r in _rows("em_house_price_trim.json")]
        with mock.patch.object(em, "MIN_CITIES", 1):
            with self.assertRaises(PanelError):
                em.rows_to_house(broken)


def _page(count, pages, rows):
    return json.dumps({"version": "x", "result": {"pages": pages, "data": rows, "count": count},
                       "success": True, "message": "ok", "code": 0}).encode()


class FetchReportTest(unittest.TestCase):
    def test_pages_until_count(self):
        calls = []

        def fake_get(url, timeout=30, headers=None):
            q = parse_qs(urlsplit(url).query)
            calls.append(q)
            self.assertEqual(headers, em.HEADERS)
            self.assertEqual(timeout, em.TIMEOUT)
            page = int(q["pageNumber"][0])
            rows = [{"REPORT_DATE": f"{2000 + page:04d}-01-01 00:00:00", "i": i} for i in range(500 if page < 3 else 200)]
            return _page(1200, 3, rows), {}

        with mock.patch.object(em, "http_get", fake_get):
            rows = em._fetch_report(em.REPORT_HOUSE)
        self.assertEqual(len(rows), 1200)
        self.assertEqual([c["pageNumber"][0] for c in calls], ["1", "2", "3"])
        self.assertEqual(calls[0]["pageSize"][0], "500")
        self.assertEqual(calls[0]["sortColumns"][0], "REPORT_DATE")
        self.assertEqual(calls[0]["sortTypes"][0], "1")
        self.assertEqual(calls[0]["reportName"][0], em.REPORT_HOUSE)
        self.assertNotIn("filter", calls[0])
        self.assertEqual(urlsplit(em.URL).hostname, "datacenter-web.eastmoney.com")

    def test_filter_param_and_empty(self):
        calls = []

        def fake_get(url, timeout=30, headers=None):
            calls.append(parse_qs(urlsplit(url).query))
            return _fx("em_empty.json"), {}

        with mock.patch.object(em, "http_get", fake_get):
            rows = em._fetch_report(em.REPORT_HOUSE, "(REPORT_DATE>='2026-09-01')")
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["filter"][0], "(REPORT_DATE>='2026-09-01')")


class CacheRuleTest(unittest.TestCase):
    def _run(self, fn, cached, due, body_fn):
        store = {}
        calls = []

        def fake_get(url, timeout=30, headers=None):
            calls.append(parse_qs(urlsplit(url).query))
            return body_fn(calls[-1]), {}

        with mock.patch.object(em, "cache_load", lambda sub, key: cached), \
                mock.patch.object(em, "cache_save", lambda sub, key, obj: store.update({(sub, key): obj})), \
                mock.patch.object(em, "monthly_refresh_due", lambda fm, release_day: due), \
                mock.patch.object(em, "monthly_fetch_month", lambda release_day: "2026-10"), \
                mock.patch.object(em, "iso_now", lambda: "2026-10-23T15:00:00+08:00"), \
                mock.patch.object(em, "http_get", fake_get):
            result = fn()
        return result, store, calls

    def test_m2_not_due_returns_cache_without_requests(self):
        cached = {"fetchedAt": "x", "fetchMonth": "2026-10", "months": ["2026-09"], "m2": [1.0], "m1": [1.0], "m0": [1.0]}
        result, store, calls = self._run(em.get_m2, cached, False, lambda q: b"")
        self.assertIs(result, cached)
        self.assertEqual(calls, [])
        self.assertEqual(store, {})

    def test_m2_due_fetches_once_and_sets_fetch_month(self):
        rows = [{"REPORT_DATE": f"2009-{m:02d}-01 00:00:00", "BASIC_CURRENCY": 100.0 + m, "CURRENCY": 1.0, "FREE_CASH": 1.0}
                for m in range(1, 13)]
        with mock.patch.object(em, "MIN_M2_ROWS", 12):
            result, store, calls = self._run(em.get_m2, None, True, lambda q: _page(len(rows), 1, rows))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["reportName"][0], em.REPORT_M2)
        self.assertEqual(result["fetchMonth"], "2026-10")
        self.assertEqual(result["fetchedAt"], "2026-10-23T15:00:00+08:00")
        self.assertEqual(result["months"][0], "2009-01")
        self.assertEqual(result["m2"][-1], 112.0)
        self.assertIn((em.CACHE_SUB, "m2.json"), store)

    def test_m2_fetch_failure_keeps_cache(self):
        cached = {"fetchedAt": "x", "fetchMonth": "2026-09", "months": ["2026-08"], "m2": [1.0], "m1": [1.0], "m0": [1.0]}

        def boom(q):
            raise PanelError("HTTP 500 x")

        with self.assertLogs("etl.eastmoney_macro", level="WARNING"):
            result, store, calls = self._run(em.get_m2, cached, True, boom)
        self.assertIs(result, cached)
        self.assertEqual(store, {})

    def test_gdp_due_fetches_and_merges(self):
        cached = {"fetchedAt": "x", "fetchMonth": "2026-09", "quarterEnds": ["2026-03-31", "2026-06-30"], "cum": [1.0, 2.0]}
        rows = [{"REPORT_DATE": "2026-06-01 00:00:00", "DOMESTICL_PRODUCT_BASE": 2.5},
                {"REPORT_DATE": "2026-09-01 00:00:00", "DOMESTICL_PRODUCT_BASE": 3.0}]
        with mock.patch.object(em, "MIN_GDP_ROWS", 1):
            result, store, calls = self._run(em.get_gdp, cached, True, lambda q: _page(2, 1, rows))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["quarterEnds"], ["2026-03-31", "2026-06-30", "2026-09-30"])
        self.assertEqual(result["cum"], [1.0, 2.5, 3.0])          # 同季新值覆盖
        self.assertEqual(result["fetchMonth"], "2026-10")

    def test_house_incremental_uses_filter_and_merges(self):
        trim_rows = _rows("em_house_price_trim.json")
        cities = sorted({r["CITY"] for r in trim_rows})
        cached = {"fetchedAt": "x", "fetchMonth": "2026-09", "months": ["2026-07", "2026-08"], "cities": cities,
                  "seq": {c: [100.0, 100.0] for c in cities}}
        rows_09 = [r for r in trim_rows if r["REPORT_DATE"].startswith("2026-09")]
        rows_08 = [r for r in trim_rows if r["REPORT_DATE"].startswith("2026-08")]
        with mock.patch.object(em, "MIN_CITIES", 6):
            result, store, calls = self._run(em.get_house_prices, cached, True,
                                             lambda q: _page(12, 1, rows_08 + rows_09))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["filter"][0], "(REPORT_DATE>='2026-08-01')")
        self.assertEqual(result["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(result["seq"]["北京"], [100.0, 99.9, 100.1])   # 08 被新值覆盖，09 新增
        self.assertEqual(result["cities"], cities)
        self.assertEqual(result["fetchMonth"], "2026-10")
        self.assertIn((em.CACHE_SUB, "house.json"), store)

    def test_house_cold_fetch_has_no_filter(self):
        rows = _rows("em_house_price_trim.json")
        with mock.patch.object(em, "MIN_CITIES", 6):
            result, store, calls = self._run(em.get_house_prices, None, True, lambda q: _page(12, 1, rows))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("filter", calls[0])
        self.assertEqual(result["months"], ["2026-08", "2026-09"])


if __name__ == "__main__":
    unittest.main()
