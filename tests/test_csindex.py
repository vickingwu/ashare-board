# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from etl.common import PanelError
from etl.sources import csindex

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "000300cons.xls"


class CsindexParserTest(unittest.TestCase):
    def test_parse_fixture(self):
        parsed = csindex.parse_cons_xls(FIXTURE.read_bytes())
        self.assertEqual(len(parsed["rows"]), 300)
        self.assertEqual(parsed["rows"][0], {"code": "000001", "name": "平安银行", "exchange": "深圳证券交易所"})
        self.assertEqual(parsed["asOf"], "2026-10-15")
        self.assertEqual(parsed["notes"], [])
        codes = [r["code"] for r in parsed["rows"]]
        self.assertIn("600519", codes)
        self.assertIn("000063", codes)   # 前导零补回
        self.assertEqual(len(set(codes)), 300)
        self.assertTrue(all(len(c) == 6 and c.isdigit() for c in codes))

    def test_bad_magic(self):
        with self.assertRaises(PanelError):
            csindex.parse_cons_xls(b"PK\x03\x04" + b"\x00" * 64)
        with self.assertRaises(PanelError):
            csindex.parse_cons_xls(b"<html>Not Found</html>")


if __name__ == "__main__":
    unittest.main()
