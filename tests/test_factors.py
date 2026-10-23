# -*- coding: utf-8 -*-
"""factors.compute_panel() / build() 的离线测试：六条合成随机游走 + 两个基准（MIN_ROWS / SIZE_BUDGET 打补丁）。"""
import json
import random
import unittest
from datetime import date, timedelta
from unittest import mock

from etl.common import PanelError
from etl.panels import factors

END = date(2026, 10, 22)


def _weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def _walk(seed: int, start: date, v0: float = 1000.0, drift: float = 0.0003, vol: float = 0.01):
    rng = random.Random(seed)
    dates = list(_weekdays(start, END))
    vals = []
    v = v0
    for _ in dates:
        v *= 1 + drift + rng.gauss(0, vol)
        vals.append(round(max(v, 1.0), 2))
    return {"dates": dates, "close": vals}


def make_world(start=date(2020, 10, 22)):
    hists = {}
    for i, (_k, _n, code, _kind, _u) in enumerate(factors.FACTORS):
        hists[code] = _walk(100 + i, start)
    bench_tr = _walk(200, start)
    bench_price = _walk(201, start, v0=800.0)
    return hists, bench_tr, bench_price


class FactorsComputeTest(unittest.TestCase):
    def setUp(self):
        self.hists, self.tr, self.price = make_world()
        # 一条起点更晚 → 共同起点后移
        late = self.hists["930847"]
        cut = 40
        self.hists["930847"] = {"dates": late["dates"][cut:], "close": late["close"][cut:]}
        self.late_start = late["dates"][cut]
        with mock.patch.object(factors, "MIN_ROWS", 1000):
            self.data = factors.compute_panel(self.hists, self.tr, self.price)

    def test_common_start_and_lengths(self):
        self.assertEqual(self.data["start"], self.late_start)
        self.assertEqual(self.data["dates"][0], self.late_start)
        self.assertEqual(self.data["dates"], sorted(set(self.data["dates"])))
        n = len(self.data["dates"])
        for s in self.data["series"]:
            self.assertEqual(len(s["values"]), n)
            self.assertIsNotNone(s["values"][0], s["key"])
        self.assertEqual(len(self.data["bench"]["tr"]["values"]), n)
        self.assertEqual(len(self.data["bench"]["price"]["values"]), n)
        self.assertEqual(self.data["bench"]["tr"]["code"], "H00985")
        self.assertEqual(self.data["bench"]["price"]["code"], "000985")

    def test_series_meta_and_rounding(self):
        self.assertEqual([s["key"] for s in self.data["series"]], [f[0] for f in factors.FACTORS])
        kinds = {s["key"]: s["kind"] for s in self.data["series"]}
        self.assertEqual(kinds["lowvol"], "price")
        self.assertEqual(kinds["dividend"], "tr")
        self.assertEqual({s["key"]: s["universe"] for s in self.data["series"]}["momentum"], "沪深300")
        for s in self.data["series"]:
            self.assertTrue(all(round(v, 2) == v for v in s["values"] if v is not None))

    def test_values_start_at_common_start(self):
        # 起点之前的行被裁掉：红利序列首值 = 自身在共同起点当天的收盘
        div = self.hists["H00922"]
        i = div["dates"].index(self.late_start)
        s = {x["key"]: x for x in self.data["series"]}["dividend"]
        self.assertEqual(s["values"][0], round(div["close"][i], 2))

    def test_summary(self):
        self.assertEqual(len(self.data["summary"]), 6)
        for s in self.data["summary"]:
            self.assertEqual(s["start"], self.late_start)
            self.assertEqual(s["end"], END.isoformat())
            self.assertAlmostEqual(s["multiple"], round(s["last"] / s["first"], 4), places=4)
            self.assertLessEqual(s["maxDrawdown"], 0.0)
            self.assertIsNone(s["cagr10y"])                               # 6 年序列
            self.assertIsNotNone(s["cagr5y"])
            self.assertIsNotNone(s["cagr3y"])
            self.assertIsNotNone(s["cagrAll"])
            self.assertIn("code", s)
            self.assertIn("kind", s)

    def test_lagging(self):
        self.assertEqual(self.data["lagging"], [])
        hists = {c: dict(h) for c, h in self.hists.items()}
        h = hists["H20355"]
        hists["H20355"] = {"dates": h["dates"][:-2], "close": h["close"][:-2]}
        with mock.patch.object(factors, "MIN_ROWS", 1000):
            data = factors.compute_panel(hists, self.tr, self.price)
        self.assertEqual(data["lagging"], ["growth"])
        s = {x["key"]: x for x in data["series"]}["growth"]
        self.assertIsNone(s["values"][-1])
        self.assertEqual(data["dates"][-1], END.isoformat())

    def test_missing_days_are_null(self):
        hists = {c: dict(h) for c, h in self.hists.items()}
        h = hists["H00922"]
        gone = h["dates"][500]
        hists["H00922"] = {"dates": h["dates"][:500] + h["dates"][501:], "close": h["close"][:500] + h["close"][501:]}
        with mock.patch.object(factors, "MIN_ROWS", 1000):
            data = factors.compute_panel(hists, self.tr, self.price)
        i = data["dates"].index(gone)
        self.assertIsNone({x["key"]: x for x in data["series"]}["dividend"]["values"][i])

    def test_five_factors_rejected(self):
        hists = dict(self.hists)
        del hists["H20356"]
        with mock.patch.object(factors, "MIN_ROWS", 1000), self.assertRaises(PanelError):
            factors.compute_panel(hists, self.tr, self.price)

    def test_short_series_rejected(self):
        with self.assertRaises(PanelError):                               # MIN_ROWS = 4000，合成序列只有 ~1500 行
            factors.compute_panel(self.hists, self.tr, self.price)

    def test_non_positive_rejected(self):
        hists = {c: dict(h) for c, h in self.hists.items()}
        bad = dict(hists["931156"])
        bad["close"] = list(bad["close"])
        bad["close"][10] = 0.0
        hists["931156"] = bad
        with mock.patch.object(factors, "MIN_ROWS", 1000), self.assertRaises(PanelError):
            factors.compute_panel(hists, self.tr, self.price)

    def test_missing_bench_rejected(self):
        with mock.patch.object(factors, "MIN_ROWS", 1000), self.assertRaises(PanelError):
            factors.compute_panel(self.hists, self.tr, {"dates": [], "close": []})

    def test_size_budget(self):
        with mock.patch.object(factors, "MIN_ROWS", 1000), mock.patch.object(factors, "SIZE_BUDGET", 1000), \
                self.assertRaises(PanelError):
            factors.compute_panel(self.hists, self.tr, self.price)
        body = json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(body), factors.SIZE_BUDGET)


class FactorsBuildTest(unittest.TestCase):
    def test_build_envelope(self):
        hists, tr, price = make_world(date(2005, 1, 4))
        table = dict(hists)
        table["H00985"] = tr
        table["000985"] = price

        def fake(code):
            return table[code]

        with mock.patch.object(factors, "get_index_history", side_effect=fake) as gh:
            res = factors.build()
        self.assertEqual(gh.call_count, 8)
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], END.isoformat())
        self.assertEqual(res["data"]["start"], "2005-01-04")
        joined = "\n".join(res["notes"])
        self.assertIn("H00922", joined)
        self.assertIn("相对强弱", joined)
        self.assertIn("3 秒", joined)
        for banned in ("不构成投资建议", "灵感来自", "个人学习用途", "自用"):
            self.assertNotIn(banned, joined)


if __name__ == "__main__":
    unittest.main()
