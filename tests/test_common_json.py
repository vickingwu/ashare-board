# -*- coding: utf-8 -*-
"""write_json_atomic 的离线测试：默认缩进、compact 模式不缩进且可往返、cache_age_days。"""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from etl import common

OBJ = {"panel": "x", "data": {"dates": ["2026-10-16", "2026-10-19"], "v": [1.5, None], "名称": "申万Ａ指"}}


class WriteJsonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "out.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_indented_and_round_trips(self):
        common.write_json_atomic(self.path, OBJ)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("\n ", text)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), OBJ)
        self.assertIn("申万Ａ指", text)                       # ensure_ascii=False

    def test_compact_has_single_trailing_newline_and_round_trips(self):
        common.write_json_atomic(self.path, OBJ, compact=True)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\n", text[:-1])
        self.assertNotIn(": ", text)
        self.assertNotIn(", ", text)
        self.assertEqual(json.loads(text), OBJ)

    def test_compact_is_smaller_than_default(self):
        common.write_json_atomic(self.path, OBJ)
        n_default = self.path.stat().st_size
        common.write_json_atomic(self.path, OBJ, compact=True)
        self.assertLess(self.path.stat().st_size, n_default)

    def test_no_tmp_file_left_behind(self):
        common.write_json_atomic(self.path, OBJ, compact=True)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["out.json"])


class CacheAgeDaysTest(unittest.TestCase):
    def test_age_with_fake_clock(self):
        now = datetime(2026, 10, 19, 12, 0, tzinfo=common.TZ)
        with mock.patch.object(common, "now_sh", lambda: now):
            self.assertAlmostEqual(common.cache_age_days("2026-10-09T12:00:00+08:00"), 10.0)
            self.assertAlmostEqual(common.cache_age_days("2026-09-18T12:00:00+08:00"), 31.0)
            self.assertAlmostEqual(common.cache_age_days("2026-10-19T00:00:00"), 0.5)      # naive = 北京时间

    def test_explicit_now_and_bad_input(self):
        now = datetime(2026, 10, 19, 12, 0, tzinfo=common.TZ)
        self.assertAlmostEqual(common.cache_age_days("2026-10-18T12:00:00+08:00", now=now), 1.0)
        self.assertIsNone(common.cache_age_days(None))
        self.assertIsNone(common.cache_age_days(""))
        self.assertIsNone(common.cache_age_days("not-a-date"))
        self.assertIsNone(common.cache_age_days(20261019))


if __name__ == "__main__":
    unittest.main()
