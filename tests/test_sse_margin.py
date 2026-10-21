# -*- coding: utf-8 -*-
"""sse_margin 的离线测试：解析（元 → 亿元、倒序 → 升序）、翻页、增量窗口、缓存规则、全量下限。"""
import json
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from etl.common import PanelError
from etl.sources import sse_margin as m

FX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return (FX / name).read_bytes()


class ParseTest(unittest.TestCase):
    def test_202610_fixture(self):
        total, page_count, rows = m.parse_margin(_fx("sse_margin_202610.json"))
        self.assertEqual((total, page_count), (9, 1))
        self.assertEqual(len(rows), 9)
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual((dates[0], dates[-1]), ("2026-10-08", "2026-10-20"))   # 9 行：10-08 … 10-20
        last = rows[-1]
        self.assertAlmostEqual(last["rzye"], 12579.7819, delta=1e-3)
        self.assertAlmostEqual(last["total"], 12766.2944, delta=1e-3)
        self.assertAlmostEqual(last["rqylje"], 186.5125, delta=1e-3)
        self.assertTrue(all(round(r["rzye"], 4) == r["rzye"] for r in rows))
        self.assertEqual(set(rows[0]), {"date", "rzye", "rqylje", "total"})

    def test_range_head_fixture(self):
        total, page_count, rows = m.parse_margin(_fx("sse_margin_range_head.json"))
        self.assertEqual((total, page_count), (4019, 3))
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["date"], "2018-07-19")
        self.assertEqual(rows[-1]["date"], "2026-10-20")
        self.assertAlmostEqual(rows[0]["rzye"], 5369.3585, delta=1e-3)

    def test_bad_payload(self):
        with self.assertRaises(PanelError):
            m.parse_margin(b"<html>403</html>")
        with self.assertRaises(PanelError):
            m.parse_margin(json.dumps({"pageHelp": {"total": 1}}).encode())
        with self.assertRaises(PanelError):
            m.parse_margin(json.dumps({"result": {"opDate": "20261020"}}).encode())
        # 日期不合法的行跳过，缺 pageHelp 时 total 取行数
        total, pc, rows = m.parse_margin({"result": [{"opDate": "bad", "rzye": 1}, {"opDate": "20261020", "rzye": 1e8}]})
        self.assertEqual((total, pc, len(rows)), (2, 1, 1))
        self.assertEqual(rows[0], {"date": "2026-10-20", "rzye": 1.0, "rqylje": None, "total": None})


def _page(page_no, page_count, dates_yyyymmdd, total):
    return json.dumps({
        "pageHelp": {"total": total, "pageSize": 2000, "pageCount": page_count, "pageNo": page_no},
        "result": [{"opDate": d, "rzye": 1e12 + i, "rqylje": 2e10, "rzrqjyzl": 1.02e12} for i, d in enumerate(dates_yyyymmdd)],
    }).encode()


class FetchRangeTest(unittest.TestCase):
    def test_three_pages(self):
        calls = []
        pages = {1: _page(1, 3, ["20261020", "20261019"], 5), 2: _page(2, 3, ["20261016", "20261015"], 5),
                 3: _page(3, 3, ["20261014", "20261015"], 5)}                 # 第 3 页与第 2 页重复一天 → 去重

        def fake_get(url, timeout=30, headers=None):
            q = parse_qs(urlsplit(url).query)
            calls.append(q)
            self.assertEqual(headers, m.HEADERS)
            self.assertEqual(timeout, m.TIMEOUT)
            return pages[int(q["pageHelp.pageNo"][0])], {}

        with mock.patch.object(m, "http_get", fake_get):
            rows = m.fetch_range("20100331", "20261021")
        self.assertEqual(len(calls), 3)
        self.assertEqual([c["pageHelp.pageNo"][0] for c in calls], ["1", "2", "3"])
        self.assertEqual([c["pageHelp.beginPage"][0] for c in calls], ["1", "2", "3"])
        self.assertEqual([c["pageHelp.endPage"][0] for c in calls], ["1", "2", "3"])
        self.assertEqual(calls[0]["pageHelp.pageSize"][0], "2000")
        self.assertEqual((calls[0]["beginDate"][0], calls[0]["endDate"][0]), ("20100331", "20261021"))
        self.assertEqual(calls[0]["isPagination"][0], "true")
        self.assertTrue(calls[0]["_"][0].isdigit())
        self.assertTrue(urlsplit(m.MARGIN_URL).hostname == "query.sse.com.cn")
        self.assertEqual([r["date"] for r in rows], ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"])

    def test_single_page(self):
        with mock.patch.object(m, "http_get", lambda url, timeout=30, headers=None: (_page(1, 1, ["20261020"], 1), {})) as _:
            rows = m.fetch_range("20261013", "20261021")
        self.assertEqual(len(rows), 1)


class HistoryTest(unittest.TestCase):
    def _run(self, cached, fetched_rows, fresh=False, today="20261021"):
        store = {}

        def fake_range(begin, end):
            store["req"] = (begin, end)
            return fetched_rows

        with mock.patch.object(m, "cache_load", lambda sub, key: cached), \
                mock.patch.object(m, "cache_save", lambda sub, key, obj: store.update({(sub, key): obj})), \
                mock.patch.object(m, "cache_is_fresh", lambda fa: fresh), \
                mock.patch.object(m, "fetch_range", fake_range), \
                mock.patch.object(m, "today_compact", lambda: today), \
                mock.patch.object(m, "iso_now", lambda: "2026-10-21T10:00:00+08:00"):
            result = m.get_margin_history()
        return result, store

    def test_incremental_window_and_merge(self):
        cached = {"fetchedAt": "2026-10-20T10:00:00+08:00", "dates": ["2026-10-16", "2026-10-19"],
                  "rzye": [12582.4995, 12590.4803], "rqylje": [186.7, 186.8], "total": [12769.2, 12777.2]}
        fetched = [{"date": "2026-10-19", "rzye": 12590.5, "rqylje": 186.9, "total": 12777.3},
                   {"date": "2026-10-20", "rzye": 12579.7819, "rqylje": 186.5125, "total": 12766.2944}]
        result, store = self._run(cached, fetched)
        self.assertEqual(store["req"], ("20261012", "20261021"))                # 最后缓存日 2026-10-19 − 7 天
        self.assertEqual(result["dates"], ["2026-10-16", "2026-10-19", "2026-10-20"])
        self.assertEqual(result["rzye"], [12582.4995, 12590.5, 12579.7819])     # 同日新值覆盖
        self.assertEqual(result["total"][-1], 12766.2944)
        self.assertEqual(result["fetchedAt"], "2026-10-21T10:00:00+08:00")
        self.assertIn((m.CACHE_SUB, m.CACHE_KEY), store)

    def test_fresh_cache_no_fetch(self):
        cached = {"fetchedAt": "x", "dates": ["2026-10-20"], "rzye": [1.0], "rqylje": [1.0], "total": [2.0]}
        result, store = self._run(cached, [], fresh=True)
        self.assertIs(result, cached)
        self.assertNotIn("req", store)

    def test_full_fetch_starts_at_first_day_and_needs_min_rows(self):
        rows = [{"date": f"{2010 + i // 250:04d}-{(i % 250) // 21 + 1:02d}-{(i % 21) + 1:02d}", "rzye": 100.0 + i,
                 "rqylje": 1.0, "total": 101.0} for i in range(4000)]
        rows = [dict(r, date=d) for d, r in sorted({r["date"]: r for r in rows}.items())]
        result, store = self._run(None, rows)
        self.assertEqual(store["req"], (m.FIRST_DAY, "20261021"))
        self.assertEqual(len(result["dates"]), len(rows))
        with self.assertRaises(PanelError):
            self._run(None, rows[:3000])                                         # < MIN_ROWS_FULL
        with self.assertRaises(PanelError):
            self._run(None, [])                                                  # 全量返回空

    def test_incremental_empty_keeps_cache_and_nonpositive_rejected(self):
        cached = {"fetchedAt": "2026-10-20T10:00:00+08:00", "dates": ["2026-10-19"], "rzye": [12590.48],
                  "rqylje": [186.8], "total": [12777.2]}
        with self.assertLogs("etl.sse_margin", level="WARNING"):
            result, _ = self._run(cached, [])
        self.assertEqual(result["dates"], ["2026-10-19"])
        with self.assertRaises(PanelError):
            self._run(cached, [{"date": "2026-10-20", "rzye": 0.0, "rqylje": 1.0, "total": 1.0}])


if __name__ == "__main__":
    unittest.main()
