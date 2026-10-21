# -*- coding: utf-8 -*-
"""trend.compute_panel() / build() 的离线测试：合成聚合（n250 0 → 1500 → 6660，站上率恒 40 %）+ 合成申万一级
（16 / 11 / 4 个行业三种起点，几何增长 → MACD 恒正）+ 带缺日的中证全指。"""
import os
import unittest
from datetime import date, timedelta
from typing import Dict, List
from unittest import mock

from etl import compute
from etl.common import PanelError
from etl.panels import trend
from etl.sources import sws

GROUP_1999 = ["801010", "801030", "801040", "801050", "801080", "801110", "801120", "801130",
              "801140", "801150", "801160", "801170", "801180", "801200", "801210", "801230"]
GROUP_2014 = ["801710", "801720", "801730", "801740", "801750", "801760", "801770", "801780", "801790", "801880", "801890"]
GROUP_2021 = ["801960", "801970", "801980"]
COAL = "801950"                       # 原始行从 2014-02-21 起，经 SERIES_START 截到 2021-12-13
START_2014, START_2021 = "2014-02-21", "2021-12-13"
L1_END = "2026-10-16"
N_AGG = 600
JUMP = 255                            # n250 在这个下标从 0 跳到 1500


def _weekdays_from(start: date, n: int) -> List[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _weekdays_between(start: date, end: date) -> List[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _agg(n=N_AGG, start=date(2004, 1, 2)):
    dates = _weekdays_from(start, n)
    n250, above250, n50, above50 = [], [], [], []
    for i in range(n):
        if i < JUMP - 5:
            a = 0
        elif i < JUMP:
            a = 50 * (i - (JUMP - 5) + 1)                     # 50..250：都 < 300，不发布
        else:
            a = 1500 + (i - JUMP) * 15                        # 1500 → 6660，0.4 × a 恒为整数
        n250.append(a)
        above250.append(a * 2 // 5)
        b = 0 if i < 55 else 50 + (i - 55) * 10               # 0.3 × b 恒为整数
        n50.append(b)
        above50.append(b * 3 // 10)
    agg = {"dates": dates, "nTraded": [b + 10 for b in n50], "n250": n250, "above250": above250, "n50": n50,
           "above50": above50}
    for k in ("adv", "dec", "flat", "limitUp", "limitDown", "nLimitEligible"):
        agg[k] = [0] * n
    return agg


def _l1(end=L1_END):
    dates = _weekdays_between(date(1999, 12, 30), date.fromisoformat(end))
    codes: Dict[str, dict] = {}

    def series(start_date):
        out, k = [], 0
        for d in dates:
            if d < start_date:
                out.append(None)
            else:
                out.append(1000.0 * 1.001 ** k)
                k += 1
        return out

    for c in GROUP_1999:
        codes[c] = {"name": "甲" + c, "close": series("1999-12-30")}
    for c in GROUP_2014 + [COAL]:
        codes[c] = {"name": "乙" + c, "close": series(START_2014)}
    for c in GROUP_2021:
        codes[c] = {"name": "丙" + c, "close": series(START_2021)}
    return {"level": sws.LEVEL1, "code": "all", "fetchedAt": "x", "dates": dates, "codes": codes}


def _names():
    return {c: "行业" + c for c in GROUP_1999 + GROUP_2014 + GROUP_2021 + [COAL]}


def _csi(agg):
    dates = [d for i, d in enumerate(agg["dates"]) if i % 97 != 50]          # 每 97 天缺 1 天
    return {"code": "000985", "name": "中证全指", "dates": dates, "close": [3000.0 + i * 0.123 for i in range(len(dates))],
            "pe": [None] * len(dates)}


STATUS = {"nStocks": 5540, "nCodes": 5563, "lastDate": None, "aggLastDate": None, "nListedNow": 5224}


def _switch_month(start_date, l1):
    """某组行业第 35 个观测（MACD 预热后首个有值日）所在的月份。"""
    own = [d for d in l1["dates"] if d >= start_date]
    return compute.month_key(own[compute.MACD_WARMUP])


class TrendComputeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agg = _agg()
        cls.l1 = _l1()
        cls.names = _names()
        cls.csi = _csi(cls.agg)
        cls.data = trend.compute_panel(cls.agg, cls.l1, cls.names, cls.csi, STATUS)

    def test_daily_starts_at_first_n250_over_threshold(self):
        d = self.data["daily"]
        self.assertEqual(d["dates"][0], self.agg["dates"][JUMP])
        self.assertTrue(self.agg["dates"][JUMP] <= trend.FIRST_DAILY_BY)
        self.assertEqual(len(d["dates"]), N_AGG - JUMP)
        self.assertEqual(d["n"], self.agg["n250"][JUMP:])
        self.assertEqual(d["above250"], self.agg["above250"][JUMP:])
        self.assertTrue(all(v == 40.0 for v in d["pct250"]))
        self.assertTrue(all(v == 30.0 for v in d["pct50"]))
        self.assertEqual(d["n"][0], 1500)
        self.assertEqual(d["n"][-1], 1500 + (N_AGG - 1 - JUMP) * 15)
        self.assertEqual(set(d), {"dates", "pct250", "pct50", "n", "above250"})

    def test_days_below_threshold_after_start_kept_as_null(self):
        agg = _agg()
        agg["n250"][400], agg["above250"][400] = 200, 80
        agg["n50"][450], agg["above50"][450] = 100, 30
        data = trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS)
        d = data["daily"]
        self.assertEqual(d["dates"][400 - JUMP], agg["dates"][400])
        self.assertIsNone(d["pct250"][400 - JUMP])
        self.assertEqual(d["n"][400 - JUMP], 200)
        self.assertEqual(d["pct50"][400 - JUMP], 30.0)
        self.assertIsNone(d["pct50"][450 - JUMP])
        self.assertEqual(d["pct250"][450 - JUMP], 40.0)
        self.assertEqual(sum(1 for v in d["pct250"] if v is None), 1)
        self.assertEqual(sum(1 for v in d["pct50"] if v is None), 1)

    def test_overlay_aligned_to_daily_dates(self):
        ov = self.data["overlay"]
        daily = self.data["daily"]["dates"]
        self.assertEqual(ov["dates"], daily)
        self.assertEqual(len(ov["csiAll"]), len(daily))
        by = dict(zip(self.csi["dates"], self.csi["close"]))
        for d, v in zip(daily, ov["csiAll"]):
            if d in by:
                self.assertEqual(v, round(by[d], 2))
            else:
                self.assertIsNone(v)
        self.assertTrue(any(v is None for v in ov["csiAll"]))
        self.assertGreater(sum(1 for v in ov["csiAll"] if v is not None), len(daily) * 0.95)

    def test_monthly_series(self):
        m = self.data["monthly"]
        months = [compute.month_key(d) for d in m["dates"]]
        self.assertEqual(len(set(months)), len(months))
        self.assertEqual(months, sorted(months))
        self.assertEqual(months[0], "2000-02")                          # 1999-12-30 + 34 个工作日 = 2000-02-17
        self.assertTrue(months[0] <= trend.FIRST_MONTH_BY)
        self.assertTrue(all(v == 100.0 for v in m["pctIndustryMacdPos"]))
        self.assertTrue(all(v == 100.0 for v in m["pctHist"]))
        sw14, sw21 = _switch_month(START_2014, self.l1), _switch_month(START_2021, self.l1)
        self.assertEqual(sw14, "2014-04")
        self.assertEqual(sw21, "2022-01")
        for mo, n in zip(months, m["nIndustries"]):
            expected = 16 if mo < sw14 else (27 if mo < sw21 else 31)
            self.assertEqual(n, expected, mo)
        self.assertEqual(m["nIndustries"][-1], 31)
        self.assertEqual(m["dates"][-1], L1_END)
        self.assertTrue(all(compute.is_last_weekday_of_month(d) for d in m["dates"][:-1]))

    def test_partial_last_flag(self):
        self.assertTrue(self.data["monthly"]["partialLast"])            # 2026-10-16 不是 10 月最后一个工作日
        l1 = _l1(end="2026-09-30")
        data = trend.compute_panel(self.agg, l1, self.names, self.csi, STATUS)
        self.assertFalse(data["monthly"]["partialLast"])
        self.assertEqual(data["monthly"]["dates"][-1], "2026-09-30")

    def test_industry_daily_and_latest(self):
        ind = self.data["industryDaily"]
        self.assertEqual(len(ind["dates"]), trend.INDUSTRY_DAILY_DAYS)
        self.assertEqual(len(ind["pct"]), len(ind["dates"]))
        self.assertEqual(len(ind["pctHist"]), len(ind["dates"]))
        self.assertEqual(ind["dates"][-1], self.data["monthly"]["dates"][-1])
        lt = self.data["latest"]
        d = self.data["daily"]
        self.assertEqual(lt, {
            "date": d["dates"][-1], "pct250": 40.0, "pct50": 30.0, "n": d["n"][-1], "above250": d["above250"][-1],
            "industryPct": 100.0, "industryPctHist": 100.0, "industryN": 31, "industryDate": L1_END,
        })
        u = self.data["universe"]
        self.assertEqual(u["source"], "Baostock 沪深A股")
        self.assertEqual(u["start"], "2004-01-01")
        self.assertEqual((u["nStocksNow"], u["nStocksEver"]), (5224, 5563))
        self.assertEqual(u["storeLastDate"], STATUS["aggLastDate"])

    def test_series_start_cuts_coal(self):
        m = self.data["monthly"]
        months = [compute.month_key(d) for d in m["dates"]]
        self.assertEqual(m["nIndustries"][months.index("2015-06")], 27)        # 801950 的 2014 段不算
        self.assertEqual(m["nIndustries"][months.index("2021-12")], 27)
        with mock.patch.dict(sws.SERIES_START, {}, clear=True):
            data = trend.compute_panel(self.agg, self.l1, self.names, self.csi, STATUS)
        n_no_cut = data["monthly"]["nIndustries"]
        self.assertEqual(n_no_cut[months.index("2015-06")], 28)

    def test_corrupt_pct_rejected(self):
        agg = _agg()
        agg["above250"][300] = agg["n250"][300] + 5
        with self.assertRaises(PanelError):
            trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS)

    def test_latest_n_below_threshold_rejected_unless_partial(self):
        agg = _agg()
        agg["n250"][-1], agg["above250"][-1] = 4000, 1600
        with self.assertRaises(PanelError):
            trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS)
        data = trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS, allow_partial=True)
        self.assertEqual(data["latest"]["n"], 4000)

    def test_first_days_may_be_small_but_must_reach_1000_by_spring_2005(self):
        """真实库的头几天分母只有几百只（2004 年有停牌日的股票晚几天满 250 行），之后才到 ~1,300。"""
        agg = _agg()
        for i, v in enumerate((400, 900, 1150)):                               # 首日 400 → 第三日 1150，再往后 ≥ 1500
            agg["n250"][JUMP + i], agg["above250"][JUMP + i] = v, v * 2 // 5
        data = trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS)
        self.assertEqual(data["daily"]["n"][:3], [400, 900, 1150])
        self.assertEqual(data["daily"]["pct250"][:3], [40.0, 40.0, 40.0])
        agg = _agg()
        cut = next(i for i, d in enumerate(agg["dates"]) if d > trend.FIRST_DAILY_BY)
        for i in range(JUMP, cut):
            agg["n250"][i], agg["above250"][i] = 800, 320                      # 2005-03-31 之前从未到 1000
        with self.assertRaises(PanelError):
            trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS)
        data = trend.compute_panel(agg, self.l1, self.names, self.csi, STATUS, allow_partial=True)
        self.assertEqual(data["daily"]["n"][0], 800)

    def test_late_start_rejected_unless_partial(self):
        agg = _agg(start=date(2010, 1, 4))
        csi = _csi(agg)
        with self.assertRaises(PanelError):
            trend.compute_panel(agg, self.l1, self.names, csi, STATUS)
        data = trend.compute_panel(agg, self.l1, self.names, csi, STATUS, allow_partial=True)
        self.assertEqual(data["daily"]["dates"][0], agg["dates"][JUMP])
        small = _agg()
        small["n250"] = [min(v, 200) for v in small["n250"]]
        small["above250"] = [v * 2 // 5 for v in small["n250"]]
        with self.assertRaises(PanelError):                                    # 从未达到 300 只
            trend.compute_panel(small, self.l1, self.names, self.csi, STATUS, allow_partial=True)

    def test_industry_input_errors(self):
        names = dict(self.names)
        del names[COAL]
        with self.assertRaises(PanelError):
            trend.compute_panel(self.agg, self.l1, names, self.csi, STATUS)
        l1 = _l1()
        del l1["codes"][COAL]
        with self.assertRaises(PanelError):
            trend.compute_panel(self.agg, l1, self.names, self.csi, STATUS)
        l1 = _l1()
        l1["codes"][COAL]["close"] = [None] * len(l1["dates"])                 # 截掉后没有收盘
        with self.assertRaises(PanelError):
            trend.compute_panel(self.agg, l1, self.names, self.csi, STATUS)


class _FakeStore:
    def __init__(self, agg, cal=None):
        self.agg = agg
        self.cal = cal or []

    def load_agg(self):
        return self.agg

    def status(self):
        return {"nStocks": 5540, "nCodes": 5563, "lastDate": "2026-10-15", "aggLastDate": self.agg["dates"][-1]}

    def load_manifest(self):
        return {"codes": {"sh600000": {"status": "1", "out": ""}, "sh600001": {"status": "0", "out": "2009-12-29"},
                          "sh600519": {"status": "1", "out": ""}}}

    def load_calendar(self):
        return {"dates": self.cal}


class TrendBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agg = _agg()
        cls.l1 = _l1()
        cls.names = _names()
        cls.csi = _csi(cls.agg)

    def _patches(self, store, update):
        return [
            mock.patch.object(trend.market_state, "MarketStore", return_value=store),
            mock.patch.object(trend.market_state, "update_incremental", update),
            mock.patch.object(trend.sws, "get_level_history", return_value=self.l1),
            mock.patch.object(trend.sws, "get_index_list", return_value=list(self.names.items())),
            mock.patch.object(trend.csindex_hist, "get_index_history", return_value=self.csi),
        ]

    def test_build_lagging_note_and_asof(self):
        cal = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]
        store = _FakeStore(self.agg, cal)
        update = mock.Mock(return_value={"updated": [], "lagging": True, "lastDate": "2026-10-15", "today": "2026-10-21",
                                         "nQueries": 0, "seconds": 1.0})
        env = dict(os.environ)
        env.pop(trend.market_state.ENV_ALLOW_PARTIAL, None)
        with mock.patch.dict(os.environ, env, clear=True):
            patches = self._patches(store, update)
            for p in patches:
                p.start()
            try:
                res = trend.build()
            finally:
                for p in patches:
                    p.stop()
        update.assert_called_once()
        self.assertEqual(res["asOf"], res["data"]["daily"]["dates"][-1])
        self.assertEqual(res["asOf"], self.agg["dates"][-1])
        self.assertTrue(any("落后 3 个交易日" in n for n in res["notes"]))
        self.assertFalse(any("开发模式" in n for n in res["notes"]))
        self.assertEqual(len(res["sources"]), 3)
        self.assertEqual(res["data"]["universe"]["nStocksNow"], 2)
        self.assertEqual(res["data"]["universe"]["storeLastDate"], "2026-10-15")

    def test_build_allow_partial_skips_update(self):
        store = _FakeStore(self.agg)
        update = mock.Mock(side_effect=AssertionError("开发模式不应联网更新"))
        with mock.patch.dict(os.environ, {trend.market_state.ENV_ALLOW_PARTIAL: "1"}):
            patches = self._patches(store, update)
            for p in patches:
                p.start()
            try:
                with self.assertLogs("etl.trend", level="WARNING") as cm:
                    res = trend.build()
            finally:
                for p in patches:
                    p.stop()
        self.assertTrue(any("开发模式" in line for line in cm.output))
        update.assert_not_called()
        self.assertTrue(any("开发模式" in n for n in res["notes"]))
        self.assertEqual(res["asOf"], self.agg["dates"][-1])

    def test_build_missing_agg_rejected(self):
        empty = {"dates": []}
        store = _FakeStore(empty)
        update = mock.Mock(return_value={"updated": [], "lagging": False, "lastDate": "2026-10-20", "today": "2026-10-21"})
        env = dict(os.environ)
        env.pop(trend.market_state.ENV_ALLOW_PARTIAL, None)
        with mock.patch.dict(os.environ, env, clear=True):
            patches = self._patches(store, update)
            for p in patches:
                p.start()
            try:
                with self.assertRaises(PanelError) as cm:
                    trend.build()
            finally:
                for p in patches:
                    p.stop()
        self.assertIn("--rebuild", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
