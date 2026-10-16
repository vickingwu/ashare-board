# -*- coding: utf-8 -*-
import unittest

from etl.panels import loss


class LossTest(unittest.TestCase):
    def setUp(self):
        self.data = loss.compute()

    def test_shape(self):
        self.assertEqual(len(self.data["rows"]), 8)
        self.assertEqual(len(self.data["cols"]), 6)
        for line in self.data["matrix"]:
            self.assertEqual(len(line), 6)

    def test_values(self):
        m = self.data["matrix"]
        self.assertEqual(m[0][0], -0.2)      # 现价 80，买 100
        self.assertEqual(m[7][0], -0.9)      # 现价 10，买 100
        self.assertEqual(m[0][1], 0.0)       # 现价 80，买 80 → 0 而不是 null
        self.assertIsNone(m[0][2])           # 现价 80，买 70 → 已盈利
        self.assertEqual(self.data["rows"][0]["drawdownPct"], 20)

    def test_recovery(self):
        rec = {r["dropPct"]: r["gainPct"] for r in self.data["recovery"]}
        self.assertEqual(rec[20], 25.0)
        self.assertEqual(rec[50], 100.0)
        self.assertEqual(rec[80], 400.0)
        self.assertEqual(len(rec), 9)

    def test_build_envelope(self):
        res = loss.build()
        self.assertEqual(set(res), {"asOf", "sources", "notes", "data"})
        self.assertTrue(loss.STATIC)
        self.assertEqual(loss.PANEL_ID, "loss")


if __name__ == "__main__":
    unittest.main()
