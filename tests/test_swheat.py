# -*- coding: utf-8 -*-
"""swheat.compute_panel() 的离线测试：三组起点不同的合成序列（年末 1000 × 1.1^k）。"""
import unittest
from unittest import mock

from etl.common import PanelError
from etl.panels import swheat

AS_OF = "2026-10-16"
GROUP_1999 = sorted(swheat.BASE_1999_CODES)                       # 16 个，基日 1999-12-30
GROUP_2014 = ["801710", "801720", "801730", "801740", "801750", "801760",
              "801770", "801780", "801790", "801880", "801890"]             # 11 个，2014-02-21 起
GROUP_2021 = ["801960", "801970", "801980", "801950"]             # 4 个，2021-12-13 起（煤炭见 SERIES_START）


def _year_end_series(first_full_year, prefix_rows=()):
    """年末 12-31（1999 年用 12-30）收盘 = 1000 × 1.1^k，k 从 first_full_year − 1 年末的 0 起；外加 2026-10-16 一行。"""
    dates, close = [], []
    for d, v in prefix_rows:
        dates.append(d)
        close.append(v)
    k = 0
    for y in range(first_full_year - 1, 2026):
        dates.append(f"{y}-12-30" if y == 1999 else f"{y}-12-31")
        close.append(1000.0 * 1.1 ** k)
        k += 1
    dates.append(AS_OF)
    close.append(1000.0 * 1.1 ** (k - 1) * 0.9)                    # 年初至今 −10 %
    return dates, close


def _histories():
    out = []
    for c in GROUP_1999:
        d, v = _year_end_series(2000)
        out.append({"code": c, "name": "甲" + c, "dates": d, "close": v})
    for c in GROUP_2014:
        d, v = _year_end_series(2015, prefix_rows=[("2014-02-21", 950.0)])
        out.append({"code": c, "name": "乙" + c, "dates": d, "close": v})
    for c in GROUP_2021:
        d, v = _year_end_series(2022, prefix_rows=[("2021-12-13", 980.0)])
        out.append({"code": c, "name": "丙" + c, "dates": d, "close": v})
    return out


class SwheatComputeTest(unittest.TestCase):
    def setUp(self):
        self.data = swheat.compute_panel(_histories(), AS_OF)
        self.by = {i["code"]: i for i in self.data["industries"]}

    def test_axis(self):
        d = self.data
        self.assertEqual(d["years"][0], 2000)
        self.assertEqual(d["years"][-1], 2025)
        self.assertEqual(len(d["years"]), 26)
        self.assertEqual(d["ytdYear"], 2026)
        self.assertEqual(d["ytdAsOf"], AS_OF)
        self.assertEqual(d["baseDate"], "1999-12-30")
        self.assertEqual(len(d["industries"]), 31)
        self.assertEqual(d["nByYear"][0], 16)
        self.assertEqual(d["nByYear"][-1], 31)
        self.assertEqual(d["nByYear"][d["years"].index(2015)], 27)
        self.assertEqual(d["nByYear"][d["years"].index(2021)], 27)
        self.assertEqual(d["nByYear"][d["years"].index(2022)], 31)
        self.assertEqual(d["nYtd"], 31)

    def test_first_year_and_null_prefix(self):
        self.assertEqual(self.by["801010"]["firstYear"], 2000)
        self.assertEqual(self.by["801710"]["firstYear"], 2015)
        self.assertEqual(self.by["801950"]["firstYear"], 2022)
        self.assertEqual(self.by["801960"]["firstYear"], 2022)
        years = self.data["years"]
        r = self.by["801710"]["rets"]
        self.assertTrue(all(x is None for x in r[:years.index(2015)]))
        self.assertTrue(all(x == 10.0 for x in r[years.index(2015):]))
        self.assertEqual(self.by["801960"]["rets"][years.index(2021)], None)
        self.assertTrue(all(x == 10.0 for x in self.by["801010"]["rets"]))
        self.assertEqual(len(r), len(years))

    def test_cagr_and_windows(self):
        a, b, c = self.by["801010"], self.by["801710"], self.by["801980"]
        self.assertEqual(a["cagr"]["all"], 10.0)
        self.assertEqual(a["cagrYears"]["all"], 26)
        self.assertEqual(b["cagrYears"]["all"], 11)
        self.assertEqual(c["cagrYears"]["all"], 4)
        self.assertEqual(b["cagr"]["all"], 10.0)
        self.assertEqual(c["cagr"]["all"], 10.0)
        self.assertEqual(a["cagr"]["y10"], 10.0)
        self.assertEqual(b["cagr"]["y10"], 10.0)
        self.assertIsNone(c["cagr"]["y10"])
        self.assertIsNone(c["cagr"]["y5"])
        self.assertEqual(c["cagr"]["y3"], 10.0)

    def test_ytd_and_ranks(self):
        for ind in self.data["industries"]:
            self.assertAlmostEqual(ind["ytd"], -10.0, places=6)
            self.assertEqual(ind["ytdRank"], 1)                      # 全部并列
            self.assertEqual(len(ind["ranks"]), len(self.data["years"]))
            for r, rk in zip(ind["rets"], ind["ranks"]):
                self.assertEqual(rk, None if r is None else 1)

    def test_rets_rounded(self):
        for ind in self.data["industries"]:
            for r in ind["rets"]:
                self.assertTrue(r is None or round(r, 2) == r)

    def test_extreme_return_rejected(self):
        hs = _histories()
        hs[0]["close"][5] = hs[0]["close"][4] * 5.0                  # 某年 +400 %
        with self.assertRaises(PanelError):
            swheat.compute_panel(hs, AS_OF)

    def test_no_gap_years_by_default(self):
        for ind in self.data["industries"]:
            self.assertEqual(ind["gapYears"], [])
            self.assertEqual(ind["cagrYears"]["from"], ind["firstYear"])

    def test_series_start_drops_orphaned_segment(self):
        """801950 真实形状：2014-02-21 起一段别的指数、2017-01-20 后断档、2021-12-13 重新开始 → 起点之前全部不计。"""
        hs = _histories()
        coal = next(h for h in hs if h["code"] == "801950")
        d, v = _year_end_series(2022, prefix_rows=[("2014-02-21", 997.49), ("2014-12-31", 1287.49),
                                                   ("2015-12-31", 1986.21), ("2016-12-31", 1450.14),
                                                   ("2017-01-20", 1511.21), ("2021-12-13", 2427.14)])
        coal["dates"], coal["close"] = d, v
        data = swheat.compute_panel(hs, AS_OF)
        years = data["years"]
        ind = next(i for i in data["industries"] if i["code"] == "801950")
        self.assertEqual(ind["firstYear"], 2022)
        self.assertEqual(ind["gapYears"], [])
        self.assertTrue(all(ind["rets"][years.index(y)] is None for y in range(2000, 2022)))
        self.assertTrue(all(ind["ranks"][years.index(y)] is None for y in range(2000, 2022)))
        self.assertEqual(ind["rets"][years.index(2022)], 10.0)
        self.assertEqual(ind["cagrYears"], {"all": 4, "from": 2022})
        self.assertIsNone(ind["cagr"]["y10"])
        self.assertIsNone(ind["cagr"]["y5"])
        self.assertEqual(ind["cagr"]["y3"], 10.0)
        self.assertEqual(data["nByYear"][years.index(2015)], 27)
        self.assertEqual(data["nByYear"][years.index(2016)], 27)

    def test_orphaned_segment_without_series_start_is_kept_as_gap(self):
        """同样形状但代码不在 SERIES_START 里：按普通断档处理（2015–2016 保留、2017–2021 为 gapYears）。"""
        hs = _histories()
        other = next(h for h in hs if h["code"] == "801710")
        d, v = _year_end_series(2022, prefix_rows=[("2014-02-21", 950.0), ("2014-12-31", 1000.0),
                                                   ("2015-12-31", 1100.0), ("2016-12-31", 1210.0),
                                                   ("2017-01-20", 1200.0), ("2021-12-13", 990.0)])
        other["dates"], other["close"] = d, v
        data = swheat.compute_panel(hs, AS_OF)
        years = data["years"]
        ind = next(i for i in data["industries"] if i["code"] == "801710")
        self.assertEqual(ind["firstYear"], 2015)
        self.assertEqual(ind["gapYears"], [2017, 2018, 2019, 2020, 2021])
        self.assertEqual(ind["rets"][years.index(2015)], 10.0)
        self.assertEqual(ind["cagrYears"], {"all": 4, "from": 2022})

    def test_extreme_ytd_rejected(self):
        hs = _histories()
        hs[0]["close"][-1] = hs[0]["close"][-2] * 5.0                 # 年初至今 +400 %
        with self.assertRaises(PanelError):
            swheat.compute_panel(hs, AS_OF)

    def test_middle_gap_recorded_and_cagr_restarts(self):
        hs = _histories()
        i = hs[0]["dates"].index("2010-12-31")
        del hs[0]["dates"][i]
        del hs[0]["close"][i]
        data = swheat.compute_panel(hs, AS_OF)
        ind = data["industries"][0]
        years = data["years"]
        self.assertEqual(ind["firstYear"], 2000)
        self.assertEqual(ind["gapYears"], [2010, 2011])             # 2010 缺年末、2011 缺基准
        self.assertIsNone(ind["rets"][years.index(2010)])
        self.assertIsNone(ind["rets"][years.index(2011)])
        self.assertEqual(ind["rets"][years.index(2009)], 10.0)
        self.assertEqual(ind["rets"][years.index(2012)], 10.0)
        self.assertEqual(ind["cagrYears"], {"all": 14, "from": 2012})
        self.assertEqual(ind["cagr"]["all"], 10.0)
        self.assertEqual(ind["cagr"]["y10"], 10.0)
        self.assertEqual(data["nByYear"][years.index(2010)], 15)
        self.assertIsNone(ind["ranks"][years.index(2010)])

    def test_gap_inside_window_blanks_that_window(self):
        hs = _histories()
        i = hs[0]["dates"].index("2020-12-31")
        del hs[0]["dates"][i]
        del hs[0]["close"][i]
        ind = swheat.compute_panel(hs, AS_OF)["industries"][0]
        self.assertEqual(ind["gapYears"], [2020, 2021])
        self.assertEqual(ind["cagrYears"], {"all": 4, "from": 2022})
        self.assertIsNone(ind["cagr"]["y10"])
        self.assertIsNone(ind["cagr"]["y5"])
        self.assertEqual(ind["cagr"]["y3"], 10.0)
        self.assertEqual(ind["cagr"]["all"], 10.0)

    def test_gap_reaching_last_year_gives_no_cagr(self):
        hs = _histories()
        i = hs[0]["dates"].index("2025-12-31")
        del hs[0]["dates"][i]
        del hs[0]["close"][i]
        ind = swheat.compute_panel(hs, AS_OF)["industries"][0]
        self.assertEqual(ind["gapYears"], [2025])
        self.assertEqual(ind["cagrYears"], {"all": 0, "from": None})
        self.assertTrue(all(v is None for v in ind["cagr"].values()))
        self.assertIsNone(ind["ytd"])                               # 上年末缺失，年初至今也算不出

    def test_whole_year_missing_for_everyone_rejected(self):
        hs = _histories()
        for h in hs:
            if "2010-12-31" in h["dates"]:
                i = h["dates"].index("2010-12-31")
                del h["dates"][i]
                del h["close"][i]
        with self.assertRaises(PanelError):
            swheat.compute_panel(hs, AS_OF)

    def test_wrong_count_rejected(self):
        with self.assertRaises(PanelError):
            swheat.compute_panel(_histories()[:30], AS_OF)

    def test_base_1999_code_starting_late_rejected(self):
        hs = _histories()
        bad = next(h for h in hs if h["code"] == "801010")
        d, v = _year_end_series(2015, prefix_rows=[("2014-02-21", 950.0)])
        bad["dates"], bad["close"] = d, v
        with self.assertRaises(PanelError):
            swheat.compute_panel(hs, AS_OF)

    def test_none_in_close_is_skipped(self):
        hs = _histories()
        hs[3]["dates"].insert(1, "2000-06-30")
        hs[3]["close"].insert(1, None)
        data = swheat.compute_panel(hs, AS_OF)
        self.assertEqual(len(data["industries"]), 31)


class SwheatBuildTest(unittest.TestCase):
    def test_build_uses_list_names_and_envelope(self):
        hs = _histories()
        lv = {"dates": hs[0]["dates"], "codes": {}}
        for h in hs:
            lv["codes"][h["code"]] = {"name": "x", "close": h["close"] if h["dates"] == lv["dates"]
                                      else [dict(zip(h["dates"], h["close"])).get(d) for d in lv["dates"]]}
        names = [(h["code"], h["name"]) for h in hs]
        with mock.patch.object(swheat.sws, "get_level_history", return_value=lv), \
                mock.patch.object(swheat.sws, "get_index_list", return_value=names):
            res = swheat.build()
        self.assertEqual(res["asOf"], AS_OF)
        self.assertEqual(res["data"]["industries"][0]["name"], "甲801010")
        self.assertTrue(res["sources"] and res["notes"])
        self.assertTrue(any("2015 年起" in n for n in res["notes"]))

    def test_build_missing_code_rejected(self):
        hs = _histories()
        lv = {"dates": hs[0]["dates"], "codes": {h["code"]: {"name": h["name"], "close": h["close"]} for h in hs[:30]}}
        names = [(h["code"], h["name"]) for h in hs]
        with mock.patch.object(swheat.sws, "get_level_history", return_value=lv), \
                mock.patch.object(swheat.sws, "get_index_list", return_value=names):
            with self.assertRaises(PanelError):
                swheat.build()


if __name__ == "__main__":
    unittest.main()
