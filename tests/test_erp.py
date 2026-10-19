# -*- coding: utf-8 -*-
"""erp.compute_panel() 的离线测试：130 个合成月份。"""
import unittest
from datetime import date, timedelta

from etl.common import PanelError
from etl.panels import erp


def _month_end_dates(n, start=(2010, 1)):
    out = []
    y, m = start
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-28")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _daily(start: date, end: date, fn):
    d = start
    dates, vals = [], []
    while d <= end:
        if d.weekday() < 5:
            dates.append(d.isoformat())
            vals.append(fn(d))
        d += timedelta(days=1)
    return dates, vals


class ErpComputeTest(unittest.TestCase):
    def setUp(self):
        self.pe_dates = _month_end_dates(130)                 # 2010-01 .. 2020-10
        self.pe = [10.0 + (i % 10) for i in range(130)]       # 10..19 循环
        self.y_dates, self.y = _daily(date(2010, 1, 1), date(2020, 11, 20), lambda d: 3.0 + d.month / 100.0)

    def test_identity_and_alignment(self):
        data = erp.compute_panel(self.pe_dates, self.pe, self.y_dates, self.y, "2020-11-20", 12.5)
        self.assertEqual(len(data["dates"]), 130)
        self.assertEqual(data["dates"][0], "2010-01-28")      # 用 PE 的月末日期
        for e, ey, y, pe in zip(data["erp"], data["ey"], data["y10"], data["pe"]):
            self.assertAlmostEqual(e, ey - y, places=3)
            self.assertAlmostEqual(ey, 100.0 / pe, places=3)
        s = data["summary"]
        self.assertEqual(s["nMonths"], 130)
        self.assertEqual(s["sampleStart"], "2010-01-28")
        self.assertEqual(s["latest"]["pe"], 12.5)
        self.assertEqual(s["latest"]["y10"], 3.11)            # 11 月的收益率
        self.assertEqual(s["latest"]["y10Date"], "2020-11-20")
        self.assertAlmostEqual(s["latest"]["erp"], 100 / 12.5 - 3.11, places=3)
        self.assertAlmostEqual(s["bands"]["p1"], s["mean"] + s["std"], places=3)
        self.assertAlmostEqual(s["bands"]["m2"], s["mean"] - 2 * s["std"], places=3)
        self.assertTrue(0 <= s["erpPct"] <= 100)
        self.assertTrue(0 <= s["ratioPct"] <= 100)

    def test_percentile_of_extreme_is_100(self):
        data = erp.compute_panel(self.pe_dates, self.pe, self.y_dates, self.y, "2020-11-20", 5.0)   # EY 20 → 远超历史
        self.assertEqual(data["summary"]["erpPct"], 100.0)
        self.assertEqual(data["summary"]["ratioPct"], 100.0)

    def test_months_missing_on_one_side_are_dropped(self):
        y_dates, y = _daily(date(2010, 7, 1), date(2020, 11, 20), lambda d: 3.0)   # 国债少了 2010 上半年
        data = erp.compute_panel(self.pe_dates, self.pe, y_dates, y, "2020-11-20", 12.5)
        self.assertEqual(data["summary"]["sampleStart"], "2010-07-28")
        self.assertEqual(data["summary"]["nMonths"], 124)

    def test_yield_out_of_range_rejected(self):
        y = list(self.y)
        y[100] = 12.0
        with self.assertRaises(PanelError):
            erp.compute_panel(self.pe_dates, self.pe, self.y_dates, y, "2020-11-20", 12.5)

    def test_too_short_rejected(self):
        with self.assertRaises(PanelError):
            erp.compute_panel(self.pe_dates[:50], self.pe[:50], self.y_dates, self.y, "2020-11-20", 12.5)


if __name__ == "__main__":
    unittest.main()
