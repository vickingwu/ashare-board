# -*- coding: utf-8 -*-
"""M4 新增纯函数：窗口年化、滚动年化 / 求和、累计值转当季、月份工具、链式定基、等权均值、几何均值。"""
import unittest

from etl import compute
from etl.common import PanelError


def _monthly(years, start_year=2010, growth=1.005):
    """合成月末序列：每月复利 growth，共 years×12+1 个点。"""
    dates, vals = [], []
    v = 100.0
    for k in range(years * 12 + 1):
        y, m = start_year + k // 12, k % 12 + 1
        dates.append(compute.month_end_date(f"{y:04d}-{m:02d}"))
        vals.append(v)
        v *= growth
    return dates, vals


def _legacy_window_cagr(dates, values, years):
    """widebase 旧版私有实现（搬到 compute 之前），用来确认行为一致。"""
    end_i = len(dates) - 1
    target = compute.shift_years(dates[end_i], -years)
    i = compute.first_index_on_or_after(dates, target)
    if i is None or values[i] is None:
        return None
    if (compute.parse_date(dates[i]) - compute.parse_date(target)).days > 30:
        return None
    return compute.cagr(values[i], values[end_i], compute.years_between(dates[i], dates[end_i]))


class WindowCagrTest(unittest.TestCase):
    def test_matches_legacy_widebase_helper(self):
        d, v = _monthly(12)
        for years in (10, 5, 3):
            self.assertAlmostEqual(compute.window_cagr(d, v, years), _legacy_window_cagr(d, v, years), places=9)
        self.assertAlmostEqual(compute.window_cagr(d, v, 10), (1.005 ** 12 - 1) * 100, delta=0.05)

    def test_too_short_history_is_none(self):
        d, v = _monthly(8)
        self.assertIsNone(compute.window_cagr(d, v, 10))
        self.assertIsNone(compute.window_cagr([], [], 10))
        self.assertIsNotNone(compute.window_cagr(d, v, 5))

    def test_none_at_start_is_none(self):
        d, v = _monthly(12)
        v2 = list(v)
        i = compute.first_index_on_or_after(d, compute.shift_years(d[-1], -10))
        v2[i] = None
        self.assertIsNone(compute.window_cagr(d, v2, 10))


class RollingTest(unittest.TestCase):
    def test_rolling_annualized(self):
        out = compute.rolling_annualized([100] * 5 + [121], 5, 252)
        self.assertEqual(out[:5], [None] * 5)
        self.assertAlmostEqual(out[5], ((1.21) ** (252 / 5) - 1) * 100, places=9)
        self.assertIsNone(compute.rolling_annualized([None, 100, 100, 100, 100, 121], 5)[5])
        self.assertIsNone(compute.rolling_annualized([0, 100, 100, 100, 100, 121], 5)[5])
        self.assertEqual(compute.rolling_annualized([1, 2], 0), [None, None])
        # 252 日窗口、翻倍 → 100 %
        self.assertAlmostEqual(compute.rolling_annualized([1.0] + [None] * 251 + [2.0], 252)[-1], 100.0, places=9)

    def test_rolling_sum(self):
        self.assertEqual(compute.rolling_sum([1, 2, None, 4], 2), [None, 3, None, None])
        self.assertEqual(compute.rolling_sum([1, 2, 3, 4], 4), [None, None, None, 10])
        self.assertEqual(compute.rolling_sum([1, 2], 0), [None, None])


class CumulativeTest(unittest.TestCase):
    DATES = ["2006-03-31", "2006-06-30", "2006-09-30", "2006-12-31"]
    CUM = [47831.7, 101247.0, 158067.6, 222578.4]

    def test_quarterly_from_cumulative(self):
        out = compute.cumulative_to_period(self.DATES, self.CUM)
        for got, want in zip(out, [47831.7, 53415.3, 56820.6, 64510.8]):
            self.assertAlmostEqual(got, want, delta=1e-6)
        self.assertAlmostEqual(sum(out), self.CUM[-1], delta=1e-6)

    def test_missing_predecessor_is_none(self):
        out = compute.cumulative_to_period(["2006-03-31", "2006-09-30", "2006-12-31"], [47831.7, 158067.6, 222578.4])
        self.assertEqual(out[0], 47831.7)
        self.assertIsNone(out[1])                       # 缺 Q2
        self.assertAlmostEqual(out[2], 64510.8, delta=1e-6)
        self.assertEqual(compute.cumulative_to_period(["2007-03-31", "2007-06-30"], [None, 10.0]), [None, None])
        # 跨年：下一年 Q1 不减上一年 Q4
        out = compute.cumulative_to_period(["2006-12-31", "2007-03-31"], [222578.4, 56000.0])
        self.assertIsNone(out[0])
        self.assertEqual(out[1], 56000.0)


class MonthToolsTest(unittest.TestCase):
    def test_month_end_date(self):
        self.assertEqual(compute.month_end_date("2026-02"), "2026-02-28")
        self.assertEqual(compute.month_end_date("2028-02"), "2028-02-29")
        self.assertEqual(compute.month_end_date("2026-12"), "2026-12-31")
        self.assertEqual(compute.month_end_date("2026-09"), "2026-09-30")

    def test_months_between(self):
        self.assertEqual(compute.months_between("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(compute.months_between("2026-02", "2026-02"), ["2026-02"])
        self.assertEqual(compute.months_between("2026-03", "2026-02"), [])
        self.assertEqual(len(compute.months_between("2006-01", "2016-11")), 131)

    def test_align_months(self):
        axis = ["2026-01", "2026-02", "2026-03"]
        self.assertEqual(compute.align_months(axis, ["2026-01", "2026-03"], [1.0, 3.0]), [1.0, None, 3.0])
        # 键可以是日期，取前 7 位
        self.assertEqual(compute.align_months(axis, ["2026-02-28"], [2.0]), [None, 2.0, None])

    def test_month_mean(self):
        months, means, counts = compute.month_mean(
            ["2016-12-29", "2016-12-30", "2017-01-03", "2017-01-04", "2017-01-05"], [262.0, 264.0, None, 265.0, 266.0])
        self.assertEqual(months, ["2016-12", "2017-01"])
        self.assertEqual(means, [263.0, 265.5])
        self.assertEqual(counts, [2, 2])
        self.assertEqual(compute.month_mean([], []), ([], [], []))


class ChainAndMeanTest(unittest.TestCase):
    def test_chain_link(self):
        out = compute.chain_link([101, 100, 99], 100)
        for got, want in zip(out, [101.0, 101.0, 99.99]):
            self.assertAlmostEqual(got, want, delta=1e-9)
        self.assertEqual(compute.chain_link([], 100), [])
        with self.assertRaises(PanelError):
            compute.chain_link([101, None, 99])
        with self.assertRaises(PanelError):
            compute.chain_link([0.0])

    def test_equal_weight_mean(self):
        self.assertEqual(compute.equal_weight_mean([[1, None], [3, 5]], 1), [2.0, 5.0])
        self.assertEqual(compute.equal_weight_mean([[1, None], [3, 5]], 2), [2.0, None])
        self.assertEqual(compute.equal_weight_mean([], 1), [])

    def test_geometric_mean(self):
        self.assertAlmostEqual(compute.geometric_mean([1.0, 4.0]), 2.0, places=12)
        self.assertAlmostEqual(compute.geometric_mean([1.02, 1.02, 1.02]), 1.02, places=12)
        self.assertIsNone(compute.geometric_mean([]))
        self.assertIsNone(compute.geometric_mean([1.0, 0.0]))
        self.assertIsNone(compute.geometric_mean([1.0, None]))


if __name__ == "__main__":
    unittest.main()
