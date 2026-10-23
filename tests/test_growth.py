# -*- coding: utf-8 -*-
"""growth.compute_panel() / build() 的离线测试：合成 2006-01 → 2012-12 的八个来源（MIN_CITIES / GOLD_SPLIT 打补丁）。"""
import unittest
from datetime import date, timedelta
from unittest import mock

from etl import compute
from etl.common import PanelError
from etl.panels import growth

TODAY = "2013-01-15"


def _weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def _daily(start: date, end: date, v0: float, rate: float):
    dates = list(_weekdays(start, end))
    return {"dates": dates, "close": [round(v0 * (1 + rate) ** i, 4) for i in range(len(dates))]}


def make_world():
    months_m2 = compute.months_between("2008-01", "2012-12")
    m2 = {"months": months_m2, "m2": [400000.0 + 2000.0 * i for i in range(len(months_m2))]}

    q_months = [m for m in compute.months_between("2006-03", "2012-12") if m[5:7] in ("03", "06", "09", "12")]
    quarter_ends = [compute.month_end_date(m) for m in q_months]
    period = [40000.0 + 500.0 * i for i in range(len(q_months))]          # 当季值
    cum = []
    for m, p in zip(q_months, period):
        cum.append(p if m[5:7] == "03" else cum[-1] + p)
    gdp = {"quarterEnds": quarter_ends, "cum": cum, "period": period}

    a_dates = list(_weekdays(date(2005, 12, 1), date(2012, 12, 31)))
    mv = []
    for i, d in enumerate(a_dates):
        mv.append(None if "2011-01-04" <= d <= "2012-04-16" else 150000.0 + 10.0 * i)
    a_index = {"dates": a_dates, "mv": mv, "amount": [1000.0] * len(a_dates)}

    csi = _daily(date(2005, 12, 30), date(2012, 12, 31), 1000.0, 0.0004)
    dlv = _daily(date(2005, 12, 30), date(2012, 12, 31), 2000.0, 0.0003)

    h_months = compute.months_between("2011-01", "2012-12")
    cities = ["北京", "上海", "广州", "深圳", "成都", "武汉"]
    seq = {}
    for ci, c in enumerate(cities):
        seq[c] = [round(100.0 + 0.1 * ci + 0.05 * ((i % 3) - 1), 2) for i in range(len(h_months))]
    house = {"months": h_months, "cities": cities, "seq": seq}

    seed_months = compute.months_between("2006-01", "2009-11")
    seed = {"version": 1, "months": seed_months, "cnyPerGram": [140.0 + 2.0 * i for i in range(len(seed_months))],
            "k": 1.01, "overlapMonths": 30, "overlap": ["2009-12", "2012-05"]}
    sge = _daily(date(2009, 12, 1), date(2012, 12, 31), 240.0, 0.0005)
    return dict(m2=m2, gdp=gdp, a_index=a_index, csi=csi, dlv=dlv, house=house, seed=seed, sge=sge)


def run(w, min_cities=6, **over):
    args = dict(w)
    args.update(over)
    with mock.patch.object(growth, "MIN_CITIES", min_cities), mock.patch.object(growth, "GOLD_SPLIT", "2009-12"):
        return growth.compute_panel(args["m2"], args["gdp"], args["a_index"], args["csi"], args["dlv"], args["house"],
                                    args["seed"], args["sge"], today=TODAY)


class GrowthComputeTest(unittest.TestCase):
    def setUp(self):
        self.w = make_world()
        self.data = run(self.w)
        self.by = {s["key"]: s for s in self.data["series"]}
        self.months = self.data["months"]

    def _vals(self, key):
        return self.by[key]["values"]

    def test_axis_months(self):
        self.assertEqual(self.months[0], "2006-01")
        self.assertEqual(self.months[-1], "2012-12")                      # 2012-12-31 周一 = 当月最后工作日 → 完整月
        self.assertEqual(self.months, compute.months_between("2006-01", "2012-12"))
        self.assertEqual(len(self.months), 84)
        self.assertEqual(self.data["monthEnds"][-1], "2012-12-31")
        self.assertEqual(self.data["monthEnds"][1], "2006-02-28")
        self.assertEqual((self.data["start"], self.data["end"]), ("2006-01", "2012-12"))

    def test_series_order_and_lengths(self):
        self.assertEqual([s["key"] for s in self.data["series"]], [m[0] for m in growth.SERIES_META])
        self.assertEqual([s["key"] for s in self.data["summary"]], [m[0] for m in growth.SERIES_META])
        for s in self.data["series"]:
            self.assertEqual(len(s["values"]), len(self.months))
        self.assertEqual(self.by["gold"]["cadence"], "monthlyAvg")
        self.assertEqual(self.by["house70"]["kind"], "index")

    def test_gdp_quarter_points(self):
        vals = self._vals("gdp")
        for m, v in zip(self.months, vals):
            if m[5:7] in ("03", "06", "09", "12") and m >= "2006-12":
                self.assertIsNotNone(v, m)
            else:
                self.assertIsNone(v, m)
        i = self.months.index("2006-12")
        self.assertAlmostEqual(vals[i], sum(self.w["gdp"]["period"][:4]), places=6)
        j = self.months.index("2007-03")
        self.assertAlmostEqual(vals[j], sum(self.w["gdp"]["period"][1:5]), places=6)
        self.assertEqual(self.by["gdp"]["start"], "2006-12")

    def test_m2_start(self):
        vals = self._vals("m2")
        self.assertIsNone(vals[self.months.index("2007-12")])
        self.assertEqual(vals[self.months.index("2008-01")], 400000.0)
        self.assertEqual(self.by["m2"]["start"], "2008-01")

    def test_mv_gap(self):
        vals = self._vals("mv")
        gap = compute.months_between("2011-01", "2012-03")
        for m, v in zip(self.months, vals):
            if m in gap:
                self.assertIsNone(v, m)
            else:
                self.assertIsNotNone(v, m)
        self.assertEqual(self.by["mv"]["start"], "2006-01")

    def test_house70_first_value(self):
        i = self.months.index("2011-01")
        first = [self.w["house"]["seq"][c][0] for c in self.w["house"]["cities"]]
        self.assertAlmostEqual(self._vals("house70")[i], round(sum(first) / len(first), 2), places=2)
        self.assertIsNone(self._vals("house70")[i - 1])
        self.assertEqual(self.by["house70"]["start"], "2011-01")
        # 第二个月 = 链式：mean(seq0 × seq1 / 100)
        chained = [self.w["house"]["seq"][c][0] * self.w["house"]["seq"][c][1] / 100 for c in self.w["house"]["cities"]]
        self.assertAlmostEqual(self._vals("house70")[i + 1], round(sum(chained) / len(chained), 2), places=2)

    def test_house_tier1_uses_four_cities(self):
        i = self.months.index("2011-01")
        first = [self.w["house"]["seq"][c][0] for c in growth.TIER1]
        self.assertAlmostEqual(self._vals("houseTier1")[i], round(sum(first) / 4, 2), places=2)
        self.assertNotEqual(self._vals("houseTier1")[i], self._vals("house70")[i])

    def test_house_city_with_null_dropped(self):
        w = make_world()
        w["house"]["seq"]["武汉"][5] = None
        data = run(w, min_cities=5)
        self.assertEqual(data["houseDropped"], ["武汉"])
        i = data["months"].index("2011-01")
        first = [w["house"]["seq"][c][0] for c in w["house"]["cities"] if c != "武汉"]
        self.assertAlmostEqual({s["key"]: s for s in data["series"]}["house70"]["values"][i], round(sum(first) / 5, 2), places=2)

    def test_house_too_few_cities_rejected(self):
        w = make_world()
        w["house"]["seq"]["武汉"][5] = None
        with self.assertRaises(PanelError):
            run(w)                                                        # MIN_CITIES = 6，只剩 5 个完整城市

    def test_house_missing_tier1_rejected(self):
        w = make_world()
        w["house"]["seq"]["北京"][0] = None
        with self.assertRaises(PanelError):
            run(w, min_cities=5)

    def test_gold_splice(self):
        vals = self._vals("gold")
        i0 = self.months.index("2006-01")
        i_split = self.months.index("2009-12")
        self.assertEqual(vals[i0], 140.0)
        self.assertEqual(vals[i_split - 1], round(self.w["seed"]["cnyPerGram"][-1], 2))
        sge_m, sge_avg, _ = growth.sge_gold.monthly_averages(self.w["sge"], complete_only=True, today=TODAY)
        self.assertEqual(sge_m[0], "2009-12")
        self.assertEqual(vals[i_split], round(sge_avg[0], 2))
        self.assertEqual(vals[-1], round(sge_avg[-1], 2))
        self.assertTrue(all(v is not None for v in vals), "黄金两段拼接后不应有缺月")
        self.assertEqual(self.by["gold"]["end"], "2012-12")

    def test_gold_seed_end_mismatch_rejected(self):
        w = make_world()
        w["seed"]["months"] = w["seed"]["months"][:-1]
        w["seed"]["cnyPerGram"] = w["seed"]["cnyPerGram"][:-1]
        with self.assertRaises(PanelError):
            run(w)

    def test_summary_multiple_and_cagr(self):
        for s in self.data["summary"]:
            self.assertAlmostEqual(s["multiple"], round(s["last"] / s["first"], 4), places=4, msg=s["key"])
            self.assertEqual(s["start"], self.by[s["key"]]["start"])
            self.assertEqual(s["end"], self.by[s["key"]]["end"])
            self.assertIsNotNone(s["cagrAll"], s["key"])
        s = {x["key"]: x for x in self.data["summary"]}
        self.assertIsNone(s["house70"]["cagr10y"])                         # 2 年序列没有 10 年年化
        self.assertIsNone(s["m2"]["cagr10y"])                              # 5 年
        self.assertAlmostEqual(s["mv"]["years"], compute.years_between("2006-01-31", "2012-12-31"), places=2)
        self.assertEqual(s["m2"]["note"], "")
        self.assertIn("季度点", s["gdp"]["note"])

    def test_base_month(self):
        # 八条序列同时有值的第一个月：房价 2011-01 起，流通市值 2011-01 → 2012-03 为空，GDP 只在季末月有点 → 2012-06
        self.assertEqual(self.data["baseMonth"], "2012-06")
        i = self.months.index("2012-06")
        self.assertTrue(all(s["values"][i] is not None for s in self.data["series"]))
        for j in range(i):
            self.assertTrue(any(s["values"][j] is None for s in self.data["series"]), self.months[j])

    def test_dropped_list(self):
        self.assertEqual([d["key"] for d in self.data["dropped"]], ["deposits", "income"])
        self.assertEqual(self.data["houseDropped"], [])

    def test_short_series_rejected(self):
        w = make_world()
        w["m2"] = {"months": compute.months_between("2011-02", "2012-12"), "m2": [1.0] * 23}
        with self.assertRaises(PanelError):
            run(w)

    def test_non_positive_rejected(self):
        w = make_world()
        w["m2"]["m2"][3] = 0.0
        with self.assertRaises(PanelError):
            run(w)

    def test_incomplete_last_month_dropped(self):
        w = make_world()
        w["csi"] = _daily(date(2005, 12, 30), date(2012, 12, 28), 1000.0, 0.0004)   # 12-28 周五，之后 12-31 周一还有工作日
        with mock.patch.object(growth, "MIN_POINTS", 20):                           # 房价只剩 23 个月
            data = run(w)
        self.assertEqual(data["end"], "2012-11")
        self.assertEqual(data["months"][-1], "2012-11")


class GrowthBuildTest(unittest.TestCase):
    def test_build_envelope(self):
        w = make_world()

        def fake_hist(code):
            return w["csi"] if code == growth.CSI_ALL_TR else w["dlv"]

        with mock.patch.object(growth.eastmoney_macro, "get_m2", return_value=w["m2"]), \
                mock.patch.object(growth.eastmoney_macro, "get_gdp", return_value=w["gdp"]), \
                mock.patch.object(growth.eastmoney_macro, "get_house_prices", return_value=w["house"]), \
                mock.patch.object(growth.sws, "get_a_index", return_value=w["a_index"]), \
                mock.patch.object(growth.csindex_hist, "get_index_history", side_effect=fake_hist) as gh, \
                mock.patch.object(growth.gold_history, "load_seed", return_value=w["seed"]), \
                mock.patch.object(growth.sge_gold, "get_au9999", return_value=w["sge"]), \
                mock.patch.object(growth, "MIN_CITIES", 6), mock.patch.object(growth, "GOLD_SPLIT", "2009-12"):
            res = growth.build()
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], compute.month_end_date(res["data"]["end"]))
        self.assertEqual(res["asOf"], "2012-12-31")
        self.assertEqual(gh.call_count, 2)
        self.assertEqual(len(res["data"]["series"]), 8)
        joined = "\n".join(res["notes"])
        self.assertIn("东方财富", joined)
        self.assertIn("存款", joined)
        self.assertIn("k = 1.01", joined)
        self.assertEqual(len(res["sources"]), 5)
        for banned in ("不构成投资建议", "灵感来自", "个人学习用途", "自用"):
            self.assertNotIn(banned, joined)


if __name__ == "__main__":
    unittest.main()
