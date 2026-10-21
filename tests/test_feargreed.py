# -*- coding: utf-8 -*-
"""feargreed.compute_panel() / build() 的离线测试：固定种子的随机游走中证全指（1,600 日）+ 带 300 日空档的申万Ａ指
+ 从第 600 日起的两融 + 合成全市场聚合。"""
import math
import os
import random
import unittest
from datetime import date, timedelta
from unittest import mock

from etl import compute
from etl.common import PanelError
from etl.panels import feargreed, trend

N = 1600
GAP_START, GAP_LEN = 800, 300            # a_index.mv 在主轴下标 [800, 1100) 为 None
MARGIN_FROM = 600                        # 两融序列从主轴下标 600 起
BREADTH_FROM = 60                        # n50 在前 60 日 < 300
EXPECTED_START = 269                     # vol 从下标 20 起有值 + 250 个样本 → 第 4 个子分在 269 出现


def _weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _inputs(n=N, start=date(2005, 1, 4), seed=7):
    rnd = random.Random(seed)
    dates = _weekdays(start, n)
    close = [3000.0]
    for _ in range(n - 1):
        close.append(close[-1] * math.exp(rnd.gauss(0.0003, 0.015)))
    csi = {"code": "000985", "name": "中证全指", "dates": dates, "close": close, "pe": [None] * n}

    pre = _weekdays(start - timedelta(days=400), 200)                      # 申万Ａ指 比主轴早 200 个工作日
    a_dates = [d for d in pre if d < dates[0]] + dates
    na = len(a_dates)
    amount = [2000.0 + 800.0 * rnd.random() for _ in range(na)]
    mv = [100000.0 + i * 20.0 for i in range(na)]
    offset = na - n
    for i in range(GAP_START, min(GAP_START + GAP_LEN, n)):
        mv[offset + i] = None
    a_index = {"code": "801003", "name": "申万Ａ指", "dates": a_dates, "close": [1.0] * na, "amount": amount,
               "volume": [1.0] * na, "mv": mv, "turnoverSw": [None] * na}

    m_dates = dates[MARGIN_FROM:]
    rzye = [5000.0] if m_dates else []
    for _ in range(len(m_dates) - 1):
        rzye.append(rzye[-1] * (1.0 + rnd.gauss(0.0005, 0.01)))
    margin = {"fetchedAt": "x", "dates": m_dates, "rzye": rzye, "rqylje": [10.0] * len(m_dates), "total": rzye}

    adv = [rnd.randint(200, 900) for _ in range(n)]
    dec = [rnd.randint(200, 900) for _ in range(n)]
    n50 = [100 if i < BREADTH_FROM else 1200 + i for i in range(n)]
    above50 = [int(v * rnd.uniform(0.1, 0.9)) for v in n50]
    agg = {"dates": dates, "nTraded": [1400] * n, "n250": [1000] * n, "above250": [400] * n, "n50": n50,
           "above50": above50, "adv": adv, "dec": dec, "flat": [20] * n, "limitUp": [5] * n, "limitDown": [3] * n,
           "nLimitEligible": [1300] * n}
    return csi, a_index, margin, agg


class FearGreedComputeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csi, cls.a, cls.m, cls.agg = _inputs()
        cls.data = feargreed.compute_panel(cls.csi, cls.a, cls.m, cls.agg)
        cls.start_i = cls.csi["dates"].index(cls.data["dates"][0])

    def _master(self, i):
        """主轴下标 → 切片后的下标（None = 在起点之前）。"""
        j = i - self.start_i
        return j if j >= 0 else None

    def test_shape_and_ranges(self):
        d = self.data
        n = len(d["dates"])
        self.assertEqual(d["nDays"], n)
        self.assertEqual(d["start"], d["dates"][0])
        self.assertEqual(len(d["composite"]), n)
        self.assertEqual(len(d["overlay"]["csiAll"]), n)
        self.assertEqual(list(d["subs"]), [k for k, _n, _u in feargreed.SUBS])
        for k, s in d["subs"].items():
            self.assertEqual(len(s["raw"]), n, k)
            self.assertEqual(len(s["score"]), n, k)
            self.assertTrue(all(v is None or 0.0 <= v <= 100.0 for v in s["score"]), k)
            self.assertTrue(all(v is None or round(v, 1) == v for v in s["score"]), k)
            self.assertTrue(s["name"] and s["definition"])
        self.assertTrue(all(v is None or 0.0 <= v <= 100.0 for v in d["composite"]))
        self.assertTrue(all(v is None or round(v, 2) == v for v in d["composite"]))
        self.assertEqual((d["window"], d["minObs"], d["minSubs"]), (500, 250, 4))
        self.assertEqual(d["bands"], feargreed.BANDS)
        self.assertEqual(d["overlay"]["csiAll"], [round(v, 2) for v in self.csi["close"][self.start_i:]])

    def test_start_is_first_day_with_four_scores(self):
        d = self.data
        self.assertEqual(self.start_i, EXPECTED_START)
        self.assertEqual(d["dates"][0], self.csi["dates"][EXPECTED_START])
        keys = list(d["subs"])
        first = [d["subs"][k]["score"][0] for k in keys]
        self.assertEqual(sum(1 for v in first if v is not None), 4)             # vol / rsi / strength / turnover
        self.assertIsNotNone(d["composite"][0])
        # 主轴上起点前一天：重算分数只剩 3 个
        raw = feargreed.raw_series(self.csi, self.a, self.m, self.agg)
        prev = [feargreed.score_series(k, raw[k])[EXPECTED_START - 1] for k in keys]
        self.assertEqual(sum(1 for v in prev if v is not None), 3)
        self.assertTrue(d["start"] <= feargreed.START_BY)

    def test_composite_is_mean_of_stored_scores(self):
        d = self.data
        keys = list(d["subs"])
        n_with = 0
        for i, c in enumerate(d["composite"]):
            avail = [d["subs"][k]["score"][i] for k in keys if d["subs"][k]["score"][i] is not None]
            if c is None:
                self.assertLess(len(avail), feargreed.MIN_SUBS)
                continue
            n_with += 1
            self.assertGreaterEqual(len(avail), feargreed.MIN_SUBS)
            self.assertAlmostEqual(c, sum(avail) / len(avail), delta=0.006)
        self.assertEqual(n_with, len(d["composite"]))                           # 合成数据里起点之后天天有综合分

    def test_vol_score_is_inverted_percentile(self):
        vol = compute.realized_vol(self.csi["close"], feargreed.VOL_N, 250)
        pct = compute.rolling_percentile(vol, feargreed.WINDOW, feargreed.MIN_OBS)
        s = self.data["subs"]["vol"]
        for i in (EXPECTED_START, 700, 1200, N - 1):
            self.assertEqual(s["score"][self._master(i)], round(100.0 - pct[i], 1), i)
            self.assertEqual(s["raw"][self._master(i)], round(vol[i], 1))
        self.assertEqual(s["unit"], "%")

    def test_turnover_null_in_gap_and_60_days_after(self):
        s = self.data["subs"]["turnover"]["score"]
        raw = self.data["subs"]["turnover"]["raw"]
        gap_last = GAP_START + GAP_LEN - 1                                        # 最后一个空值日 1099
        for i in range(GAP_START, gap_last + 60):                                 # 空档内 + 之后 59 天：60 日均值窗口仍含空值
            self.assertIsNone(raw[self._master(i)], i)
            self.assertIsNone(s[self._master(i)], i)
        self.assertIsNotNone(raw[self._master(gap_last + 60)])                   # 第 60 天起窗口里不再有空值
        self.assertIsNotNone(raw[self._master(GAP_START - 1)])
        self.assertIsNotNone(s[self._master(GAP_START - 1)])
        # 分数要等窗口内有效样本重新凑够 250 个：空档结束后的第 60 天有原始值但仍然无分
        self.assertIsNone(s[self._master(gap_last + 60)])
        self.assertTrue(any(v is not None for v in s[self._master(gap_last + 60):]))
        self.assertEqual(self.data["subs"]["turnover"]["unit"], "倍")
        self.assertTrue(all(v is None or round(v, 2) == v for v in raw))

    def test_margin_score_needs_250_observations(self):
        s = self.data["subs"]["margin"]
        first_raw = MARGIN_FROM + feargreed.MARGIN_N
        first_score = first_raw + feargreed.MIN_OBS - 1                          # 869
        self.assertEqual(first_score, 869)
        self.assertTrue(all(v is None for v in s["raw"][:self._master(first_raw)]))
        self.assertIsNotNone(s["raw"][self._master(first_raw)])
        self.assertTrue(all(v is None for v in s["score"][:self._master(first_score)]))
        self.assertIsNotNone(s["score"][self._master(first_score)])
        self.assertTrue(all(v is not None for v in s["score"][self._master(first_score):]))

    def test_breadth_and_strength_alignment(self):
        b = self.data["subs"]["breadth"]
        exp = [a / n * 100.0 if n >= feargreed.MIN_N50 else None for a, n in zip(self.agg["above50"], self.agg["n50"])]
        for i in (EXPECTED_START, 500, N - 1):
            self.assertEqual(b["raw"][self._master(i)], round(exp[i], 1))
        st = self.data["subs"]["strength"]
        ratio = [a / (a + d) * 100.0 for a, d in zip(self.agg["adv"], self.agg["dec"])]
        for i in (EXPECTED_START, 500, N - 1):
            self.assertAlmostEqual(st["raw"][self._master(i)], sum(ratio[i - 4:i + 1]) / 5, delta=0.051)
        rsi = compute.rsi(self.csi["close"], 14)
        self.assertEqual(self.data["subs"]["rsi"]["raw"][-1], round(rsi[-1], 1))

    def test_band_boundaries(self):
        bl = compute.band_label
        self.assertEqual(bl(0, feargreed.BANDS), "极度恐慌")
        self.assertEqual(bl(24.99, feargreed.BANDS), "极度恐慌")
        self.assertEqual(bl(25, feargreed.BANDS), "恐慌")
        self.assertEqual(bl(44.9, feargreed.BANDS), "恐慌")
        self.assertEqual(bl(45, feargreed.BANDS), "中性")
        self.assertEqual(bl(55, feargreed.BANDS), "贪婪")
        self.assertEqual(bl(74.99, feargreed.BANDS), "贪婪")
        self.assertEqual(bl(75, feargreed.BANDS), "极度贪婪")
        self.assertEqual(bl(100, feargreed.BANDS), "极度贪婪")
        self.assertIsNone(bl(None, feargreed.BANDS))

    def test_latest(self):
        lt = self.data["latest"]
        d = self.data
        self.assertEqual(lt["date"], d["dates"][-1])
        self.assertEqual(lt["composite"], d["composite"][-1])
        self.assertEqual(lt["band"], compute.band_label(lt["composite"], feargreed.BANDS))
        self.assertEqual(len(lt["subs"]), 6)
        self.assertEqual([s["key"] for s in lt["subs"]], [k for k, _n, _u in feargreed.SUBS])
        for s in lt["subs"]:
            self.assertIn("unit", s)
            self.assertEqual(s["unit"], dict((k, u) for k, _n, u in feargreed.SUBS)[s["key"]])
            self.assertEqual(s["raw"], d["subs"][s["key"]]["raw"][-1])
            self.assertEqual(s["score"], d["subs"][s["key"]]["score"][-1])
            self.assertEqual(s["definition"], feargreed.DEFINITIONS[s["key"]])

    def test_size_budget_enforced(self):
        with mock.patch.object(feargreed, "SIZE_BUDGET", 1000):
            with self.assertRaises(PanelError):
                feargreed.compute_panel(self.csi, self.a, self.m, self.agg)

    def test_late_start_rejected_unless_partial(self):
        csi, a, m, agg = _inputs(start=date(2010, 1, 4))
        with self.assertRaises(PanelError):
            feargreed.compute_panel(csi, a, m, agg)
        data = feargreed.compute_panel(csi, a, m, agg, allow_partial=True)
        self.assertEqual(data["start"], csi["dates"][EXPECTED_START])

    def test_too_short_or_too_few_subs_rejected(self):
        csi, a, m, agg = _inputs(n=400)
        with self.assertRaises(PanelError):
            feargreed.compute_panel(csi, a, m, agg)
        csi, a, m, agg = _inputs()
        agg["n50"] = [100] * N                                                    # 宽度永远无分
        a["mv"] = [None] * len(a["mv"])                                           # 换手永远无分
        m["dates"] = []
        m["rzye"] = []                                                            # 两融永远无分 → 只剩 3 个
        with self.assertRaises(PanelError):
            feargreed.compute_panel(csi, a, m, agg)


class _FakeStore:
    def __init__(self, agg):
        self.agg = agg

    def load_agg(self):
        return self.agg

    def status(self):
        return {"nStocks": 5540, "nCodes": 5563, "lastDate": "2026-10-20", "aggLastDate": self.agg["dates"][-1]}

    def load_manifest(self):
        return {"codes": {"sh600000": {"status": "1", "out": ""}}}

    def load_calendar(self):
        return {"dates": []}


class FearGreedBuildTest(unittest.TestCase):
    def _run(self, env_partial):
        csi, a, m, agg = _inputs()
        store = _FakeStore(agg)
        update = mock.Mock(return_value={"updated": [], "lagging": False, "lastDate": "2026-10-20", "today": "2026-10-21",
                                         "nQueries": 0, "seconds": 0.1})
        env = dict(os.environ)
        env.pop(trend.market_state.ENV_ALLOW_PARTIAL, None)
        if env_partial:
            env[trend.market_state.ENV_ALLOW_PARTIAL] = "1"
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(trend.market_state, "MarketStore", return_value=store), \
                mock.patch.object(trend.market_state, "update_incremental", update), \
                mock.patch.object(feargreed.csindex_hist, "get_index_history", return_value=csi), \
                mock.patch.object(feargreed.sws, "get_a_index", return_value=a), \
                mock.patch.object(feargreed.sse_margin, "get_margin_history", return_value=m):
            if env_partial:
                with self.assertLogs("etl.trend", level="WARNING"):
                    res = feargreed.build()
            else:
                res = feargreed.build()
        return res, update, csi

    def test_build_envelope(self):
        res, update, csi = self._run(env_partial=False)
        update.assert_called_once()
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], res["data"]["dates"][-1])
        self.assertEqual(res["asOf"], csi["dates"][-1])
        self.assertEqual(len(res["sources"]), 4)
        self.assertTrue(any("沪市" in n for n in res["notes"]))
        self.assertTrue(any("2011-01-04" in n for n in res["notes"]))
        self.assertTrue(any("极度贪婪" in n for n in res["notes"]))
        self.assertFalse(any("开发模式" in n for n in res["notes"]))

    def test_build_allow_partial(self):
        res, update, _csi = self._run(env_partial=True)
        update.assert_not_called()
        self.assertTrue(any("开发模式" in n for n in res["notes"]))


if __name__ == "__main__":
    unittest.main()
