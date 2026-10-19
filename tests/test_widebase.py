# -*- coding: utf-8 -*-
"""widebase.compute_panel() 的离线测试：合成三条起点不同的序列。"""
import unittest
from datetime import date, timedelta

from etl.common import PanelError
from etl.panels import widebase


def _weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def _series(code, tr, name, kind, start, end, v0, v1):
    dates = list(_weekdays(start, end))
    n = len(dates)
    # 几何增长：从 v0 到 v1
    vals = [v0 * (v1 / v0) ** (i / (n - 1)) for i in range(n)]
    return {"code": code, "trCode": tr, "name": name, "kind": kind, "dates": dates, "close": vals}


class WidebaseComputeTest(unittest.TestCase):
    def setUp(self):
        self.end = date(2026, 10, 16)
        self.a = _series("A", "HA", "甲", "tr", date(2016, 10, 17), self.end, 100.0, 200.0)   # 正好 10 年翻倍
        self.b = _series("B", "HB", "乙", "tr", date(2013, 12, 31), self.end, 1000.0, 3000.0)
        self.c = _series("C", None, "丙", "price", date(2024, 10, 16), self.end, 50.0, 60.0)      # 2 年

    def test_shared_axis_and_null_prefix(self):
        data = widebase.compute_panel([self.a, self.b, self.c])
        self.assertEqual(data["dates"][0], "2013-12-31")
        self.assertEqual(data["dates"][-1], "2026-10-16")
        self.assertEqual(data["dates"], sorted(set(data["dates"])))
        by = {s["code"]: s for s in data["series"]}
        for s in data["series"]:
            self.assertEqual(len(s["values"]), len(data["dates"]))
        i_a = data["dates"].index("2016-10-17")
        self.assertIsNone(by["A"]["values"][i_a - 1])
        self.assertEqual(by["A"]["values"][i_a], 100.0)
        self.assertEqual(by["A"]["start"], "2016-10-17")
        self.assertEqual(by["C"]["kind"], "price")
        self.assertIsNone(by["C"]["trCode"])

    def test_summary_numbers(self):
        data = widebase.compute_panel([self.a, self.b, self.c])
        s = {x["code"]: x for x in data["summary"]}
        self.assertEqual(s["A"]["multiple"], 2.0)
        self.assertAlmostEqual(s["A"]["cagrAll"], 7.18, delta=0.02)       # 2^(1/10) − 1
        self.assertAlmostEqual(s["A"]["cagr10y"], 7.18, delta=0.05)       # 窗口起点 ≈ 自身起点
        self.assertIsNotNone(s["A"]["cagr5y"])
        self.assertIsNotNone(s["A"]["cagr3y"])
        self.assertEqual(s["B"]["multiple"], 3.0)
        self.assertEqual(s["B"]["start"], "2013-12-31")
        self.assertIsNone(s["C"]["cagr10y"])                               # 2 年序列没有 10/5/3 年年化
        self.assertIsNone(s["C"]["cagr5y"])
        self.assertIsNone(s["C"]["cagr3y"])
        self.assertAlmostEqual(s["C"]["multiple"], 1.2, places=4)
        self.assertEqual(s["C"]["maxDrawdown"], 0.0)                       # 单调上升

    def test_rounding_and_lengths(self):
        data = widebase.compute_panel([self.a])
        vals = [v for v in data["series"][0]["values"] if v is not None]
        self.assertTrue(all(round(v, 2) == v for v in vals))
        self.assertEqual(data["summary"][0]["years"], round((self.end - date(2016, 10, 17)).days / 365.25, 2))

    def test_short_series_rejected(self):
        short = _series("S", None, "短", "price", date(2026, 9, 1), self.end, 1.0, 1.1)
        with self.assertRaises(PanelError):
            widebase.compute_panel([short])

    def test_non_positive_rejected(self):
        bad = dict(self.a)
        bad["close"] = list(self.a["close"])
        bad["close"][10] = 0.0
        with self.assertRaises(PanelError):
            widebase.compute_panel([bad])


if __name__ == "__main__":
    unittest.main()
