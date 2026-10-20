# -*- coding: utf-8 -*-
"""compute 的自然年工具：年末收盘、年度涨跌幅、YTD、首个完整年、整年年化、排名、滚动均值、安全除法。

合成序列：每个交易日（周一至周五）一条，收盘 = 1000 × 1.1^k，k = 该日所属年份 − 1999（年末换挡），
所以每个自然年涨跌幅恰为 10 %。
"""
import unittest
from datetime import date, timedelta

from etl import compute


def _weekdays(start: str, end: str):
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _series(start: str, end: str = "2026-10-16"):
    dates = _weekdays(start, end)
    values = [1000.0 * (1.1 ** (int(d[:4]) - 1999)) for d in dates]
    return dates, values


YEARS = list(range(2000, 2026))


class YearEndTest(unittest.TestCase):
    def test_1999_12_30_is_base_for_2000(self):
        dates, values = _series("1999-12-30")
        self.assertEqual(dates[0], "1999-12-30")
        ye = compute.year_end_close(dates, values, 1999)
        self.assertEqual(ye, ("1999-12-31", 1000.0))             # 12-31 是周五
        self.assertIsNone(compute.year_end_close(dates, values, 1998))

    def test_year_end_is_last_non_null_in_window(self):
        dates = ["2021-12-10", "2021-12-20", "2021-12-30", "2021-12-31"]
        values = [1.0, 2.0, 3.0, None]
        self.assertEqual(compute.year_end_close(dates, values, 2021), ("2021-12-30", 3.0))
        self.assertIsNone(compute.year_end_close(["2021-12-10"], [1.0], 2021))   # 12-20 之前的不算

    def test_last_index_per_year(self):
        idx = compute.last_index_per_year(["2020-01-02", "2020-12-31", "2021-01-04"])
        self.assertEqual(idx, {2020: 1, 2021: 2})


class CalendarReturnsTest(unittest.TestCase):
    def test_full_history_gives_ten_percent_every_year(self):
        dates, values = _series("1999-12-30")
        rets = compute.calendar_year_returns(dates, values, YEARS)
        self.assertEqual(len(rets), 26)
        for r in rets:
            self.assertAlmostEqual(r, 10.0, places=6)
        self.assertEqual(compute.first_full_year(dates, values, YEARS), 2000)

    def test_series_from_2014_02_21_starts_in_2015(self):
        dates, values = _series("2014-02-21")
        rets = compute.calendar_year_returns(dates, values, YEARS)
        self.assertEqual(rets[:15], [None] * 15)                   # 2000–2014
        for r in rets[15:]:
            self.assertAlmostEqual(r, 10.0, places=6)
        self.assertEqual(compute.first_full_year(dates, values, YEARS), 2015)

    def test_series_from_2021_12_13_with_12_31_row_starts_in_2022(self):
        dates, values = _series("2021-12-13")
        self.assertIn("2021-12-31", dates)
        self.assertEqual(compute.first_full_year(dates, values, YEARS), 2022)
        rets = compute.calendar_year_returns(dates, values, [2021, 2022])
        self.assertIsNone(rets[0])
        self.assertAlmostEqual(rets[1], 10.0)

    def test_missing_base_gives_none(self):
        dates, values = _series("2021-12-13", "2026-10-16")
        keep = [(d, v) for d, v in zip(dates, values) if not ("2021-12-10" < d <= "2021-12-31")]
        d2 = [k[0] for k in keep]
        v2 = [k[1] for k in keep]
        self.assertEqual(compute.calendar_year_returns(d2, v2, [2022]), [None])
        self.assertEqual(compute.first_full_year(d2, v2, YEARS), 2023)

    def test_ytd(self):
        dates, values = _series("1999-12-30")
        self.assertEqual(dates[-1], "2026-10-16")
        self.assertAlmostEqual(compute.ytd_return(dates, values, 2026), 10.0, places=6)
        self.assertIsNone(compute.ytd_return(dates, values, 2027))
        self.assertIsNone(compute.ytd_return(["2026-03-01"], [5.0], 2026))     # 没有上年末基准

    def test_cagr_over_years(self):
        dates, values = _series("1999-12-30")
        c10 = compute.cagr_over_years(dates, values, 2025, 10)
        self.assertAlmostEqual(c10, 10.0, places=6)
        base = compute.year_end_close(dates, values, 2015)[1]
        end = compute.year_end_close(dates, values, 2025)[1]
        self.assertAlmostEqual(c10, compute.cagr(base, end, 10))
        self.assertAlmostEqual(compute.cagr_over_years(dates, values, 2025, 26), 10.0, places=6)
        self.assertIsNone(compute.cagr_over_years(dates, values, 2025, 27))    # 1998 年末不存在
        self.assertIsNone(compute.cagr_over_years(dates, values, 2025, 0))


class RankRollingTest(unittest.TestCase):
    def test_rank_desc_ties_share_smaller_rank(self):
        self.assertEqual(compute.rank_desc([3, None, 5, 3]), [2, None, 1, 2])
        self.assertEqual(compute.rank_desc([1.0, 2.0, 2.0, 0.5]), [3, 1, 1, 4])
        self.assertEqual(compute.rank_desc([None, None]), [None, None])
        self.assertEqual(compute.rank_desc([]), [])

    def test_count_not_none(self):
        self.assertEqual(compute.count_not_none([3, None, 5, 3]), 3)
        self.assertEqual(compute.count_not_none([]), 0)

    def test_rolling_mean(self):
        self.assertEqual(compute.rolling_mean([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(compute.rolling_mean([1, None, 3, 4], 2), [None, None, None, 3.5])
        self.assertEqual(compute.rolling_mean([1, 2], 3), [None, None])
        self.assertEqual(compute.rolling_mean([], 2), [])

    def test_safe_div_pct(self):
        self.assertIsNone(compute.safe_div_pct(1, 0))
        self.assertIsNone(compute.safe_div_pct(None, 2))
        self.assertIsNone(compute.safe_div_pct(1, None))
        self.assertEqual(compute.safe_div_pct(15497.84, 435541.95), 3.5583)
        self.assertEqual(compute.safe_div_pct(1, 3, 2), 33.33)


if __name__ == "__main__":
    unittest.main()
