# -*- coding: utf-8 -*-
"""etl/compute.py 的离线测试：分位（含等号/平局）、月末抽样、最新值拆分、归一、年化、拆解恒等式、合并。"""
import unittest

from etl import compute
from etl.common import PanelError


class DateHelpersTest(unittest.TestCase):
    def test_last_weekday_of_month(self):
        self.assertTrue(compute.is_last_weekday_of_month("2026-09-30"))    # 周三，9 月最后一个工作日
        self.assertFalse(compute.is_last_weekday_of_month("2026-10-16"))   # 周五，但 10 月还有工作日
        self.assertTrue(compute.is_last_weekday_of_month("2026-10-30"))    # 周五，10-31 是周六
        self.assertFalse(compute.is_last_weekday_of_month("2025-01-27"))   # 春节前最后交易日，不是最后工作日

    def test_years_between_and_shift(self):
        self.assertAlmostEqual(compute.years_between("2016-01-01", "2026-01-01"), 3653 / 365.25)
        self.assertEqual(compute.shift_years("2026-10-16", -5), "2021-10-16")
        self.assertEqual(compute.shift_years("2024-02-29", -1), "2023-02-28")

    def test_index_lookups(self):
        dates = ["2024-01-02", "2024-01-03", "2024-01-05"]
        self.assertEqual(compute.first_index_on_or_after(dates, "2024-01-03"), 1)
        self.assertEqual(compute.first_index_on_or_after(dates, "2024-01-04"), 2)
        self.assertIsNone(compute.first_index_on_or_after(dates, "2024-02-01"))
        self.assertEqual(compute.last_index_on_or_before(dates, "2024-01-04"), 1)
        self.assertIsNone(compute.last_index_on_or_before(dates, "2023-12-31"))

    def test_assert_ascending(self):
        compute.assert_ascending(["2024-01-01", "2024-01-02"])
        with self.assertRaises(PanelError):
            compute.assert_ascending(["2024-01-02", "2024-01-02"])


class SeriesTest(unittest.TestCase):
    def test_merge_series_newer_wins(self):
        d, v = compute.merge_series(["2024-01-01", "2024-01-02"], [1.0, 2.0],
                                    ["2024-01-02", "2024-01-03"], [2.5, 3.0])
        self.assertEqual(d, ["2024-01-01", "2024-01-02", "2024-01-03"])
        self.assertEqual(v, [1.0, 2.5, 3.0])

    def test_month_end_sample_picks_last_day_of_each_month(self):
        dates = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29", "2024-03-05"]
        vals = [1, 2, 3, None, 5]
        d, v = compute.month_end_sample(dates, vals)
        self.assertEqual(d, ["2024-01-31", "2024-02-01", "2024-03-05"])   # 02-29 为 None，退到 02-01
        self.assertEqual(v, [2, 3, 5])
        d2, v2 = compute.month_end_sample(dates, vals, complete_only=True)
        self.assertEqual(d2, ["2024-01-31", "2024-02-01"])                # 2024-03-05 不是 3 月最后工作日

    def test_split_latest_mid_month(self):
        dates = ["2026-07-31", "2026-08-31", "2026-09-30", "2026-10-16"]
        me_d, me_v, ld, lv = compute.split_latest(dates, [1, 2, 3, 4])
        self.assertEqual(me_d, ["2026-07-31", "2026-08-31", "2026-09-30"])
        self.assertEqual(me_v, [1, 2, 3])
        self.assertEqual((ld, lv), ("2026-10-16", 4))

    def test_split_latest_on_month_end(self):
        dates = ["2026-07-31", "2026-08-31", "2026-09-30"]
        me_d, me_v, ld, lv = compute.split_latest(dates, [1, 2, 3])
        self.assertEqual(me_d, dates)
        self.assertEqual((ld, lv), ("2026-09-30", 3))

    def test_split_latest_empty(self):
        self.assertEqual(compute.split_latest([], []), ([], [], None, None))
        self.assertEqual(compute.split_latest(["2026-01-30"], [None]), ([], [], None, None))

    def test_normalize_with_none_prefix(self):
        out = compute.normalize([None, None, 50.0, 100.0, 25.0], 2)
        self.assertEqual(out, [None, None, 100.0, 200.0, 50.0])
        self.assertEqual(compute.normalize([None, 1.0], 0), [None, None])

    def test_trailing_window(self):
        self.assertEqual(compute.trailing_window([1, 2, 3, 4], 2), [3, 4])
        self.assertIsNone(compute.trailing_window([1, 2, 3], 4))


class StatsTest(unittest.TestCase):
    def test_percentile_rank_inclusive(self):
        self.assertEqual(compute.percentile_rank([1, 2, 3, 4], 2), 50.0)
        self.assertEqual(compute.percentile_rank([1, 2, 3, 4], 5), 100.0)
        self.assertEqual(compute.percentile_rank([1, 2, 3, 4], 0), 0.0)
        self.assertEqual(compute.percentile_rank([2, 2, 2, 5], 2), 75.0)   # 平局全部计入
        self.assertIsNone(compute.percentile_rank([], 1))

    def test_cagr(self):
        self.assertAlmostEqual(compute.cagr(100, 200, 10), 7.17734625, places=6)
        self.assertIsNone(compute.cagr(100, 200, 0.4))
        self.assertIsNone(compute.cagr(0, 200, 5))
        self.assertIsNone(compute.cagr(None, 200, 5))

    def test_mean_std(self):
        m, s = compute.mean_std([2, 4, 4, 4, 5, 5, 7, 9])
        self.assertEqual(m, 5.0)
        self.assertAlmostEqual(s, 2.138089935, places=6)
        self.assertEqual(compute.mean_std([3.0]), (3.0, 0.0))
        self.assertEqual(compute.mean_std([]), (None, None))

    def test_median(self):
        self.assertEqual(compute.median([3, 1, 2]), 2)
        self.assertEqual(compute.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(compute.median([]))

    def test_max_drawdown(self):
        self.assertEqual(compute.max_drawdown([100, 120, 60, 90]), -50.0)
        self.assertEqual(compute.max_drawdown([1, 2, 3]), 0.0)
        self.assertIsNone(compute.max_drawdown([None, None]))


class DecomposeTest(unittest.TestCase):
    def test_known_numbers(self):
        r = compute.decompose(1000, 2000, 1000, 2500, 10, 15)
        self.assertEqual(r, {"totalReturn": 150.0, "priceReturn": 100.0, "dividend": 50.0,
                             "valuation": 50.0, "earnings": 50.0})

    def test_identity_holds_after_rounding(self):
        r = compute.decompose(932.4, 4170.61, 935.17, 6270.45, 13.99, 12.33)
        self.assertAlmostEqual(r["dividend"] + r["earnings"] + r["valuation"], r["totalReturn"], places=9)
        self.assertLess(r["valuation"], 0)

    def test_invalid_input(self):
        with self.assertRaises(PanelError):
            compute.decompose(0, 1, 1, 1, 1, 1)
        with self.assertRaises(PanelError):
            compute.decompose(1, 1, 1, 1, None, 1)


class AlignTest(unittest.TestCase):
    def test_align_by_month_intersection(self):
        a = (["2024-01-30", "2024-01-31", "2024-02-29", "2024-03-29"], [1, 2, 3, 4])
        b = (["2024-02-28", "2024-03-28", "2024-04-30"], [20, 30, 40])
        months, vals, dates = compute.align_by_month({"a": a, "b": b})
        self.assertEqual(months, ["2024-02", "2024-03"])
        self.assertEqual(vals["a"], [3, 4])
        self.assertEqual(vals["b"], [20, 30])
        self.assertEqual(dates["a"], ["2024-02-29", "2024-03-29"])
        self.assertEqual(dates["b"], ["2024-02-28", "2024-03-28"])

    def test_align_empty(self):
        months, vals, dates = compute.align_by_month({"a": ([], [])})
        self.assertEqual(months, [])
        self.assertEqual(vals, {"a": []})


if __name__ == "__main__":
    unittest.main()
