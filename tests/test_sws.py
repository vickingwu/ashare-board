# -*- coding: utf-8 -*-
"""sws 的离线测试：三个接口的解析（字符串数值、日期前 10 位、mv 空档）、合并、增量窗口、翻页、切窗、缓存规则。"""
import json
import unittest
from pathlib import Path
from unittest import mock

from etl.common import PanelError
from etl.sources import sws

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return (FX / name).read_bytes()


class ParseListTest(unittest.TestCase):
    def test_level1_list(self):
        items = sws.parse_list(_fx("sws_list_l1.json"))
        self.assertEqual(len(items), 31)
        self.assertEqual(items[0], ("801010", "农林牧渔"))
        self.assertEqual(items[-1], ("801980", "美容护理"))
        self.assertEqual(len({c for c, _ in items}), 31)

    def test_bad_code_or_non_json(self):
        with self.assertRaises(PanelError):
            sws.parse_list(json.dumps({"code": "500", "message": "err"}).encode())
        with self.assertRaises(PanelError):
            sws.parse_list(b"<html>502 Bad Gateway</html>")


class ParseTrendTest(unittest.TestCase):
    def test_fixture(self):
        rows = sws.parse_trend(_fx("sws_trend_801003_trim.json"))
        self.assertEqual(rows[0][0], "1999-12-30")
        self.assertEqual(rows[0][2], 0.0)                                   # 基日成交额 0，由调用方丢弃
        kept = [r for r in rows if r[2] > 0]
        self.assertEqual(kept[0][0], "2000-01-04")
        self.assertEqual(rows[-1], ("2026-10-16", 4130.8, 15497.8392, 1021.4598))
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), len(dates))
        self.assertEqual(len(rows), 45)

    def test_non_positive_close_dropped_and_dedupe(self):
        body = json.dumps({"code": "200", "data": [
            {"bargaindate": "2024-01-03", "closeindex": 10.0, "bargainsum": 5.0, "bargainamount": 1.0},
            {"bargaindate": "2024-01-02", "closeindex": 0, "bargainsum": 5.0, "bargainamount": 1.0},
            {"bargaindate": "2024-01-03", "closeindex": 11.0, "bargainsum": 6.0, "bargainamount": 2.0},
        ]}).encode()
        self.assertEqual(sws.parse_trend(body), [("2024-01-03", 11.0, 6.0, 2.0)])

    def test_bad_payload(self):
        with self.assertRaises(PanelError):
            sws.parse_trend(json.dumps({"code": "200", "data": {"x": 1}}).encode())
        with self.assertRaises(PanelError):
            sws.parse_trend(json.dumps({"code": 500, "data": []}).encode())


class ParseAnalysisTest(unittest.TestCase):
    def test_801003_fixture(self):
        count, rows = sws.parse_analysis(_fx("sws_analysis_801003_trim.json"))
        self.assertEqual(count, len(rows))
        self.assertEqual(count, 59)
        by_date = {r["date"]: r for r in rows}
        r = by_date["2026-10-16"]
        self.assertEqual((r["code"], r["name"]), ("801003", "申万Ａ指"))
        self.assertEqual(r["close"], 4130.8)
        self.assertEqual(r["mv"], 435541.95)
        self.assertEqual(r["turnoverSw"], 3.25)
        self.assertEqual(r["amtShare"], 100.0)
        self.assertIsNone(by_date["2011-01-04"]["mv"])
        self.assertIsNone(by_date["2011-01-04"]["turnoverSw"])
        self.assertEqual(by_date["2011-01-04"]["close"], 2729.8)
        self.assertEqual(by_date["2012-04-17"]["mv"], 76573.59)
        self.assertEqual(by_date["1999-12-30"]["mv"], 7926.65)
        self.assertTrue(all(len(x["date"]) == 10 for x in rows))
        self.assertTrue(all(isinstance(x["close"], float) for x in rows))

    def test_l2_fixture(self):
        count, rows = sws.parse_analysis(_fx("sws_analysis_l2_trim.json"))
        self.assertEqual(count, len(rows))
        today_rows = [r for r in rows if r["date"] == "2026-10-16"]
        self.assertEqual(len({r["code"] for r in today_rows}), 131)
        r = next(x for x in today_rows if x["code"] == "801012")
        self.assertEqual(r["close"], 2286.44)
        self.assertEqual(r["name"], "农产品加工")
        self.assertEqual(sum(1 for x in rows if x["code"] == "801012"), 6)
        self.assertTrue(any(x["code"] == "801011" for x in today_rows))

    def test_l1_single_day_fixture(self):
        count, rows = sws.parse_analysis(_fx("sws_analysis_l1_20261016.json"))
        self.assertEqual(count, 31)
        r = next(x for x in rows if x["code"] == "801010")
        self.assertEqual(r["close"], 2501.28)
        self.assertEqual(r["amtShare"], 1.58)
        self.assertEqual(r["mv"], 5687.52)
        self.assertAlmostEqual(sum(x["amtShare"] for x in rows), 99.97, places=2)

    def test_bad_payload(self):
        with self.assertRaises(PanelError):
            sws.parse_analysis(b"not json")
        with self.assertRaises(PanelError):
            sws.parse_analysis(json.dumps({"code": "500", "data": {"count": 0, "results": []}}).encode())
        with self.assertRaises(PanelError):
            sws.parse_analysis(json.dumps({"code": "200", "data": {"count": 0}}).encode())

    def test_num(self):
        self.assertEqual(sws._num("4130.80"), 4130.8)
        self.assertIsNone(sws._num(None))
        self.assertIsNone(sws._num(""))
        self.assertIsNone(sws._num("abc"))
        self.assertEqual(sws._num(3), 3.0)


def _row(code, name, d, close, amt=None, mv=None, tsw=None):
    return {"code": code, "name": name, "date": d, "close": close, "amtShare": amt, "mv": mv, "turnoverSw": tsw}


class MergeRowsTest(unittest.TestCase):
    def test_union_dates_newer_wins_none_where_missing(self):
        cached = {"dates": ["2026-10-08", "2026-10-09"], "codes": {
            "801010": {"name": "农林牧渔", "close": [100.0, 101.0], "amtShare": [1.0, 1.1], "mv": [10.0, 11.0], "turnoverSw": [2.0, 2.1]},
        }}
        rows = [_row("801010", "农林牧渔", "2026-10-09", 101.5, 1.15, 11.5, 2.15),
                _row("801010", "农林牧渔", "2026-10-16", 103.0, 1.3, 13.0, 2.3),
                _row("801030", "基础化工", "2026-10-16", 200.0, 4.0, 20.0, 3.0)]
        dates, codes = sws._merge_rows(cached, rows)
        self.assertEqual(dates, ["2026-10-08", "2026-10-09", "2026-10-16"])
        self.assertEqual(codes["801010"]["close"], [100.0, 101.5, 103.0])
        self.assertEqual(codes["801010"]["mv"], [10.0, 11.5, 13.0])
        self.assertEqual(codes["801030"]["close"], [None, None, 200.0])
        self.assertEqual(codes["801030"]["turnoverSw"], [None, None, 3.0])
        self.assertEqual(codes["801030"]["name"], "基础化工")
        self.assertEqual(list(codes), ["801010", "801030"])

    def test_no_cache(self):
        dates, codes = sws._merge_rows(None, [_row("801003", "申万Ａ指", "2011-01-04", 2729.8, 100.0, None, None)])
        self.assertEqual(dates, ["2011-01-04"])
        self.assertEqual(codes["801003"]["mv"], [None])
        self.assertEqual(codes["801003"]["close"], [2729.8])


class LevelHistoryTest(unittest.TestCase):
    def _run(self, index_type, cached, fetched, now_closed=False, today="2026-10-19", fresh=False):
        store = {}

        def fake_range(it, code, start, end):
            store["req"] = (it, code, start, end)
            return fetched

        with mock.patch.object(sws, "cache_load", lambda sub, key: cached), \
                mock.patch.object(sws, "cache_save", lambda sub, key, obj: store.update({key: obj})), \
                mock.patch.object(sws, "fetch_analysis_range", fake_range), \
                mock.patch.object(sws, "cache_is_fresh", lambda fa: fresh), \
                mock.patch.object(sws, "market_closed", lambda: now_closed), \
                mock.patch.object(sws, "today_str", lambda: today), \
                mock.patch.object(sws, "iso_now", lambda: today + "T10:00:00+08:00"):
            result = sws.get_level_history(index_type)
        return result, store

    def test_incremental_window_and_today_dropped_before_close(self):
        cached = {"level": sws.LEVEL1, "fetchedAt": "2026-10-10T17:00:00+08:00",
                  "dates": ["2026-10-08", "2026-10-09"], "codes": {
                      "801010": {"name": "农林牧渔", "close": [100.0, 101.0], "amtShare": [1.0, 1.0], "mv": [1.0, 1.0], "turnoverSw": [1.0, 1.0]}}}
        fetched = [_row("801010", "农林牧渔", "2026-10-16", 103.0, 1.0, 1.0, 1.0),
                   _row("801010", "农林牧渔", "2026-10-19", 104.0, 1.0, 1.0, 1.0)]
        result, store = self._run(sws.LEVEL1, cached, fetched, now_closed=False)
        self.assertEqual(store["req"], (sws.LEVEL1, "all", "2026-09-09", "2026-10-19"))   # 最后缓存日 − 30 天
        self.assertEqual(result["dates"], ["2026-10-08", "2026-10-09", "2026-10-16"])
        self.assertEqual(result["codes"]["801010"]["close"], [100.0, 101.0, 103.0])
        self.assertIn("analysis_l1.json", store)
        self.assertEqual(result["level"], sws.LEVEL1)
        result2, _ = self._run(sws.LEVEL1, cached, fetched, now_closed=True)
        self.assertEqual(result2["dates"][-1], "2026-10-19")

    def test_backfill_start_l1_and_l2(self):
        fetched = [_row("801010", "农林牧渔", "2026-10-16", 103.0)]
        _, store = self._run(sws.LEVEL1, None, fetched)
        self.assertEqual(store["req"][2], sws.L1_START)
        fetched2 = [_row("801012", "农产品加工", "2026-10-16", 2286.44)]
        result, store2 = self._run(sws.LEVEL2, None, fetched2)
        self.assertEqual(store2["req"], (sws.LEVEL2, "all", "2024-05-02", "2026-10-19"))  # 今天 − 900 天
        self.assertIn("analysis_l2.json", store2)
        self.assertEqual(result["codes"]["801012"]["mv"], [None])

    def test_fresh_cache_returns_without_fetch(self):
        cached = {"level": sws.LEVEL1, "fetchedAt": "x", "dates": ["2026-10-16"], "codes": {}}
        result, store = self._run(sws.LEVEL1, cached, [], fresh=True)
        self.assertIs(result, cached)
        self.assertNotIn("req", store)

    def test_full_fetch_empty_is_error_but_incremental_empty_keeps_cache(self):
        with self.assertRaises(PanelError):
            self._run(sws.LEVEL1, None, [])
        cached = {"level": sws.LEVEL1, "fetchedAt": "2026-10-10T17:00:00+08:00", "dates": ["2026-10-09"],
                  "codes": {"801010": {"name": "农林牧渔", "close": [1.0], "amtShare": [None], "mv": [None], "turnoverSw": [None]}}}
        with self.assertLogs("etl.sws", level="WARNING"):
            result, _ = self._run(sws.LEVEL1, cached, [])
        self.assertEqual(result["dates"], ["2026-10-09"])

    def test_non_positive_close_is_error_and_unknown_level(self):
        with self.assertRaises(PanelError):
            self._run(sws.LEVEL1, None, [_row("801010", "x", "2026-10-16", 0.0)])
        with self.assertRaises(PanelError):
            sws.get_level_history("三级行业")


class FetchTest(unittest.TestCase):
    def test_page_loop(self):
        calls = []

        def fake_get_json(url, params, referer, what="x"):
            calls.append(dict(params))
            page = params["page"]
            results = {1: [{"swindexcode": "801010", "bargaindate": "2026-10-16T08:00:00+08:00", "closeindex": "1"},
                           {"swindexcode": "801030", "bargaindate": "2026-10-16T08:00:00+08:00", "closeindex": "2"}],
                       2: [{"swindexcode": "801040", "bargaindate": "2026-10-16T08:00:00+08:00", "closeindex": "3"}]}[page]
            return {"code": "200", "data": {"count": 3, "results": results}}

        with mock.patch.object(sws, "_get_json", fake_get_json):
            rows = sws.fetch_analysis_rows(sws.LEVEL1, "all", "2026-10-16", "2026-10-16")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual([c["page"] for c in calls], [1, 2])
        self.assertEqual(calls[0]["page_size"], sws.PAGE_SIZE)
        self.assertEqual(calls[0]["index_type"], sws.LEVEL1)
        self.assertEqual(calls[0]["swindexcode"], "all")
        self.assertEqual(calls[0]["type"], "DAY")

    def test_window_splitting(self):
        calls = []

        def fake_get_json(url, params, referer, what="x"):
            calls.append((params["start_date"], params["end_date"]))
            return {"code": "200", "data": {"count": 0, "results": []}}

        with mock.patch.object(sws, "_get_json", fake_get_json):
            rows = sws.fetch_analysis_range(sws.LEVEL1, "all", "1999-12-30", "2001-12-31")
        self.assertEqual(rows, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ("1999-12-30", "2001-03-13"))
        self.assertEqual(calls[1], ("2001-03-14", "2001-12-31"))
        self.assertEqual(sws._windows("2026-10-01", "2026-10-19", 100), [("2026-10-01", "2026-10-19")])

    def test_get_index_list_requires_31_level1(self):
        body = json.loads(_fx("sws_list_l1.json"))
        store = {}
        with mock.patch.object(sws, "cache_load", lambda sub, key: None), \
                mock.patch.object(sws, "cache_save", lambda sub, key, obj: store.update({key: obj})), \
                mock.patch.object(sws, "_get_json", lambda url, params, referer, what="x": body):
            items = sws.get_index_list(sws.LEVEL1)
        self.assertEqual(len(items), 31)
        self.assertIn("list_一级行业.json", store)
        short = {"code": "200", "data": {"count": 30, "results": body["data"]["results"][:30]}}
        with mock.patch.object(sws, "cache_load", lambda sub, key: None), \
                mock.patch.object(sws, "_get_json", lambda url, params, referer, what="x": short):
            with self.assertRaises(PanelError):
                sws.get_index_list(sws.LEVEL1)

    def test_get_index_list_uses_fresh_cache(self):
        cached = {"indexType": sws.LEVEL1, "fetchedAt": "x", "items": [["801010", "农林牧渔"]]}
        with mock.patch.object(sws, "cache_load", lambda sub, key: cached), \
                mock.patch.object(sws, "cache_is_fresh", lambda fa: True), \
                mock.patch.object(sws, "_get_json", lambda *a, **k: self.fail("不应联网")):
            self.assertEqual(sws.get_index_list(sws.LEVEL1), [("801010", "农林牧渔")])


class AIndexTest(unittest.TestCase):
    def test_get_trend_drops_zero_amount_and_today_before_close(self):
        body = json.loads(_fx("sws_trend_801003_trim.json"))
        store = {}
        with mock.patch.object(sws, "cache_load", lambda sub, key: None), \
                mock.patch.object(sws, "cache_save", lambda sub, key, obj: store.update({key: obj})), \
                mock.patch.object(sws, "_get_json", lambda url, params, referer, what="x": body), \
                mock.patch.object(sws, "market_closed", lambda: False), \
                mock.patch.object(sws, "today_str", lambda: "2026-10-16"):
            t = sws.get_trend()
        self.assertEqual(t["dates"][0], "2000-01-04")
        self.assertEqual(t["dates"][-1], "2026-10-14")                 # 2026-10-16 当天、未收盘 → 丢弃
        self.assertEqual(len(t["dates"]), 43)
        self.assertIn("trend_801003.json", store)

    def test_get_a_index_left_joins_on_trend_dates(self):
        trend = {"code": "801003", "fetchedAt": "x", "dates": ["2011-01-04", "2012-04-17", "2026-10-16"],
                 "close": [2729.8, 2090.39, 4130.8], "amount": [1.0, 2.0, 15497.8392], "volume": [1.0, 2.0, 1021.4598]}
        analysis = {"level": sws.MARKET, "code": "801003", "fetchedAt": "x", "dates": ["2011-01-04", "2026-10-16"],
                    "codes": {"801003": {"name": "申万Ａ指", "close": [2729.8, 4130.8], "amtShare": [100.0, 100.0],
                                         "mv": [None, 435541.95], "turnoverSw": [None, 3.25]}}}
        with mock.patch.object(sws, "get_trend", lambda code=sws.A_INDEX, use_cache=True: trend), \
                mock.patch.object(sws, "_analysis_history", lambda *a, **k: analysis), \
                mock.patch.object(sws, "iso_now", lambda: "2026-10-19T10:00:00+08:00"):
            with self.assertLogs("etl.sws", level="INFO") as cm:
                a = sws.get_a_index()
        self.assertEqual(a["dates"], trend["dates"])
        self.assertEqual(a["mv"], [None, None, 435541.95])
        self.assertEqual(a["turnoverSw"], [None, None, 3.25])
        self.assertEqual(a["amount"], trend["amount"])
        self.assertEqual(a["name"], "申万Ａ指")
        self.assertTrue(any("流通市值缺失 2 日" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
