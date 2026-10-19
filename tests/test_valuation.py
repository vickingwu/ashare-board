# -*- coding: utf-8 -*-
"""valuation.compute_one() 的离线测试：分位、窗口、样本短标记。"""
import unittest

from etl.common import PanelError
from etl.panels import valuation


def _month_ends(n, start_year=2020):
    out = []
    y, m = start_year, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-28")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


class ValuationComputeTest(unittest.TestCase):
    def test_24_months_short_sample(self):
        dates = _month_ends(24)
        pe = [10.0 + i for i in range(24)]            # 10..33
        one = valuation.compute_one(dates, pe, "2022-01-14", 33.0)
        self.assertEqual(one["pctAll"], 100.0)          # 等于最大值 → 含等号 100
        self.assertTrue(one["shortSample"])
        self.assertIsNone(one["pct10y"])
        self.assertIsNone(one["pct5y"])
        self.assertEqual(one["nMonths"], 24)            # 最新值不计入样本
        self.assertEqual(one["sampleStart"], "2020-01-28")
        self.assertEqual((one["min"], one["max"], one["median"]), (10.0, 33.0, 21.5))
        self.assertEqual(one["peNow"], 33.0)
        self.assertEqual(one["peDate"], "2022-01-14")
        self.assertEqual(len(one["monthly"]["dates"]), 24)

    def test_median_is_about_50(self):
        dates = _month_ends(24)
        pe = [10.0 + i for i in range(24)]
        one = valuation.compute_one(dates, pe, "2022-01-14", 21.5)
        self.assertEqual(one["pctAll"], 50.0)           # 12 个 ≤ 21.5

    def test_windows_with_long_history(self):
        dates = _month_ends(150, start_year=2010)
        pe = [20.0] * 150
        pe[-60:] = [10.0] * 60                          # 最近 5 年都是 10
        one = valuation.compute_one(dates, pe, "2022-07-15", 10.0)
        self.assertFalse(one["shortSample"])
        self.assertEqual(one["pctAll"], round(100 * 60 / 150, 1))
        self.assertEqual(one["pct10y"], 50.0)           # 最近 120 个里 60 个是 10
        self.assertEqual(one["pct5y"], 100.0)

    def test_rejects_bad_input(self):
        with self.assertRaises(PanelError):
            valuation.compute_one(_month_ends(5), [10.0] * 5, "2020-06-10", 10.0)        # 样本太少
        with self.assertRaises(PanelError):
            valuation.compute_one(_month_ends(24), [10.0] * 23 + [0.0], "2022-01-14", 10.0)   # 非正 PE
        with self.assertRaises(PanelError):
            valuation.compute_one(_month_ends(24), [10.0] * 24, "2022-01-14", -1.0)


if __name__ == "__main__":
    unittest.main()
