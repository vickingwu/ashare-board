# -*- coding: utf-8 -*-
"""legulegu 的离线测试：token、代码后缀、_csrf 提取、PE 解析（必须取 addTtmPe 而不是等权 ttmPe）。"""
import json
import unittest
from datetime import date
from pathlib import Path

from etl.common import PanelError
from etl.sources import legulegu

FX = Path(__file__).resolve().parent / "fixtures"


class LeguleguTest(unittest.TestCase):
    def test_token_is_md5_of_iso_date(self):
        self.assertEqual(legulegu.token_for(date(2026, 10, 19)), "ce9a86695921835f053a92ab01fff737")

    def test_code_suffix(self):
        self.assertEqual(legulegu.to_lg_code("000300"), "000300.SH")
        self.assertEqual(legulegu.to_lg_code("399006"), "399006.SZ")
        with self.assertRaises(ValueError):
            legulegu.to_lg_code("abc")
        with self.assertRaises(ValueError):
            legulegu.to_lg_code("H30269")

    def test_csrf_from_page_head(self):
        tok = legulegu.extract_csrf((FX / "legulegu_page_head.html").read_text(encoding="utf-8"))
        self.assertEqual(len(tok), 36)
        self.assertEqual(tok.count("-"), 4)
        # 属性顺序颠倒也能取到
        self.assertEqual(legulegu.extract_csrf('<meta content="abc" name="_csrf"/>'), "abc")
        with self.assertRaises(PanelError):
            legulegu.extract_csrf('<html><meta name="_csrf_header" content="X-CSRF-TOKEN"/></html>')

    def test_parse_fixture_uses_weighted_pe(self):
        rows = legulegu.parse_pe((FX / "legulegu_pe_000300.json").read_bytes())
        self.assertEqual(len(rows), 259)
        self.assertEqual(rows[0]["date"], "2005-04-29")
        self.assertEqual(rows[0]["pe"], 13.99)          # addTtmPe；同一行 ttmPe（等权）是 31.53
        self.assertEqual(rows[0]["close"], 932.4)
        self.assertEqual(rows[-1]["date"], "2026-10-16")
        self.assertEqual(rows[-1]["pe"], 12.33)
        self.assertEqual(rows[-1]["close"], 4170.61)
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates))

    def test_parse_empty(self):
        self.assertEqual(legulegu.parse_pe((FX / "legulegu_pe_empty.json").read_bytes()), [])

    def test_parse_rejects_non_positive_and_garbage(self):
        with self.assertRaises(PanelError):
            legulegu.parse_pe(json.dumps({"data": [{"date": "2024-01-31", "addTtmPe": -1}]}).encode())
        with self.assertRaises(PanelError):
            legulegu.parse_pe(b"<html>403</html>")
        with self.assertRaises(PanelError):
            legulegu.parse_pe(json.dumps({"nodata": 1}).encode())

    def test_known_codes(self):
        self.assertIn("000300", legulegu.KNOWN_CODES)
        self.assertIn("000906", legulegu.KNOWN_CODES)
        self.assertNotIn("000985", legulegu.KNOWN_CODES)
        self.assertNotIn("H30269", legulegu.KNOWN_CODES)


if __name__ == "__main__":
    unittest.main()
