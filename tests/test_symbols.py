# -*- coding: utf-8 -*-
import json
import unittest
from unittest import mock

from etl.common import PanelError
from etl.panels import symbols

FAKE_CODES = {
    "sz000001": {"name": "平安银行", "ipo": "1991-04-03", "out": "", "status": "1", "n": 5000},
    "sh600001": {"name": "邯郸钢铁", "ipo": "1998-01-22", "out": "2009-12-29", "status": "0", "n": 1332},
    "sz301718": {"name": "", "ipo": "2026-10-22", "out": "", "status": "1", "n": 1},
    "sh600519": {"name": "贵州 茅台", "ipo": "2001-08-27", "out": "", "status": "1", "n": 0},
}
FAKE_MANIFEST = {"version": 1, "kind": "full", "lastDate": "2026-10-22", "codes": FAKE_CODES}


class SymbolsRowsTest(unittest.TestCase):
    def setUp(self):
        self.rows = symbols.compute_rows(FAKE_CODES)

    def test_shape_and_order(self):
        self.assertEqual(len(self.rows), 4 + len(symbols.EXTRA))
        for r in self.rows:
            self.assertEqual(len(r), 5)
        stocks = self.rows[:4]
        # 先按市场再按代码：sh600001, sh600519, sz000001, sz301718
        self.assertEqual([r[3] + r[0] for r in stocks], ["sh600001", "sh600519", "sz000001", "sz301718"])
        self.assertTrue(all(r[2] == "stock" for r in stocks))

    def test_values(self):
        by = {r[3] + r[0]: r for r in self.rows}
        self.assertEqual(by["sh600001"][4], "2009-12-29")      # 退市日保留
        self.assertIsNone(by["sz000001"][4])                  # 在市 → None
        self.assertIsNone(by["sz301718"][1])                  # 空简称 → None
        self.assertEqual(by["sh600519"][1], "贵州茅台")        # 空格去掉；n == 0 的仍收录
        self.assertEqual(by["sh000300"][2], "index")
        self.assertEqual(by["sh510300"][2], "etf")
        self.assertIsNone(by["sh000300"][4])

    def test_extra_list(self):
        extra = self.rows[4:]
        self.assertEqual(len(extra), 34)
        self.assertEqual(sum(1 for r in extra if r[2] == "index"), 16)
        self.assertEqual(sum(1 for r in extra if r[2] == "etf"), 18)
        self.assertTrue(all(r[3] in ("sh", "sz") and len(r[0]) == 6 and r[0].isdigit() for r in extra))
        self.assertNotIn("932000", [r[0] for r in extra])
        keys = [r[3] + r[0] for r in self.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_duplicate_raises(self):
        with self.assertRaises(PanelError):
            symbols.compute_rows(FAKE_CODES, extra=[("sz000001", "重复", "index")])
        with self.assertRaises(PanelError):
            symbols.compute_rows({"xx600000": {"name": "坏前缀"}})
        with self.assertRaises(PanelError):
            symbols.compute_rows({}, extra=[("sh000300", "沪深300", "bond")])

    def test_clean_name(self):
        self.assertEqual(symbols.clean_name("万  科Ａ"), "万科A")
        self.assertEqual(symbols.clean_name(" ＴＣＬ科技 "), "TCL科技")
        self.assertIsNone(symbols.clean_name(""))
        self.assertIsNone(symbols.clean_name(None))
        self.assertEqual(symbols.market_of("bj430047"), "bj")


class SymbolsSizeTest(unittest.TestCase):
    def test_check_size_limits(self):
        rows = [[f"{i:06d}", "名", "stock", "sz", None] for i in range(symbols.MIN_STOCKS)]
        data = {"columns": symbols.COLUMNS, "rows": rows, "counts": {"stock": len(rows)}}
        self.assertLessEqual(symbols.check_size(data), symbols.MAX_BYTES)
        with self.assertRaises(PanelError):
            symbols.check_size({"columns": symbols.COLUMNS, "rows": rows[:10], "counts": {"stock": 10}})
        big = {"columns": symbols.COLUMNS, "rows": [[f"{i:06d}", "名" * 40, "stock", "sz", None] for i in range(symbols.MIN_STOCKS)],
               "counts": {"stock": symbols.MIN_STOCKS}}
        with self.assertRaises(PanelError):
            symbols.check_size(big)

    def test_compute_counts(self):
        codes = dict(FAKE_CODES)
        codes.update({f"sz2{i:05d}": {"name": f"股{i}", "out": ""} for i in range(symbols.MIN_STOCKS)})
        data = symbols.compute({"lastDate": "2026-10-22", "codes": codes})
        self.assertEqual(data["columns"], ["code", "name", "kind", "market", "out"])
        c = data["counts"]
        self.assertEqual(c["stock"], symbols.MIN_STOCKS + 4)
        self.assertEqual(c["index"], 16)
        self.assertEqual(c["etf"], 18)
        self.assertEqual(c["delisted"], 1)
        self.assertEqual(c["nameMissing"], 1)
        self.assertEqual(data["marketLastDate"], "2026-10-22")


class SymbolsBuildTest(unittest.TestCase):
    def test_build_envelope(self):
        codes = dict(FAKE_CODES)
        codes.update({f"sz2{i:05d}": {"name": f"股{i}", "out": ""} for i in range(symbols.MIN_STOCKS)})
        manifest = dict(FAKE_MANIFEST, codes=codes)
        with mock.patch("etl.market_state.ensure_store", return_value=manifest) as ensure:
            res = symbols.build()
        ensure.assert_called_once()
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], "2026-10-22")
        self.assertEqual(len(res["sources"]), 2)
        self.assertTrue(symbols.STATIC)
        self.assertTrue(symbols.COMPACT_JSON)
        self.assertEqual(symbols.PANEL_ID, "symbols")
        compact = json.dumps(res["data"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(compact), symbols.MAX_BYTES)
        text = " ".join(res["notes"]) + " ".join(res["sources"])
        for banned in ("不构成" + "投资建议", "灵" + "感", "个人" + "学习", "自" + "用", "免" + "责", "致" + "谢"):
            self.assertNotIn(banned, text)
        self.assertIn("932000", text)
        self.assertIn("已退市", text)


if __name__ == "__main__":
    unittest.main()
