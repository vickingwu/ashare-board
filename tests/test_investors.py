# -*- coding: utf-8 -*-
"""investors.compute_panel() / build() 的离线测试：合成 4,000 日两融 + 每日概况 + 申万Ａ指 + 中证全指。"""
import unittest
from datetime import date, timedelta
from unittest import mock

from etl import compute
from etl.common import PanelError
from etl.panels import investors

N = 4000


def _weekdays(start: date, n: int):
    out = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_world():
    dates = _weekdays(date(2010, 3, 31), N)
    rzye = [1000.0 + 2.5 * i for i in range(N)]
    total = [v * 1.015 for v in rzye]
    margin = {"dates": dates, "rzye": rzye, "rqylje": [v * 0.015 for v in rzye], "total": total}
    last = dates[-1]
    overview = {"date": last, "listNum": 2318, "totalValue": 700000.0, "negoValue": 573200.34, "tradeAmt": 7068.29,
                "turnoverPct": 1.2331}
    a_dates = _weekdays(date(2010, 1, 4), N + 100)
    a_index = {"dates": a_dates, "amount": [10000.0 + (i % 7) * 100 for i in range(len(a_dates))],
               "mv": [400000.0 + 10.0 * i for i in range(len(a_dates))]}
    csi_dates = list(dates)
    gone = csi_dates.pop(100)                                                  # 中证缺一天
    csi = {"dates": csi_dates, "close": [4000.0 + i * 0.5 for i in range(len(csi_dates))]}
    return dict(margin=margin, overview=overview, a_index=a_index, csi=csi, gone=gone)


class InvestorsComputeTest(unittest.TestCase):
    def setUp(self):
        self.w = make_world()
        self.data = investors.compute_panel(self.w["margin"], self.w["overview"], self.w["a_index"], self.w["csi"])
        self.cards = {c["key"]: c for c in self.data["cards"]}

    def test_card_order_and_fields(self):
        self.assertEqual([c["key"] for c in self.data["cards"]], investors.CARD_ORDER)
        for c in self.data["cards"]:
            self.assertEqual(set(c), {"key", "label", "value", "unit", "date", "scope", "note"})
            self.assertIsNotNone(c["date"], c["key"])
            self.assertIsNotNone(c["value"], c["key"])

    def test_margin_cards(self):
        m = self.w["margin"]
        self.assertEqual(self.cards["marginTotal"]["value"], round(m["total"][-1], 2))
        self.assertEqual(self.cards["marginFin"]["value"], round(m["rzye"][-1], 2))
        self.assertEqual(self.cards["marginTotal"]["date"], m["dates"][-1])
        self.assertEqual(self.cards["marginTotal"]["scope"], "沪市")
        self.assertEqual(self.cards["marginTotal"]["unit"], "亿元")

    def test_ratio_and_sse_cards(self):
        m, ov = self.w["margin"], self.w["overview"]
        expected = round(round(m["total"][-1], 2) / ov["negoValue"] * 100, 2)
        self.assertAlmostEqual(self.cards["marginRatio"]["value"], expected, places=9)
        self.assertEqual(self.cards["marginRatio"]["unit"], "%")
        self.assertEqual(self.cards["sseNego"]["value"], 573200.34)
        self.assertEqual(self.cards["sseNego"]["date"], ov["date"])
        self.assertAlmostEqual(self.cards["sseTurnover"]["value"], round(7068.29 / 573200.34 * 100, 2), places=9)
        self.assertEqual(self.data["latest"]["ratioPct"], self.cards["marginRatio"]["value"])

    def test_a_share_cards(self):
        a = self.w["a_index"]
        self.assertEqual(self.cards["aMv"]["value"], round(a["mv"][-1], 2))
        self.assertEqual(self.cards["aAmount"]["value"], round(a["amount"][-1], 2))
        self.assertEqual(self.cards["aAmount"]["date"], a["dates"][-1])
        ma = sum(a["amount"][-20:]) / 20
        self.assertAlmostEqual(self.cards["aTurnover20"]["value"], round(ma / a["mv"][-1] * 100, 2), places=9)
        self.assertIn("申万", self.cards["aMv"]["scope"])

    def test_chart_alignment(self):
        ch = self.data["chart"]
        n = len(self.w["margin"]["dates"])
        self.assertEqual(len(ch["dates"]), n)
        self.assertEqual(len(ch["total"]), n)
        self.assertEqual(len(ch["rzye"]), n)
        self.assertEqual(len(ch["csiAll"]), n)
        i = ch["dates"].index(self.w["gone"])
        self.assertIsNone(ch["csiAll"][i])
        self.assertEqual(ch["csiAll"][0], 4000.0)
        self.assertEqual(ch["csiAll"][i + 1], round(4000.0 + 100 * 0.5, 2))
        self.assertTrue(all(round(v, 2) == v for v in ch["total"]))
        lt = self.data["latest"]
        self.assertEqual(lt["date"], ch["dates"][-1])
        self.assertEqual(lt["total"], ch["total"][-1])
        self.assertEqual(lt["rzye"], ch["rzye"][-1])

    def test_overview_none(self):
        data = investors.compute_panel(self.w["margin"], None, self.w["a_index"], self.w["csi"])
        cards = {c["key"]: c for c in data["cards"]}
        self.assertIsNone(cards["marginRatio"]["value"])
        self.assertIn("当日概况未取到", cards["marginRatio"]["note"])
        self.assertIsNone(cards["sseNego"]["value"])
        self.assertIsNone(cards["sseTurnover"]["value"])
        self.assertIsNone(data["latest"]["ratioPct"])
        self.assertEqual(cards["marginTotal"]["value"], self.cards["marginTotal"]["value"])

    def test_overview_date_mismatch(self):
        ov = dict(self.w["overview"])
        ov["date"] = self.w["margin"]["dates"][-2]
        data = investors.compute_panel(self.w["margin"], ov, self.w["a_index"], self.w["csi"])
        cards = {c["key"]: c for c in data["cards"]}
        self.assertIsNone(cards["marginRatio"]["value"])
        self.assertIn("当日概况未取到", cards["marginRatio"]["note"])
        self.assertEqual(cards["sseNego"]["value"], 573200.34)              # 概况本身照常展示，标注自己的日期
        self.assertEqual(cards["sseNego"]["date"], ov["date"])

    def test_ratio_out_of_range_rejected(self):
        ov = dict(self.w["overview"])
        ov["negoValue"] = self.w["margin"]["total"][-1] / 0.2                 # 比值 20 %
        with self.assertRaises(PanelError):
            investors.compute_panel(self.w["margin"], ov, self.w["a_index"], self.w["csi"])

    def test_short_margin_rejected(self):
        m = {k: v[:3000] for k, v in self.w["margin"].items()}
        with self.assertRaises(PanelError):
            investors.compute_panel(m, self.w["overview"], self.w["a_index"], self.w["csi"])

    def test_total_below_rzye_rejected(self):
        m = dict(self.w["margin"])
        m["total"] = list(m["total"])
        m["total"][-1] = m["rzye"][-1] - 1
        with self.assertRaises(PanelError):
            investors.compute_panel(m, self.w["overview"], self.w["a_index"], self.w["csi"])

    def test_csi_coverage_rejected(self):
        csi = {"dates": self.w["csi"]["dates"][:1000], "close": self.w["csi"]["close"][:1000]}
        with self.assertRaises(PanelError):
            investors.compute_panel(self.w["margin"], self.w["overview"], self.w["a_index"], csi)

    def test_mv_missing_gives_null_cards(self):
        a = dict(self.w["a_index"])
        a["mv"] = list(a["mv"])
        a["mv"][-1] = None
        data = investors.compute_panel(self.w["margin"], self.w["overview"], a, self.w["csi"])
        cards = {c["key"]: c for c in data["cards"]}
        self.assertIsNone(cards["aMv"]["value"])
        self.assertIsNone(cards["aTurnover20"]["value"])
        self.assertIsNotNone(cards["aAmount"]["value"])

    def test_dropped(self):
        self.assertEqual([d["key"] for d in self.data["dropped"]], ["newInvestors", "fundAum", "northbound", "szMargin"])


class InvestorsBuildTest(unittest.TestCase):
    def test_build_envelope(self):
        w = make_world()
        raw_ov = {"date": w["overview"]["date"], "rows": {"01": {}}}
        with mock.patch.object(investors.sse_margin, "get_margin_history", return_value=w["margin"]), \
                mock.patch.object(investors.sse_overview, "get_latest_overview", return_value=raw_ov) as glo, \
                mock.patch.object(investors.sse_overview, "a_share_totals", return_value=w["overview"]) as ast, \
                mock.patch.object(investors.sws, "get_a_index", return_value=w["a_index"]), \
                mock.patch.object(investors.csindex_hist, "get_index_history", return_value=w["csi"]) as gh:
            res = investors.build()
        glo.assert_called_once_with(w["margin"]["dates"][-1])
        ast.assert_called_once_with(raw_ov)
        gh.assert_called_once_with("000985")
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertEqual(res["asOf"], w["margin"]["dates"][-1])
        self.assertEqual(len(res["data"]["cards"]), 8)
        self.assertEqual(len(res["sources"]), 3)
        joined = "\n".join(res["notes"])
        for word in ("沪市", "深市", "新增投资者", "公募", "北向", "07:53"):
            self.assertIn(word, joined)
        # 禁用的填充语（拼接写出，仓库全文检索这些词本身应为 0 命中）
        for banned in ("不构成" + "投资建议", "灵" + "感来自", "个人" + "学习用途", "自" + "用"):
            self.assertNotIn(banned, joined)

    def test_build_without_overview(self):
        w = make_world()
        with mock.patch.object(investors.sse_margin, "get_margin_history", return_value=w["margin"]), \
                mock.patch.object(investors.sse_overview, "get_latest_overview", return_value=None), \
                mock.patch.object(investors.sws, "get_a_index", return_value=w["a_index"]), \
                mock.patch.object(investors.csindex_hist, "get_index_history", return_value=w["csi"]):
            res = investors.build()
        cards = {c["key"]: c for c in res["data"]["cards"]}
        self.assertIsNone(cards["marginRatio"]["value"])
        self.assertEqual(compute.month_key(res["asOf"]), w["margin"]["dates"][-1][:7])


if __name__ == "__main__":
    unittest.main()
