# -*- coding: utf-8 -*-
import unittest

from etl.common import PanelError
from etl.sources import tencent


def _line(sym, name, code, price, mv_float, mv_total, ts="20261016141853"):
    f = [""] * 88
    f[0] = "1"
    f[tencent.IDX_NAME] = name
    f[tencent.IDX_CODE] = code
    f[tencent.IDX_PRICE] = price
    f[tencent.IDX_TIME] = ts
    f[tencent.IDX_MV_FLOAT] = mv_float
    f[tencent.IDX_MV_TOTAL] = mv_total
    return f'v_{sym}="{"~".join(f)}";'


SAMPLE = "\n".join([
    _line("sh601398", "工商银行", "601398", "8.40", "22647.43", "29938.13"),
    _line("sh600519", "贵州茅台", "600519", "1315.27", "16441.95", "16441.95", "20261016141850"),
    'v_pv_none_match="1";',
    _line("sz300750", "宁德时代", "300750", "273.90", "11669.83", "12674.04"),
])


class TencentTest(unittest.TestCase):
    def test_parse(self):
        q = tencent.parse_quote_text(SAMPLE)
        self.assertEqual(set(q), {"601398", "600519", "300750"})
        self.assertEqual(q["601398"]["mvTotal"], 29938.13)
        self.assertEqual(q["601398"]["mvFloat"], 22647.43)
        self.assertEqual(q["601398"]["quoteTime"], "20261016141853")
        self.assertEqual(q["601398"]["name"], "工商银行")
        self.assertEqual(q["600519"]["price"], 1315.27)
        self.assertEqual(len(q["600519"]["raw"]), 88)

    def test_to_symbol(self):
        self.assertEqual(tencent.to_symbol("600519"), "sh600519")
        self.assertEqual(tencent.to_symbol("000001"), "sz000001")
        self.assertEqual(tencent.to_symbol("300750"), "sz300750")
        self.assertEqual(tencent.to_symbol("430047"), "bj430047")
        self.assertEqual(tencent.to_symbol("920000"), "bj920000")
        with self.assertRaises(ValueError):
            tencent.to_symbol("12345")
        with self.assertRaises(ValueError):
            tencent.to_symbol("500001")

    def test_verify_field_index_ok(self):
        q = tencent.parse_quote_text(SAMPLE)
        lines = tencent.verify_field_index(q)
        self.assertEqual(len(lines), 3)
        self.assertIn("贵州茅台", lines[0])
        self.assertIn("16441.95", lines[0])

    def test_verify_field_index_fails_when_icbc_total_not_gt_float(self):
        bad = SAMPLE.replace("22647.43~29938.13", "29938.13~22647.43")
        q = tencent.parse_quote_text(bad)
        with self.assertRaises(PanelError):
            tencent.verify_field_index(q)

    def test_verify_field_index_fails_when_missing(self):
        q = tencent.parse_quote_text(_line("sh600519", "贵州茅台", "600519", "1", "1", "16441.95"))
        with self.assertRaises(PanelError):
            tencent.verify_field_index(q)

    def test_latest_quote_date(self):
        q = tencent.parse_quote_text(SAMPLE)
        self.assertEqual(tencent.latest_quote_date(q), ("2026-10-16", "1418"))


if __name__ == "__main__":
    unittest.main()
