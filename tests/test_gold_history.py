# -*- coding: utf-8 -*-
"""gold_history 的离线测试：世界银行 xlsx 解析（标准库 zip + xml）、中间价解析、拼接系数、种子构建与校验、CLI。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from etl import compute
from etl.common import PanelError
from etl.sources import gold_history as gh

FX = Path(__file__).resolve().parent / "fixtures"
G = 31.1034768


def _fx(name):
    return (FX / name).read_bytes()


class WorldBankTest(unittest.TestCase):
    def test_mini_xlsx(self):
        out = gh.parse_worldbank_monthly(_fx("worldbank_cmo_mini.xlsx"))
        self.assertEqual(out["months"], ["2006-01", "2006-02", "2006-03", "2006-04"])
        self.assertEqual(out["gold"][0], 549.864)
        self.assertEqual(out["gold"][1], 555.0)
        self.assertIsNone(out["gold"][2])                    # 「…」→ None
        self.assertEqual(out["gold"][3], 610.65)

    def test_other_column_and_errors(self):
        out = gh.parse_worldbank_monthly(_fx("worldbank_cmo_mini.xlsx"), column="Silver")
        self.assertEqual(out["gold"], [9.1, 9.5, 10.3, 12.6])
        with self.assertRaises(PanelError):
            gh.parse_worldbank_monthly(_fx("worldbank_cmo_mini.xlsx"), column="Platinum")
        with self.assertRaises(PanelError):
            gh.parse_worldbank_monthly(b"<html>not a zip</html>")


class ChinamoneyTest(unittest.TestCase):
    def test_trim_fixture_ascending(self):
        rows = gh.parse_chinamoney(_fx("chinamoney_2006_trim.json"))
        self.assertEqual(len(rows), 25)
        self.assertEqual(rows[0], ("2006-01-04", 8.0702))
        self.assertEqual(rows[1], ("2006-01-05", 8.0678))
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))

    def test_bad_payloads(self):
        with self.assertRaises(PanelError):
            gh.parse_chinamoney(json.dumps({"head": {"rep_code": "500"}, "records": []}).encode())
        with self.assertRaises(PanelError):
            gh.parse_chinamoney(json.dumps({"head": {"rep_code": "200"}}).encode())
        with self.assertRaises(PanelError):
            gh.parse_chinamoney(b"<html>")


class SpliceTest(unittest.TestCase):
    def test_factor_and_min_overlap(self):
        months = compute.months_between("2016-12", "2024-12")
        a = {m: 100.0 for m in months}
        b = {m: 102.0 for m in months}
        k, n = gh.splice_factor(a, b, "2016-12", "2024-12")
        self.assertAlmostEqual(k, 1.02, places=12)
        self.assertEqual(n, 97)
        # 区间外与缺失的月份不计
        b2 = dict(b)
        b2.pop("2017-01")
        k2, n2 = gh.splice_factor(a, b2, "2016-12", "2018-12")
        self.assertEqual(n2, 24)
        self.assertAlmostEqual(k2, 1.02, places=12)
        with self.assertRaises(PanelError):
            gh.splice_factor(a, b, "2016-12", "2017-12")      # 13 < 24


def _inputs(k=1.02):
    """LEG_A 只含 2006-01；重叠期 2016-12 → 2018-11 共 24 个月，SGE = 原始腿 × k。"""
    overlap = compute.months_between("2016-12", "2018-11")
    wb = {"months": ["2006-01"] + overlap, "gold": [549.864] + [1200.0] * len(overlap)}
    fx = [("2006-01-04", 8.0668)] + [(compute.month_end_date(m), 6.8) for m in overlap]
    sge = {m: 1200.0 * 6.8 / G * k for m in overlap}
    return wb, fx, sge


class BuildSeedTest(unittest.TestCase):
    def test_single_month_arithmetic(self):
        wb, fx, sge = _inputs(1.02)
        with mock.patch.object(gh, "LEG_A_END", "2006-01"), mock.patch.object(gh, "OVERLAP", ("2016-12", "2018-11")):
            seed = gh.build_seed(wb, fx, sge, built_at="2026-10-23T15:00:00+08:00")
        self.assertEqual(seed["version"], 1)
        self.assertEqual(seed["months"], ["2006-01"])
        self.assertEqual(seed["legAEnd"], "2006-01")
        self.assertEqual(seed["overlapMonths"], 24)
        self.assertAlmostEqual(seed["k"], 1.02, places=6)
        raw = 549.864 * 8.0668 / G
        self.assertAlmostEqual(seed["cnyPerGramRaw"][0], raw, delta=1e-4)
        self.assertAlmostEqual(seed["cnyPerGram"][0], raw * 1.02, delta=raw * 1e-6 + 0.005)
        self.assertEqual(seed["usdPerOz"], [549.864])
        self.assertEqual(seed["usdcny"], [8.0668])
        self.assertEqual(seed["builtAt"], "2026-10-23T15:00:00+08:00")
        self.assertIn(gh.WB_URL, seed["sources"])

    def test_missing_month_is_error(self):
        wb, fx, sge = _inputs()
        with mock.patch.object(gh, "LEG_A_END", "2006-02"), mock.patch.object(gh, "OVERLAP", ("2016-12", "2018-11")):
            with self.assertRaises(PanelError):
                gh.build_seed(wb, fx, sge)                     # 2006-02 没有世界银行值
        wb2 = dict(wb, months=wb["months"] + ["2006-02"], gold=wb["gold"] + [555.0])
        with mock.patch.object(gh, "LEG_A_END", "2006-02"), mock.patch.object(gh, "OVERLAP", ("2016-12", "2018-11")):
            with self.assertRaises(PanelError):
                gh.build_seed(wb2, fx, sge)                    # 2006-02 没有中间价


class LoadSeedTest(unittest.TestCase):
    def test_mini_seed_loads(self):
        with mock.patch.object(gh, "LEG_A_END", "2006-12"):
            seed = gh.load_seed(FX / "gold_seed_mini.json")
        self.assertEqual(seed["k"], 1.02)
        self.assertEqual(len(seed["months"]), 12)
        self.assertAlmostEqual(seed["cnyPerGram"][0], round(549.864 * 8.0668 / G, 4) * 1.02, delta=0.01)

    def test_full_range_required_by_default(self):
        with self.assertRaises(PanelError):
            gh.load_seed(FX / "gold_seed_mini.json")           # 12 个月 ≠ 131 个月

    def test_invalid_seeds(self):
        mini = json.loads(_fx("gold_seed_mini.json"))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(gh, "LEG_A_END", "2006-12"):
            p = Path(tmp) / "seed.json"

            def check(obj):
                p.write_text(json.dumps(obj), encoding="utf-8")
                with self.assertRaises(PanelError):
                    gh.load_seed(p)

            check(dict(mini, k=1.5))
            check(dict(mini, version=2))
            check(dict(mini, months=mini["months"][:-1], cnyPerGram=mini["cnyPerGram"][:-1]))        # 缺月
            shuffled = mini["months"][:5] + mini["months"][6:] + [mini["months"][5]]
            check(dict(mini, months=shuffled))                                                         # 不连续
            check(dict(mini, cnyPerGram=[0.0] + mini["cnyPerGram"][1:]))
            with self.assertRaises(PanelError):
                gh.load_seed(Path(tmp) / "missing.json")


class CliTest(unittest.TestCase):
    def test_build_seed_cli_writes_file(self):
        wb, fx, sge = _inputs(1.01)
        hist = {"dates": [compute.month_end_date(m) for m in sge], "close": list(sge.values())}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(gh, "LEG_A_END", "2006-01"), mock.patch.object(gh, "OVERLAP", ("2016-12", "2018-11")), \
                mock.patch.object(gh, "FX_YEARS", [2006]), \
                mock.patch.object(gh, "fetch_worldbank", lambda: wb), \
                mock.patch.object(gh, "fetch_chinamoney_year", lambda year: fx), \
                mock.patch("etl.sources.sge_gold.get_au9999", lambda: hist), \
                mock.patch("etl.sources.sge_gold.monthly_averages", lambda h, complete_only=True: (list(sge), list(sge.values()), [20] * len(sge))):
            out = Path(tmp) / "gold_seed.json"
            with redirect_stdout(io.StringIO()) as buf:
                rc = gh.main(["--build-seed", "--out", str(out)])
                rc_help = gh.main([])                          # 没有 --build-seed → 用法 + 1
            self.assertEqual(rc, 0)
            self.assertEqual(rc_help, 1)
            seed = json.loads(out.read_text(encoding="utf-8"))
            self.assertAlmostEqual(seed["k"], 1.01, places=6)
            self.assertIn('"overlapMonths": 24', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
