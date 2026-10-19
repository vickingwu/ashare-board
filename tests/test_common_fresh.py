# -*- coding: utf-8 -*-
"""cache_is_fresh / market_closed 的离线测试：用假时钟替换 common.now_sh。"""
import unittest
from datetime import datetime
from unittest import mock

from etl import common


def _sh(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=common.TZ)


class FreshnessTest(unittest.TestCase):
    def _fresh(self, fetched_at, now):
        with mock.patch.object(common, "now_sh", lambda: now):
            return common.cache_is_fresh(fetched_at)

    def test_none_is_stale(self):
        self.assertFalse(self._fresh(None, _sh(2026, 10, 19, 10)))
        self.assertFalse(self._fresh("", _sh(2026, 10, 19, 10)))

    def test_yesterday_is_stale(self):
        self.assertFalse(self._fresh("2026-10-18T17:00:00+08:00", _sh(2026, 10, 19, 10)))
        self.assertFalse(self._fresh("2026-10-18T17:00:00+08:00", _sh(2026, 10, 19, 20)))

    def test_today_before_close_now_before_close_is_fresh(self):
        self.assertTrue(self._fresh("2026-10-19T09:30:00+08:00", _sh(2026, 10, 19, 11, 45)))

    def test_today_before_close_now_after_close_is_stale(self):
        self.assertFalse(self._fresh("2026-10-19T09:30:00+08:00", _sh(2026, 10, 19, 16, 0)))
        self.assertFalse(self._fresh("2026-10-19T15:59:59+08:00", _sh(2026, 10, 19, 21, 0)))

    def test_today_after_close_is_fresh_for_the_rest_of_the_day(self):
        self.assertTrue(self._fresh("2026-10-19T16:00:00+08:00", _sh(2026, 10, 19, 16, 1)))
        self.assertTrue(self._fresh("2026-10-19T16:30:00+08:00", _sh(2026, 10, 19, 23, 59)))

    def test_malformed_is_stale(self):
        self.assertFalse(self._fresh("not-a-date", _sh(2026, 10, 19, 10)))
        self.assertFalse(self._fresh(20261019, _sh(2026, 10, 19, 10)))

    def test_naive_timestamp_is_treated_as_beijing(self):
        self.assertTrue(self._fresh("2026-10-19T17:00:00", _sh(2026, 10, 19, 18)))

    def test_market_closed(self):
        self.assertFalse(common.market_closed(_sh(2026, 10, 19, 15, 59)))
        self.assertTrue(common.market_closed(_sh(2026, 10, 19, 16, 0)))
        # UTC 08:00 = 北京 16:00
        from datetime import timezone
        self.assertTrue(common.market_closed(datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)))

    def test_explicit_now_argument(self):
        self.assertTrue(common.cache_is_fresh("2026-10-19T09:00:00+08:00", now=_sh(2026, 10, 19, 9, 5)))
        self.assertFalse(common.cache_is_fresh("2026-10-19T09:00:00+08:00", now=_sh(2026, 10, 19, 17)))


if __name__ == "__main__":
    unittest.main()
