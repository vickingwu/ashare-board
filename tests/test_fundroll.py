# -*- coding: utf-8 -*-
"""fundroll.compute_panel() / build() 的离线测试：合成 2,000 日、日涨 0.05 % 的主序列（MIN_ROWS / SIZE_BUDGET 打补丁）。"""
import json
import unittest
from datetime import date, timedelta
from unittest import mock

from etl.common import PanelError
from etl.panels import fundroll

N = 2000
DAILY = 0.0005


def _weekdays(start: date, n: int):
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_world():
    dates = _weekdays(date(2007, 12, 28), N)
    main = {"dates": dates, "close": [1000.0 * (1 + DAILY) ** i for i in range(N)]}
    hists = {}
    for _k, _n, code, _kind, _x in fundroll.SERIES:
        hists[code] = {"dates": list(dates), "close": [500.0 + i for i in range(N)]}
    hists[fundroll.MAIN] = main
    # 930897 缺两个交易日
    h = hists["930897"]
    hists["930897"] = {"dates": h["dates"][:10] + h["dates"][12:], "close": h["close"][:10] + h["close"][12:]}
    return hists


class FundrollComputeTest(unittest.TestCase):
    def setUp(self):
        self.hists = make_world()
        with mock.patch.object(fundroll, "MIN_ROWS", 1000):
            self.data = fundroll.compute_panel(self.hists)

    def test_axis_and_windows(self):
        self.assertEqual(self.data["dates"][0], "2007-12-28")
        self.assertEqual(len(self.data["dates"]), N)
        self.assertEqual(self.data["windows"], {"1": 252, "3": 756, "5": 1260, "7": 1764})
        self.assertEqual(set(self.data["rolling"]), {"1", "3", "5", "7"})
        for arr in self.data["rolling"].values():
            self.assertEqual(len(arr), N)
        self.assertEqual(self.data["startOptions"], ["2008-01-01", "2014-01-01", "2019-01-01", "2024-01-01"])

    def test_rolling_one_year(self):
        r1 = self.data["rolling"]["1"]
        expected = ((1 + DAILY) ** 252 - 1) * 100
        self.assertTrue(all(v is None for v in r1[:252]))
        self.assertAlmostEqual(r1[252], round(expected, 2), places=6)
        self.assertAlmostEqual(r1[-1], round(expected, 2), places=6)
        r3 = self.data["rolling"]["3"]
        self.assertTrue(all(v is None for v in r3[:756]))
        self.assertAlmostEqual(r3[756], round(((1 + DAILY) ** 756) ** (252 / 756) * 100 - 100, 2), places=6)

    def test_latest_and_stats(self):
        lt = self.data["latest"]
        self.assertEqual(lt["date"], self.data["dates"][-1])
        self.assertAlmostEqual(lt["close"], round(1000.0 * (1 + DAILY) ** (N - 1), 2), places=2)
        self.assertEqual(lt["r1"], self.data["rolling"]["1"][-1])
        self.assertEqual(lt["r3"], self.data["rolling"]["3"][-1])
        self.assertIsNotNone(lt["r5"])
        self.assertIsNotNone(lt["r7"])                                    # 2,000 日 > 1,764
        st = self.data["stats"]
        self.assertEqual(st["1"]["pctPositive"], 100.0)
        self.assertEqual(st["1"]["n"], N - 252)
        self.assertEqual(st["1"]["latest"], lt["r1"])
        self.assertAlmostEqual(st["1"]["min"], st["1"]["max"], places=2)
        self.assertEqual(st["7"]["n"], N - 1764)

    def test_series_alignment_and_nulls(self):
        by = {s["key"]: s for s in self.data["series"]}
        self.assertEqual(list(by), [s[0] for s in fundroll.SERIES])
        self.assertEqual(by["equityFund"]["values"][0], 1000.0)
        self.assertEqual(by["equityFund"]["kind"], "nav")
        self.assertFalse(by["equityFund"]["extra"])
        self.assertTrue(by["stockFund"]["extra"])
        self.assertEqual(by["csi300TR"]["kind"], "tr")
        self.assertEqual(by["a500"]["kind"], "price")
        bp = by["bondPlusFund"]["values"]
        self.assertIsNone(bp[10])
        self.assertIsNone(bp[11])
        self.assertEqual(bp[9], 509.0)
        self.assertEqual(bp[12], 512.0)
        for s in self.data["series"]:
            self.assertEqual(len(s["values"]), N)

    def test_r7_none_on_short_series(self):
        hists = make_world()
        n = 1700                                                           # < 1,764：7 年窗口永远不满
        dates = _weekdays(date(2007, 12, 28), n)
        hists[fundroll.MAIN] = {"dates": dates, "close": [1000.0 * (1 + DAILY) ** i for i in range(n)]}
        with mock.patch.object(fundroll, "MIN_ROWS", 1000):
            data = fundroll.compute_panel(hists)
        self.assertIsNone(data["latest"]["r7"])
        self.assertIsNotNone(data["latest"]["r5"])
        self.assertEqual(data["stats"]["7"], {"min": None, "max": None, "latest": None, "pctPositive": None, "n": 0})
        self.assertAlmostEqual(data["latest"]["r5"], round(((1 + DAILY) ** 252 - 1) * 100, 2), places=6)

    def test_short_main_rejected(self):
        with self.assertRaises(PanelError):                               # MIN_ROWS = 4000 > 2000
            fundroll.compute_panel(self.hists)

    def test_missing_series_rejected(self):
        hists = dict(self.hists)
        del hists["H11023"]
        with mock.patch.object(fundroll, "MIN_ROWS", 1000), self.assertRaises(PanelError):
            fundroll.compute_panel(hists)

    def test_non_positive_rejected(self):
        hists = make_world()
        hists[fundroll.MAIN]["close"][5] = -1.0
        with mock.patch.object(fundroll, "MIN_ROWS", 1000), self.assertRaises(PanelError):
            fundroll.compute_panel(hists)

    def test_size_budget(self):
        with mock.patch.object(fundroll, "MIN_ROWS", 1000), mock.patch.object(fundroll, "SIZE_BUDGET", 1000), \
                self.assertRaises(PanelError):
            fundroll.compute_panel(self.hists)
        body = json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(body), fundroll.SIZE_BUDGET)


class FundrollBuildTest(unittest.TestCase):
    def test_build_envelope(self):
        hists = make_world()
        with mock.patch.object(fundroll, "get_index_history", side_effect=lambda code: hists[code]) as gh, \
                mock.patch.object(fundroll, "MIN_ROWS", 1000):
            res = fundroll.build()
        self.assertEqual(gh.call_count, 7)
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], hists[fundroll.MAIN]["dates"][-1])
        joined = "\n".join(res["notes"])
        self.assertIn("930897", joined)
        self.assertIn("252", joined)
        # 禁用的填充语（拼接写出，仓库全文检索这些词本身应为 0 命中）
        for banned in ("不构成" + "投资建议", "灵" + "感来自", "个人" + "学习用途", "自" + "用"):
            self.assertNotIn(banned, joined)


if __name__ == "__main__":
    unittest.main()
