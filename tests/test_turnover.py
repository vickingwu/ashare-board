# -*- coding: utf-8 -*-
"""turnover.compute_panel() 的离线测试：合成 6000 日成交额 + 带 300 日空档的流通市值。"""
import unittest
from datetime import date, timedelta
from unittest import mock

from etl.common import PanelError
from etl.panels import turnover

N = 6000
GAP_START, GAP_LEN = 2500, 300


def _weekdays(n, end=date(2026, 10, 16)):
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def _a_index(n=N):
    dates = _weekdays(n)
    amount = [3000.0 + (i % 97) * 40.0 + 0.1234 for i in range(n)]            # 3000–6840 亿元
    mv = [150000.0 + i * 40.0 for i in range(n)]                               # 15 万 → 39 万亿元
    tsw = [1.5 + (i % 7) * 0.1 for i in range(n)]
    for i in range(GAP_START, GAP_START + GAP_LEN):
        mv[i] = None
        tsw[i] = None
    mv[100] = None                                                             # 单日缺失不算空档
    return {"code": "801003", "name": "申万Ａ指", "fetchedAt": "2026-10-20T10:00:00+08:00", "dates": dates,
            "close": [4000.0] * n, "amount": amount, "volume": [1000.0] * n, "mv": mv, "turnoverSw": tsw}


class TurnoverComputeTest(unittest.TestCase):
    def setUp(self):
        self.a = _a_index()
        self.data = turnover.compute_panel(self.a)

    def test_shape(self):
        d = self.data
        self.assertEqual(len(d["dates"]), N)
        for k in ("amount", "mv", "turnover", "ma20", "swTurnover"):
            self.assertEqual(len(d[k]), N, k)
        self.assertEqual(d["mvKind"], "float")
        self.assertEqual(d["mvLabel"], "申万Ａ指成分流通市值（申万口径）")
        self.assertIn("不含北交所与 B 股", d["scope"])
        self.assertEqual(d["dates"], sorted(d["dates"]))

    def test_gap_detected(self):
        gaps = self.data["gaps"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["from"], self.a["dates"][GAP_START])
        self.assertEqual(gaps[0]["to"], self.a["dates"][GAP_START + GAP_LEN - 1])
        self.assertEqual(gaps[0]["n"], GAP_LEN)
        self.assertEqual(gaps[0]["nPresent"], 0)
        t = self.data["turnover"]
        self.assertTrue(all(t[i] is None for i in range(GAP_START, GAP_START + GAP_LEN)))
        self.assertIsNone(t[100])
        self.assertIsNotNone(t[101])
        self.assertEqual([i for i, v in enumerate(t) if v is None], [100] + list(range(GAP_START, GAP_START + GAP_LEN)))

    def test_identity_and_rounding(self):
        d = self.data
        for amt, mv, t in zip(d["amount"], d["mv"], d["turnover"]):
            if mv is None:
                self.assertIsNone(t)
            else:
                self.assertAlmostEqual(t, amt / mv * 100.0, delta=1e-3)
                self.assertEqual(round(t, 4), t)
            self.assertEqual(round(amt, 2), amt)
        self.assertEqual(d["amount"][0], 3000.12)
        self.assertTrue(all(v is None or round(v, 2) == v for v in d["swTurnover"]))

    def test_ma20(self):
        d = self.data
        self.assertTrue(all(v is None for v in d["ma20"][:19]))
        self.assertIsNone(d["ma20"][18])
        self.assertAlmostEqual(d["ma20"][19], round(sum(self.a["amount"][:20]) / 20, 2), places=2)
        self.assertAlmostEqual(d["ma20"][-1], round(sum(self.a["amount"][-20:]) / 20, 2), places=2)

    def test_summary(self):
        s = self.data["summary"]
        lt = s["latest"]
        self.assertEqual(lt["date"], self.a["dates"][-1])
        self.assertEqual(lt["amount"], self.data["amount"][-1])
        self.assertEqual(lt["mv"], self.data["mv"][-1])
        self.assertEqual(lt["turnover"], self.data["turnover"][-1])
        self.assertEqual(lt["ma20"], self.data["ma20"][-1])
        self.assertEqual(lt["swTurnover"], self.data["swTurnover"][-1])
        self.assertTrue(0 <= s["pctAll"] <= 100)
        present = [t for t in self.data["turnover"] if t is not None]
        self.assertAlmostEqual(s["pctAll"], 100.0 * sum(1 for v in present if v <= lt["turnover"]) / len(present), places=1)
        self.assertEqual(s["nDays"], N)
        self.assertEqual(s["nTurnover"], N - GAP_LEN - 1)

    def test_gap_with_stray_values_merged(self):
        a = _a_index()
        a["mv"][GAP_START + 100] = 200000.0                                    # 空档里零星 1 天有值
        a["mv"][GAP_START + 200] = 200000.0
        a["mv"][GAP_START + 201] = 200000.0
        d = turnover.compute_panel(a)
        self.assertEqual(len(d["gaps"]), 1)
        g = d["gaps"][0]
        self.assertEqual((g["from"], g["to"]), (a["dates"][GAP_START], a["dates"][GAP_START + GAP_LEN - 1]))
        self.assertEqual((g["n"], g["nPresent"]), (GAP_LEN - 3, 3))
        self.assertIsNotNone(d["turnover"][GAP_START + 100])                   # 有值日照常计算
        # 夹着 6 个有值日则不再合并
        for k in range(GAP_START + 150, GAP_START + 156):
            a["mv"][k] = 200000.0
        self.assertEqual(len(turnover.compute_panel(a)["gaps"]), 2)

    def test_non_positive_amount_dropped(self):
        a = _a_index()
        a["amount"][0] = 0.0
        a["amount"][5] = -1.0
        d = turnover.compute_panel(a)
        self.assertEqual(len(d["dates"]), N - 2)
        self.assertEqual(d["dates"][0], a["dates"][1])
        self.assertNotIn(a["dates"][5], d["dates"])

    def test_turnover_out_of_range_rejected(self):
        a = _a_index()
        a["mv"][10] = a["amount"][10] * 5.0                                    # 20 %
        with self.assertRaises(PanelError):
            turnover.compute_panel(a)

    def test_latest_mv_none_rejected(self):
        a = _a_index()
        a["mv"][-1] = None
        with self.assertRaises(PanelError):
            turnover.compute_panel(a)

    def test_too_few_rows_rejected(self):
        with self.assertRaises(PanelError):
            turnover.compute_panel(_a_index(4000))

    def test_build_envelope(self):
        with mock.patch.object(turnover.sws, "get_a_index", return_value=self.a):
            res = turnover.build()
        self.assertEqual(res["asOf"], self.a["dates"][-1])
        self.assertEqual(len(res["sources"]), 1)
        self.assertTrue(any(self.a["dates"][GAP_START] in n for n in res["notes"]))


if __name__ == "__main__":
    unittest.main()
