# -*- coding: utf-8 -*-
"""sge_gold 的离线测试：Dailyhq 解析（列顺序、周末 / 非正收盘、15 % 日变动护栏）、月均价、按月刷新与合并。"""
import json
import unittest
from pathlib import Path
from unittest import mock

from etl.common import PanelError
from etl.sources import sge_gold as g

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return (FX / name).read_bytes()


class ParseTest(unittest.TestCase):
    def test_trim_fixture(self):
        rows = g.parse_dailyhq(_fx("sge_dailyhq_trim.json"))
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows[0], ("2016-12-19", 262.76))
        self.assertEqual(rows[-1], ("2026-10-22", 880.3))
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(set(dates)), 60)

    def test_zero_close_and_weekend_dropped(self):
        body = {"time": [["2026-10-16", 1, 870.0, 1, 1], ["2026-10-17", 1, 871.0, 1, 1], ["2026-10-19", 1, 0, 1, 1],
                         ["2026-10-20", 1, 872.5, 1, 1], ["bad", 1, 2, 3, 4], ["2026-10-21", 1, 2]]}
        self.assertEqual(g.parse_dailyhq(body), [("2026-10-16", 870.0), ("2026-10-20", 872.5)])

    def test_big_jump_is_error(self):
        body = {"time": [["2026-10-16", 1, 870.0, 1, 1], ["2026-10-19", 1, 1044.0, 1, 1]]}   # +20 %
        with self.assertRaises(PanelError):
            g.parse_dailyhq(body)
        ok = {"time": [["2026-10-16", 1, 870.0, 1, 1], ["2026-10-19", 1, 990.0, 1, 1]]}      # +13.8 %
        self.assertEqual(len(g.parse_dailyhq(ok)), 2)
        # 相隔 > 14 天的两行（历史断档）不比较
        far = {"time": [["2026-09-01", 1, 870.0, 1, 1], ["2026-10-19", 1, 1200.0, 1, 1]]}
        self.assertEqual(len(g.parse_dailyhq(far)), 2)

    def test_bad_payloads(self):
        with self.assertRaises(PanelError):
            g.parse_dailyhq(b"<html>")
        with self.assertRaises(PanelError):
            g.parse_dailyhq(json.dumps({"data": []}).encode())
        with self.assertRaises(PanelError):
            g.parse_dailyhq({"time": [["2026-10-20", 1, 870.0, 1, 1], ["2026-10-19", 1, 871.0, 1, 1]]})   # 非升序

    def test_monthly_averages(self):
        rows = g.parse_dailyhq(_fx("sge_dailyhq_trim.json"))
        hist = {"dates": [r[0] for r in rows], "close": [r[1] for r in rows]}
        months, avg, n = g.monthly_averages(hist, complete_only=True, today="2026-10-23")
        self.assertEqual(months[0], "2016-12")
        self.assertAlmostEqual(avg[0], 261.77, delta=0.01)
        self.assertEqual(n[0], 10)
        self.assertNotIn("2026-10", months)                 # 当前月排除
        self.assertEqual(months[-1], "2026-09")
        months_all, _, _ = g.monthly_averages(hist, complete_only=False, today="2026-10-23")
        self.assertEqual(months_all[-1], "2026-10")


class GetAu9999Test(unittest.TestCase):
    def _run(self, cached, body, due, closed=True, today="2026-10-23"):
        store = {}
        posts = []

        def fake_post(url, *, data, timeout=30, headers=None):
            posts.append((url, data, headers))
            return body, {}

        with mock.patch.object(g, "cache_load", lambda sub, key: cached), \
                mock.patch.object(g, "cache_save", lambda sub, key, obj: store.update({(sub, key): obj})), \
                mock.patch.object(g, "monthly_refresh_due", lambda fm, release_day: due), \
                mock.patch.object(g, "monthly_fetch_month", lambda release_day: "2026-10"), \
                mock.patch.object(g, "market_closed", lambda: closed), \
                mock.patch.object(g, "today_str", lambda: today), \
                mock.patch.object(g, "iso_now", lambda: today + "T15:00:00+08:00"), \
                mock.patch.object(g, "http_post", fake_post):
            result = g.get_au9999()
        return result, store, posts

    def test_not_due_returns_cache(self):
        cached = {"fetchedAt": "x", "fetchMonth": "2026-10", "dates": ["2026-09-30"], "close": [907.32]}
        result, store, posts = self._run(cached, b"", due=False)
        self.assertIs(result, cached)
        self.assertEqual(posts, [])

    def test_due_posts_once_and_merges_keeping_old_dates(self):
        cached = {"fetchedAt": "x", "fetchMonth": "2026-09", "dates": ["2016-12-01", "2026-10-21"], "close": [250.0, 870.0]}
        body = {"time": [["2026-10-21", 1, 871.0, 1, 1], ["2026-10-22", 1, 880.3, 1, 1]]}
        result, store, posts = self._run(cached, body, due=True)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][0], g.URL)
        self.assertEqual(posts[0][1], {"instid": "Au99.99"})
        self.assertEqual(posts[0][2], g.HEADERS)
        self.assertEqual(result["dates"], ["2016-12-01", "2026-10-21", "2026-10-22"])   # 接口窗口外的旧日期保留
        self.assertEqual(result["close"], [250.0, 871.0, 880.3])                        # 同日新值覆盖
        self.assertEqual(result["fetchMonth"], "2026-10")
        self.assertIn((g.CACHE_SUB, g.CACHE_KEY), store)

    def test_cold_fetch_needs_min_rows_and_drops_today_before_close(self):
        body = {"time": [["2026-10-22", 1, 880.3, 1, 1], ["2026-10-23", 1, 882.0, 1, 1]]}
        with self.assertRaises(PanelError):
            self._run(None, body, due=True)
        with mock.patch.object(g, "MIN_ROWS_FULL", 1):
            result, _, _ = self._run(None, body, due=True, closed=False)
            self.assertEqual(result["dates"], ["2026-10-22"])
            result, _, _ = self._run(None, body, due=True, closed=True)
            self.assertEqual(result["dates"], ["2026-10-22", "2026-10-23"])


if __name__ == "__main__":
    unittest.main()
