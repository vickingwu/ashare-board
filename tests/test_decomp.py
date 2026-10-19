# -*- coding: utf-8 -*-
"""decomp.compute_one() 的离线测试：36 个月合成数据（价格翻倍、全收益 ×2.5、PE 10 → 15）。"""
import unittest
from datetime import date, timedelta

from etl.common import PanelError
from etl.panels import decomp


def _daily(start: date, end: date, v0, v1):
    d = start
    dates = []
    while d <= end:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    n = len(dates)
    vals = [v0 * (v1 / v0) ** (i / (n - 1)) for i in range(n)]
    return dates, vals


def _month_ends(start: date, end: date):
    """每个月的最后一个工作日。"""
    out = []
    d = start
    while d <= end:
        nxt = d + timedelta(days=1)
        if nxt.month != d.month:
            e = d
            while e.weekday() >= 5:
                e -= timedelta(days=1)
            out.append(e.isoformat())
        d = nxt
    return out


class DecompComputeTest(unittest.TestCase):
    def setUp(self):
        s, e = date(2023, 1, 2), date(2025, 12, 31)
        self.price = _daily(s, e, 1000.0, 2000.0)
        self.tr = _daily(s, e, 1000.0, 2500.0)
        me = _month_ends(s, e)                                  # 36 个月末
        n = len(me)
        self.pe = (me, [10.0 * (15.0 / 10.0) ** (i / (n - 1)) for i in range(n)])

    def test_whole_period(self):
        one = decomp.compute_one(self.price, self.tr, self.pe)
        self.assertEqual(one["months"], 36)
        self.assertEqual(one["startDate"], "2023-01-31")
        self.assertEqual(one["endDate"], "2025-12-31")
        self.assertAlmostEqual(one["years"], 2.92, delta=0.01)
        # 起点是 1 月末而不是 1 月 2 日，所以涨幅略小于 100% / 150%，但恒等式必须成立
        self.assertAlmostEqual(one["dividend"] + one["earnings"] + one["valuation"], one["totalReturn"], places=6)
        self.assertEqual(one["peStart"], 10.0)
        self.assertEqual(one["peEnd"], 15.0)
        self.assertEqual(one["valuation"], 50.0)
        self.assertGreater(one["dividend"], 0)
        self.assertGreater(one["cagrTotal"], one["cagrPrice"])
        py = one["perYear"]
        self.assertAlmostEqual(py["dividend"] + py["earnings"] + py["valuation"], py["totalReturn"], delta=0.03)

    def test_by_year(self):
        one = decomp.compute_one(self.price, self.tr, self.pe)
        by = one["byYear"]
        self.assertEqual([b["year"] for b in by], [2023, 2024, 2025])
        self.assertTrue(by[0]["partial"])                       # 2023-01-31 → 2023-12-29 不满整年
        self.assertFalse(by[1]["partial"])
        self.assertFalse(by[2]["partial"])                      # 2024-12 → 2025-12 满整年
        self.assertEqual(by[0]["from"], "2023-01-31")
        self.assertEqual(by[1]["from"], by[0]["to"])
        self.assertEqual(by[2]["to"], "2025-12-31")
        for b in by:
            self.assertAlmostEqual(b["dividend"] + b["earnings"] + b["valuation"], b["totalReturn"], places=6)

    def test_pe_start_moves_period(self):
        me, pe = self.pe
        one = decomp.compute_one(self.price, self.tr, (me[12:], pe[12:]))
        self.assertEqual(one["startDate"], "2024-01-31")
        self.assertEqual(one["months"], 24)
        self.assertEqual([b["year"] for b in one["byYear"]], [2024, 2025])

    def test_start_in_december_skips_empty_year(self):
        me, pe = self.pe
        one = decomp.compute_one(self.price, self.tr, (me[11:], pe[11:]))     # 从 2023-12 起
        self.assertEqual(one["startDate"], "2023-12-29")
        self.assertEqual([b["year"] for b in one["byYear"]], [2024, 2025])
        self.assertFalse(one["byYear"][0]["partial"])

    def test_too_short_rejected(self):
        me, pe = self.pe
        with self.assertRaises(PanelError):
            decomp.compute_one(self.price, self.tr, (me[-20:], pe[-20:]))


if __name__ == "__main__":
    unittest.main()
