# -*- coding: utf-8 -*-
"""coverage.compute() 的离线测试：120 只合成股票，市值 1000-i，手工划分指数集合。"""
import math
import unittest

from etl.panels import coverage


def _rows(codes):
    return [{"code": c, "name": f"股{c}", "exchange": "测试"} for c in codes]


class CoverageComputeTest(unittest.TestCase):
    def setUp(self):
        # 排名 i (1..120) 的代码为 6 位，市值 1000 - i，故代码顺序即市值排名
        self.codes = [f"{600000 + i:06d}" for i in range(1, 121)]
        rank = lambda a, b: self.codes[a - 1:b]  # noqa: E731  排名 a..b（含）
        self.cons = {
            "000300": _rows(rank(1, 20)),
            "000905": _rows(rank(21, 50)),
            "000852": _rows(rank(51, 80)),
            "932000": _rows(rank(81, 100)),
            "000985": _rows(self.codes),           # 全体 120 只
            "000510": _rows(rank(1, 10) + rank(21, 30) + rank(51, 55)),
        }
        self.quotes = {c: {"mvTotal": 1000.0 - (i + 1)} for i, c in enumerate(self.codes)}

    def test_cells_and_counts(self):
        data, notes = coverage.compute(self.cons, self.quotes)
        self.assertEqual(notes, [])
        self.assertEqual(len(data["cells"]), math.ceil(120 / 50))
        c0 = data["cells"][0]
        self.assertEqual(c0["rankFrom"], 1)
        self.assertEqual(c0["rankTo"], 50)
        self.assertEqual(c0["counts"]["000300"], 20)
        self.assertEqual(c0["counts"]["000905"], 30)
        self.assertEqual(c0["counts"]["000852"], 0)
        self.assertEqual(c0["counts"]["000985"], 50)
        self.assertEqual(c0["counts"]["000510"], 20)
        self.assertEqual(c0["uncovered"], 0)
        self.assertEqual(c0["mvMax"], 999.0)
        self.assertEqual(c0["mvMin"], 950.0)
        last = data["cells"][-1]
        self.assertEqual(last["rankTo"], 120)
        self.assertEqual(last["uncovered"], 20)
        self.assertEqual(data["uncovered"]["cells"], [0, 0, 20])

    def test_uncovered_and_universe(self):
        data, _ = coverage.compute(self.cons, self.quotes)
        self.assertEqual(data["uncovered"]["count"], 20)
        self.assertEqual(data["universe"]["count"], 120)
        self.assertEqual(data["universe"]["countListed"], 120)
        self.assertEqual(data["universe"]["sizeUnionCount"], 100)
        self.assertEqual(data["universe"]["count"], data["universe"]["sizeUnionCount"] + data["uncovered"]["count"])

    def test_overlap(self):
        data, _ = coverage.compute(self.cons, self.quotes)
        m = data["overlap"]["matrix"]
        codes = data["overlap"]["codes"]
        for i in range(6):
            self.assertEqual(m[i][i], 100.0)
        i_all, i_300, i_a500 = codes.index("000985"), codes.index("000300"), codes.index("000510")
        self.assertEqual(m[i_all][i_300], round(100 * 20 / 120, 1))
        self.assertEqual(m[i_300][i_all], 100.0)
        self.assertEqual(m[i_a500][i_300], 40.0)   # A500 25 只里 10 只属于 300
        self.assertEqual(m[i_300][i_a500], 50.0)   # 300 的 20 只里 10 只属于 A500

    def test_mv_ladder_sums_to_100(self):
        data, _ = coverage.compute(self.cons, self.quotes)
        self.assertEqual([x["code"] for x in data["mvLadder"]], ["000300", "000905", "000852", "932000", "uncovered"])
        total = sum(x["mvShare"] for x in data["mvLadder"])
        self.assertAlmostEqual(total, 100.0, delta=0.05)
        idx = {x["code"]: x for x in data["indexes"]}
        self.assertEqual(idx["000300"]["count"], 20)
        self.assertEqual(idx["000300"]["mvSum"], sum(1000.0 - i for i in range(1, 21)))
        self.assertEqual(len(idx["000300"]["top3"]), 3)
        self.assertEqual(idx["000300"]["top3"][0]["code"], self.codes[0])
        self.assertEqual(idx["000985"]["mvShare"], 100.0)

    def test_missing_quote_excluded_but_noted(self):
        quotes = dict(self.quotes)
        del quotes[self.codes[5]]              # 排名 6 的股票没有行情
        quotes[self.codes[6]] = {"mvTotal": None}
        data, notes = coverage.compute(self.cons, quotes)
        self.assertEqual(data["universe"]["count"], 118)
        self.assertEqual(data["universe"]["countListed"], 120)
        self.assertTrue(any("2 只全指成分未取到行情" in n for n in notes))
        self.assertEqual(data["cells"][-1]["rankTo"], 118)
        self.assertEqual(data["indexes"][0]["count"], 20)     # 成分数不受行情缺失影响
        self.assertEqual(data["uncovered"]["count"], 20)

    def test_not_in_universe_noted(self):
        cons = dict(self.cons)
        cons["932000"] = _rows(self.codes[80:100] + ["688999"])
        data, notes = coverage.compute(cons, self.quotes)
        idx = {x["code"]: x for x in data["indexes"]}
        self.assertEqual(idx["932000"]["notInUniverse"], 1)
        self.assertEqual(idx["932000"]["count"], 21)
        self.assertTrue(any("中证2000 有 1 只成分不在中证全指" in n for n in notes))
        self.assertEqual(data["universe"]["sizeUnionCount"], 100)


if __name__ == "__main__":
    unittest.main()
