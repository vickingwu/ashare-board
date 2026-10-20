# -*- coding: utf-8 -*-
"""indheat.compute_panel() / build() 的离线测试：合成 31 个一级 × 700 日 + 5 个二级（含稀疏、无父级）。"""
import unittest
from datetime import date, timedelta
from unittest import mock

from etl.common import PanelError
from etl.panels import indheat


def _weekdays(n, end=date(2026, 10, 16)):
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


L1_CODES = [f"8{i:05d}" for i in range(1, 32)]
L2_CODES = ["900001", "900002", "900003", "900004", "900005"]


def _l1(n_days=700):
    dates = _weekdays(n_days)
    codes = {}
    for k, c in enumerate(L1_CODES):
        codes[c] = {
            "name": f"一级{k + 1}",
            "close": [1000.0 + k + i * 0.123456 for i in range(n_days)],
            "amtShare": [3.2258 for _ in range(n_days)],
            "mv": [None] * n_days,
            "turnoverSw": [None] * n_days,
        }
    return {"level": "一级行业", "code": "all", "dates": dates, "codes": codes}


def _l2(l1_dates):
    dates = l1_dates[-590:]                                        # 二级只回填了部分历史：窗口开头 10 日为空
    n = len(dates)
    codes = {}
    for k, c in enumerate(L2_CODES):
        close = [500.0 + k + i * 0.01 for i in range(n)]
        if c == "900003":                                          # 10 % 空值 → 剔除
            for i in range(0, n, 10):
                close[i] = None
        codes[c] = {"name": f"二级{k + 1}", "close": close, "amtShare": [None] * n, "mv": [None] * n, "turnoverSw": [None] * n}
    return {"level": "二级行业", "code": "all", "dates": dates, "codes": codes}


def _pm(missing=None, bad_parent=None):
    level2 = {c: {"name": f"二级{k + 1}", "parent": L1_CODES[k], "parentName": f"一级{k + 1}", "count": 5}
              for k, c in enumerate(L2_CODES)}
    if missing:
        del level2[missing]
    if bad_parent:
        level2[bad_parent]["parent"] = "899999"
    return {"fetchedAt": "2026-10-20T10:00:00+08:00", "level1": dict(zip(L1_CODES, [f"一级{k + 1}" for k in range(31)])), "level2": level2}


class IndheatComputeTest(unittest.TestCase):
    def setUp(self):
        self.l1 = _l1()
        self.l2 = _l2(self.l1["dates"])
        self.old_min_l2 = indheat.MIN_LEVEL2
        indheat.MIN_LEVEL2 = 3

    def tearDown(self):
        indheat.MIN_LEVEL2 = self.old_min_l2

    def test_shape_and_window(self):
        data = indheat.compute_panel(self.l1, self.l2, _pm(), indheat.DAYS)
        self.assertEqual(data["days"], 600)
        self.assertEqual(len(data["dates"]), 600)
        self.assertEqual(data["dates"], self.l1["dates"][-600:])
        self.assertEqual(len(data["level1"]), 31)
        self.assertEqual(len(data["level2"]), 4)
        self.assertEqual([d["code"] for d in data["dropped"]], ["900003"])
        self.assertEqual(data["periods"], [3, 5, 10, 20, 60, 120, 250])
        self.assertEqual(data["columns"], [20, 60, 120, 250])
        self.assertEqual(data["parentSource"], "乐咕乐股 申万行业总览")
        for e in data["level1"]:
            self.assertEqual(len(e["closes"]), 600)
            self.assertEqual(len(e["amtShare"]), 600)
            self.assertNotIn("parent", e)
        for e in data["level2"]:
            self.assertEqual(len(e["closes"]), 600)
            self.assertNotIn("amtShare", e)
            self.assertIn(e["parent"], L1_CODES)

    def test_rounding_and_reindex(self):
        data = indheat.compute_panel(self.l1, self.l2, _pm(), indheat.DAYS)
        for e in data["level1"] + data["level2"]:
            for v in e["closes"]:
                self.assertTrue(v is None or round(v, 2) == v)
        e1 = data["level1"][0]
        self.assertEqual(e1["closes"][-1], round(1000.0 + 699 * 0.123456, 2))
        self.assertEqual(e1["amtShare"][0], 3.23)
        # 二级起点晚于窗口起点：开头为 None，之后按日期对齐
        e2 = next(e for e in data["level2"] if e["code"] == "900001")
        self.assertIsNone(e2["closes"][0])
        self.assertEqual(e2["closes"][-1], round(500.0 + 589 * 0.01, 2))
        self.assertTrue(all(v is None for v in e2["closes"][:10]))
        first = data["dates"].index(self.l2["dates"][0])
        self.assertEqual(e2["closes"][first], 500.0)

    def test_missing_parent_rejected_then_fixed(self):
        with self.assertRaises(PanelError):
            indheat.compute_panel(self.l1, self.l2, _pm(missing="900002"), indheat.DAYS)
        with self.assertRaises(PanelError):
            indheat.compute_panel(self.l1, self.l2, _pm(bad_parent="900002"), indheat.DAYS)
        data = indheat.compute_panel(self.l1, self.l2, _pm(), indheat.DAYS)
        self.assertEqual(next(e for e in data["level2"] if e["code"] == "900002")["parent"], L1_CODES[1])

    def test_dropped_series_does_not_need_parent(self):
        data = indheat.compute_panel(self.l1, self.l2, _pm(missing="900003"), indheat.DAYS)
        self.assertEqual(data["dropped"][0]["code"], "900003")
        self.assertGreater(data["dropped"][0]["nullShare"], 5)

    def test_min_days_rejected(self):
        with self.assertRaises(PanelError):
            indheat.compute_panel(_l1(500), self.l2, _pm(), indheat.DAYS)

    def test_level1_sparse_or_short_rejected(self):
        l1 = _l1()
        for i in range(0, 700, 10):
            l1["codes"][L1_CODES[0]]["close"][i] = None
        with self.assertRaises(PanelError):
            indheat.compute_panel(l1, self.l2, _pm(), indheat.DAYS)
        l1 = _l1()
        del l1["codes"][L1_CODES[-1]]
        with self.assertRaises(PanelError):
            indheat.compute_panel(l1, self.l2, _pm(), indheat.DAYS)

    def test_too_few_level2_rejected(self):
        indheat.MIN_LEVEL2 = 5
        with self.assertRaises(PanelError):
            indheat.compute_panel(self.l1, self.l2, _pm(), indheat.DAYS)

    def test_fewer_days_than_requested_keeps_all(self):
        data = indheat.compute_panel(_l1(550), self.l2, _pm(), indheat.DAYS)
        self.assertEqual(data["days"], 550)
        self.assertEqual(len(data["dates"]), 550)


class IndheatBuildTest(unittest.TestCase):
    def setUp(self):
        self.l1 = _l1()
        self.l2 = _l2(self.l1["dates"])
        self.old_min_l2 = indheat.MIN_LEVEL2
        indheat.MIN_LEVEL2 = 3

    def tearDown(self):
        indheat.MIN_LEVEL2 = self.old_min_l2

    def _patches(self):
        return (mock.patch.object(indheat.sws, "get_level_history",
                                  side_effect=lambda lvl: self.l1 if lvl == indheat.sws.LEVEL1 else self.l2),
                mock.patch.object(indheat.legulegu, "get_sw_parent_map", return_value=_pm()))

    def test_build_default_days(self):
        p1, p2 = self._patches()
        with p1, p2:
            res = indheat.build()
        self.assertEqual(res["data"]["days"], 600)
        self.assertEqual(res["asOf"], "2026-10-16")
        self.assertEqual(len(res["sources"]), 2)
        self.assertTrue(any("二级3（900003）" in n for n in res["notes"]))
        self.assertFalse(any("文件体积限制" in n for n in res["notes"]))

    def test_build_size_budget_fallback(self):
        p1, p2 = self._patches()
        with p1, p2, mock.patch.object(indheat, "SIZE_BUDGET", 1000):
            res = indheat.build()
        self.assertEqual(res["data"]["days"], 500)
        self.assertEqual(len(res["data"]["dates"]), 500)
        self.assertTrue(any("文件体积限制" in n and "500" in n for n in res["notes"]))


if __name__ == "__main__":
    unittest.main()
