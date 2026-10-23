# -*- coding: utf-8 -*-
"""月度缓存规则 monthly_fetch_month / monthly_refresh_due 的离线测试（固定时钟，不联网）。"""
import unittest
from datetime import datetime

from etl import common
from etl.common import TZ, monthly_fetch_month, monthly_refresh_due


def _dt(y, m, d, hour=10):
    return datetime(y, m, d, hour, 0, tzinfo=TZ)


class FetchMonthTest(unittest.TestCase):
    def test_on_or_after_release_day_is_current_month(self):
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 23), release_day=20), "2026-10")
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 20), release_day=20), "2026-10")

    def test_before_release_day_is_previous_month(self):
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 5), release_day=20), "2026-09")
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 19), release_day=20), "2026-09")

    def test_release_day_one_is_always_current_month(self):
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 1), release_day=1), "2026-10")
        self.assertEqual(monthly_fetch_month(_dt(2026, 10, 31), release_day=1), "2026-10")

    def test_year_rolls_back_in_january(self):
        self.assertEqual(monthly_fetch_month(_dt(2026, 1, 5), release_day=20), "2025-12")

    def test_default_now_is_today_in_shanghai(self):
        now = common.now_sh()
        self.assertEqual(monthly_fetch_month(release_day=1), now.strftime("%Y-%m"))

    def test_naive_datetime_is_accepted(self):
        self.assertEqual(monthly_fetch_month(datetime(2026, 10, 23), release_day=20), "2026-10")


class RefreshDueTest(unittest.TestCase):
    def test_never_fetched_is_due(self):
        self.assertIs(monthly_refresh_due(None, _dt(2026, 10, 5), release_day=20), True)
        self.assertIs(monthly_refresh_due("", _dt(2026, 10, 5), release_day=20), True)

    def test_already_fetched_this_month_not_due(self):
        self.assertIs(monthly_refresh_due("2026-10", _dt(2026, 10, 23), release_day=20), False)
        self.assertIs(monthly_refresh_due("2026-10", _dt(2026, 10, 1), release_day=1), False)

    def test_previous_month_before_release_day_not_due(self):
        self.assertIs(monthly_refresh_due("2026-09", _dt(2026, 10, 19), release_day=20), False)

    def test_previous_month_on_release_day_due(self):
        self.assertIs(monthly_refresh_due("2026-09", _dt(2026, 10, 20), release_day=20), True)
        self.assertIs(monthly_refresh_due("2026-09", _dt(2026, 10, 23), release_day=20), True)

    def test_release_day_one_due_on_first_run_of_month(self):
        self.assertIs(monthly_refresh_due("2026-09", _dt(2026, 10, 1), release_day=1), True)

    def test_future_fetch_month_not_due(self):
        self.assertIs(monthly_refresh_due("2026-11", _dt(2026, 10, 23), release_day=20), False)

    def test_cold_fetch_before_release_day_then_refetch_once(self):
        """20 日前冷抓记成上月 → 20 日再抓一次 → 之后整月不再抓。"""
        first = monthly_fetch_month(_dt(2026, 10, 5), release_day=20)
        self.assertEqual(first, "2026-09")
        self.assertIs(monthly_refresh_due(first, _dt(2026, 10, 12), release_day=20), False)
        self.assertIs(monthly_refresh_due(first, _dt(2026, 10, 20), release_day=20), True)
        second = monthly_fetch_month(_dt(2026, 10, 20), release_day=20)
        self.assertEqual(second, "2026-10")
        self.assertIs(monthly_refresh_due(second, _dt(2026, 10, 31), release_day=20), False)
        self.assertIs(monthly_refresh_due(second, _dt(2026, 11, 20), release_day=20), True)


if __name__ == "__main__":
    unittest.main()
