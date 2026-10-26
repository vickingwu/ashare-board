# -*- coding: utf-8 -*-
"""market_state 的离线测试：二进制编码、阈值与标志、合成全库的聚合重算、apply_day 续链、日增量（桩会话）、种子导入导出、CLI 辅助。"""
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from etl import market_state as ms
from etl.common import PanelError

N_DAYS = 300
LATE_START = 240          # 第三只股票从第 240 个交易日起上市
SUSPENDED_IDX = 100       # 第二只股票缺第 100 个交易日（停牌）
LIMIT_UP_IDX = 150        # 第一只股票在该日 +10 %
LIMIT_DOWN_IDX = 151      # 第二只股票在该日 −10 %


def _weekdays(n, end=date(2026, 10, 20)):
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


DATES = _weekdays(N_DAYS)
NEXT_DAY = "2026-10-21"


def _history_a():
    rows = []
    for i, d in enumerate(DATES):
        pct = 10.0 if i == LIMIT_UP_IDX else (5.0 if i == 0 else 0.1)
        rows.append((d, 10.0 + 0.01 * i, pct, 0))
    return rows


def _history_b():
    rows = []
    for i, d in enumerate(DATES):
        if i == SUSPENDED_IDX:
            continue
        pct = -10.0 if i == LIMIT_DOWN_IDX else -0.1
        rows.append((d, 100.0 - 0.1 * i, pct, 1 if i >= 290 else 0))      # 最后 10 天 ST
    return rows


def _history_c():
    return [(d, 50.0, 15.0 if i == LATE_START else 0.0, 0) for i, d in enumerate(DATES) if i >= LATE_START]


STOCKS = [
    {"key": "sh600000", "name": "甲", "ipo": DATES[0], "out": "", "status": "1"},
    {"key": "sz000001", "name": "乙", "ipo": DATES[0], "out": "", "status": "1"},
    {"key": "sz300750", "name": "丙", "ipo": DATES[LATE_START], "out": "", "status": "1"},
]
HISTORIES = {"sh600000": _history_a(), "sz000001": _history_b(), "sz300750": _history_c()}


def _make_store(root, cold_done=True):
    store = ms.MarketStore(root)
    store.ensure_dirs()
    manifest = ms._new_manifest(STOCKS)
    for key, rows in HISTORIES.items():
        ms._store_history(store, manifest, key, rows)
    manifest["coldDone"] = cold_done
    manifest["lastDate"] = DATES[-1]
    manifest["nStocks"] = 3
    store.save_manifest(manifest)
    cal = _weekdays(N_DAYS + 60, end=date(2026, 12, 31))
    store.save_calendar({"fetchedAt": "2026-10-20T19:00:00+08:00", "dates": cal})
    return store, manifest


def _flags_counters(store):
    """用 stock_day_flags 逐行累加（慢而直白），作为 rebuild_agg 的对照。"""
    by_date = {}
    for key, rows in HISTORIES.items():
        s = store.read_stock(key)
        closes = list(s.close)
        for i in range(len(s.dates)):
            d = ms.date_str(s.dates[i])
            fl = ms.stock_day_flags(closes[max(0, i + 1 - ms.MA_LONG):i + 1], float(s.pct[i]), bool(s.flags[i] & 1),
                                    i + 1, key[-6:], d)
            ms.add_flags(by_date.setdefault(d, {k: 0 for k in ms.AGG_KEYS}), fl)
    return ms.agg_from_counters(by_date)


class _StubSession:
    instances = []

    def __init__(self):
        self.n_queries = 0
        _StubSession.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ImportTest(unittest.TestCase):
    def test_no_baostock_import(self):
        self.assertIn("etl.market_state", sys.modules)
        self.assertNotIn("baostock", sys.modules)


class PackTest(unittest.TestCase):
    def test_round_trip_and_bad_magic(self):
        dates = [20260101 + i for i in range(10)]
        close = [10.123456 * (i + 1) for i in range(10)]
        pct = [-9.87 + i for i in range(10)]
        flags = [i % 2 for i in range(10)]
        data = ms.pack_series(dates, close, pct, flags)
        self.assertEqual(len(data), 12 + 10 * 13)
        self.assertEqual(data[:4], b"ASHM")
        s = ms.unpack_series(data)
        self.assertEqual(list(s.dates), dates)
        self.assertEqual(list(s.flags), flags)
        for a, b in zip(s.close, close):
            self.assertAlmostEqual(a / b, 1.0, delta=1e-5)
        for a, b in zip(s.pct, pct):
            self.assertAlmostEqual(a, b, delta=1e-4)
        with self.assertRaises(PanelError):
            ms.unpack_series(b"XXXX" + data[4:])
        with self.assertRaises(PanelError):
            ms.unpack_series(data[:-1])
        with self.assertRaises(PanelError):
            ms.pack_series(dates, close[:-1], pct, flags)
        empty = ms.unpack_series(ms.pack_series([], [], [], []))
        self.assertEqual(len(empty.dates), 0)

    def test_date_conversion(self):
        self.assertEqual(ms.date_int("2026-10-20"), 20261020)
        self.assertEqual(ms.date_str(20261020), "2026-10-20")
        self.assertEqual(ms.date_str(ms.date_int("2004-01-02")), "2004-01-02")
        with self.assertRaises(PanelError):
            ms.date_int("20261020")
        self.assertEqual(ms.prev_trading_day(["2026-10-19", "2026-10-20", "2026-10-21"], "2026-10-21"), "2026-10-20")
        self.assertEqual(ms.prev_trading_day(["2026-10-19", "2026-10-21"], "2026-10-20"), "2026-10-19")
        self.assertIsNone(ms.prev_trading_day(["2026-10-19"], "2026-10-19"))


class ThresholdAndFlagsTest(unittest.TestCase):
    def test_limit_threshold_table(self):
        self.assertEqual(ms.limit_threshold("688981", "2026-10-20", False), 19.8)
        self.assertEqual(ms.limit_threshold("688981", "2019-07-22", True), 19.8)
        self.assertEqual(ms.limit_threshold("300750", "2020-08-21", False), 9.8)
        self.assertEqual(ms.limit_threshold("300750", "2020-08-21", True), 4.8)
        self.assertEqual(ms.limit_threshold("300750", "2020-08-24", False), 19.8)
        self.assertEqual(ms.limit_threshold("300750", "2020-08-24", True), 19.8)
        self.assertEqual(ms.limit_threshold("600519", "2026-10-20", False), 9.8)
        self.assertEqual(ms.limit_threshold("600519", "2026-10-20", True), 4.8)
        self.assertEqual(ms.limit_threshold("000001", "2015-07-08", False), 9.8)
        self.assertEqual(ms.limit_threshold("689009", "2021-01-04", False), 19.8)      # 科创板 CDR 与 688 同为 20 %
        self.assertEqual(ms.limit_threshold("689009", "2020-11-05", True), 19.8)
        self.assertEqual(ms.STAR_PREFIXES, ("688", "689"))
        self.assertEqual(ms.CYB_20PCT_FROM, "2020-08-24")
        self.assertEqual(ms.LIMIT_TOL, 0.2)

    def test_stock_day_flags(self):
        rising = [100.0 + i for i in range(300)]
        f = ms.stock_day_flags(rising, 0.5, False, 300, "600000", "2026-10-20")
        self.assertEqual((f["traded"], f["has250"], f["above250"], f["has50"], f["above50"]), (1, 1, 1, 1, 1))
        self.assertEqual((f["adv"], f["dec"], f["flat"]), (1, 0, 0))
        self.assertEqual((f["limitEligible"], f["limitUp"], f["limitDown"]), (1, 0, 0))
        below = rising[:-1] + [50.0]
        f = ms.stock_day_flags(below, -1.0, False, 300, "600000", "2026-10-20")
        self.assertEqual((f["has250"], f["above250"], f["has50"], f["above50"], f["dec"]), (1, 0, 1, 0, 1))
        f = ms.stock_day_flags([10.0], 44.0, False, 1, "600000", "2026-10-20")
        self.assertEqual((f["has250"], f["has50"], f["adv"], f["dec"], f["flat"], f["limitEligible"]), (0, 0, 0, 0, 0, 0))
        f = ms.stock_day_flags([10.0, 10.5, 11.5], 9.85, False, 3, "600000", "2026-10-20")
        self.assertEqual((f["adv"], f["limitEligible"], f["limitUp"]), (1, 0, 0))
        tail10 = [10.0] * 10
        self.assertEqual(ms.stock_day_flags(tail10, 9.85, False, 10, "600000", "2026-10-20")["limitUp"], 1)
        self.assertEqual(ms.stock_day_flags(tail10, 4.9, True, 10, "600000", "2026-10-20")["limitUp"], 1)
        self.assertEqual(ms.stock_day_flags(tail10, 4.9, False, 10, "600000", "2026-10-20")["limitUp"], 0)
        self.assertEqual(ms.stock_day_flags(tail10, 9.85, False, 10, "688981", "2026-10-20")["limitUp"], 0)
        self.assertEqual(ms.stock_day_flags(tail10, 19.9, False, 10, "688981", "2026-10-20")["limitUp"], 1)
        self.assertEqual(ms.stock_day_flags(tail10, -9.85, False, 10, "000001", "2026-10-20")["limitDown"], 1)
        self.assertEqual(ms.stock_day_flags(tail10, 0.0, False, 10, "000001", "2026-10-20")["flat"], 1)
        with self.assertRaises(PanelError):
            ms.stock_day_flags(tail10, 1.0, False, 300, "600000", "2026-10-20")   # n_rows ≥ 250 但 tail 不够

    def test_agg_helpers(self):
        agg = ms.empty_agg()
        self.assertEqual(set(agg), {"dates", *ms.AGG_KEYS})
        c = {k: 0 for k in ms.AGG_KEYS}
        ms.add_flags(c, ms.stock_day_flags([10.0] * 10, 9.9, False, 10, "600000", "2026-10-20"))
        self.assertEqual((c["nTraded"], c["adv"], c["limitUp"], c["nLimitEligible"], c["n250"]), (1, 1, 1, 1, 0))
        ms.agg_append(agg, "2026-10-20", c)
        with self.assertRaises(PanelError):
            ms.agg_append(agg, "2026-10-20", c)
        a2 = ms.agg_from_counters({"2026-10-21": c, "2026-10-20": c})
        self.assertEqual(a2["dates"], ["2026-10-20", "2026-10-21"])
        self.assertEqual(a2["adv"], [1, 1])


class StoreTest(unittest.TestCase):
    def test_read_write_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ms.MarketStore(Path(tmp))
            self.assertEqual(len(store.read_stock("sh600000").dates), 0)
            store.write_stock("sh600000", ms.StockSeries([20260101], [10.0], [1.0], [0]))
            s = store.append_rows("sh600000", [(20260102, 10.5, 5.0, 1)])
            self.assertEqual(list(s.dates), [20260101, 20260102])
            self.assertEqual(list(s.flags), [0, 1])
            self.assertEqual(list(store.read_stock("sh600000").dates), [20260101, 20260102])
            with self.assertRaises(PanelError):
                store.append_rows("sh600000", [(20260102, 1.0, 0.0, 0)])
            self.assertFalse(list(Path(tmp, "stocks").glob("*.tmp")))
            st = store.status()
            self.assertEqual((st["exists"], st["nFiles"], st["aggDays"]), (False, 1, 0))


class RebuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.manifest = _make_store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_rebuild_agg_synthetic(self):
        agg = ms.rebuild_agg(self.store)
        self.assertEqual(agg["dates"], DATES)
        self.assertEqual(agg["dates"], sorted(agg["dates"]))
        self.assertTrue((Path(self.tmp.name) / "agg.json").is_file())
        n_traded = agg["nTraded"]
        self.assertEqual(n_traded[0], 2)
        self.assertEqual(n_traded[SUSPENDED_IDX], 1)
        self.assertEqual(n_traded[-1], 3)
        # n250 = 当日含当日 ≥ 250 行的股票数：甲从第 250 行（下标 249）起，乙缺一天所以晚一天
        self.assertEqual(agg["n250"][248], 0)
        self.assertEqual(agg["n250"][249], 1)
        self.assertEqual(agg["n250"][250], 2)
        self.assertEqual(agg["n250"][-1], 2)
        # above250 手算：甲单调上涨 → 1；乙单调下跌 → 0
        self.assertEqual(agg["above250"][249], 1)
        self.assertEqual(agg["above250"][250], 1)
        self.assertEqual(agg["above250"][-1], 1)
        # n50：甲乙从下标 49 起，丙（第 240 日上市）从其第 50 行（下标 289）起；丙横盘 → 不算站上
        self.assertEqual(agg["n50"][48], 0)
        self.assertEqual(agg["n50"][49], 2)
        self.assertEqual(agg["n50"][288], 2)
        self.assertEqual(agg["n50"][289], 3)
        self.assertEqual(agg["above50"][-1], 1)
        # adv + dec + flat == nTraded − 当日首行数
        first_days = {DATES[0]: 2, DATES[LATE_START]: 1}
        for i, d in enumerate(DATES):
            self.assertEqual(agg["adv"][i] + agg["dec"][i] + agg["flat"][i], n_traded[i] - first_days.get(d, 0), d)
        self.assertEqual(agg["flat"][-1], 1)                                     # 丙 pct 0
        # 涨跌停：甲第 150 日 +10 %，乙第 151 日 −10 %，丙首日 +15 % 不计（首行且创业板 19.8）
        self.assertEqual(agg["limitUp"][LIMIT_UP_IDX], 1)
        self.assertEqual(sum(agg["limitUp"]), 1)
        self.assertEqual(agg["limitDown"][LIMIT_DOWN_IDX], 1)
        self.assertEqual(sum(agg["limitDown"]), 1)
        self.assertEqual(agg["nLimitEligible"][3], 0)
        self.assertEqual(agg["nLimitEligible"][4], 2)
        self.assertEqual(agg["nLimitEligible"][-1], 3)

    def test_rebuild_matches_stock_day_flags(self):
        agg = ms.rebuild_agg(self.store)
        ref = _flags_counters(self.store)
        self.assertEqual(agg, ref)

    def test_rebuild_rejects_tail_store(self):
        self.manifest["kind"] = "tail"
        self.store.save_manifest(self.manifest)
        with self.assertRaises(PanelError):
            ms.rebuild_agg(self.store)

    def test_apply_day(self):
        agg = ms.rebuild_agg(self.store)
        manifest = self.store.load_manifest()
        last_a = manifest["codes"]["sh600000"]["lastClose"]
        rows = [
            {"key": "sh600000", "date": NEXT_DAY, "close": 13.0, "preclose": 12.9, "pct": 1.0, "is_st": 0},
            {"key": "sz000001", "date": NEXT_DAY, "close": 70.0, "preclose": 71.4, "pct": -2.0, "is_st": 1},
            {"key": "sh601001", "date": NEXT_DAY, "close": 12.34, "preclose": 8.57, "pct": 44.0, "is_st": 0},
        ]
        counters = ms.apply_day(self.store, NEXT_DAY, rows, manifest, agg)
        new = self.store.read_stock("sh601001")
        self.assertEqual(list(new.dates), [20261021])
        self.assertAlmostEqual(new.close[0], 12.34, delta=1e-5)
        self.assertEqual(manifest["codes"]["sh601001"]["lastClose"], 12.34)
        self.assertEqual(manifest["codes"]["sh601001"]["n"], 1)
        self.assertEqual(manifest["codes"]["sh601001"]["ipo"], NEXT_DAY)
        a = manifest["codes"]["sh600000"]
        self.assertAlmostEqual(a["lastClose"], last_a * 1.01, delta=1e-9)
        self.assertEqual((a["last"], a["n"]), (NEXT_DAY, N_DAYS + 1))
        sa = self.store.read_stock("sh600000")
        self.assertEqual(sa.dates[-1], 20261021)
        self.assertAlmostEqual(sa.close[-1], last_a * 1.01, delta=1e-3)
        self.assertEqual(manifest["lastDate"], NEXT_DAY)
        self.assertEqual(manifest["nStocks"], 4)
        self.assertEqual(agg["dates"][-1], NEXT_DAY)
        self.assertEqual(len(agg["dates"]), N_DAYS + 1)
        expect = {"nTraded": 3, "n250": 2, "above250": 1, "n50": 2, "above50": 1, "adv": 1, "dec": 1, "flat": 0,
                  "limitUp": 0, "limitDown": 0, "nLimitEligible": 2}
        self.assertEqual(counters, expect)
        self.assertEqual({k: agg[k][-1] for k in ms.AGG_KEYS}, expect)
        # 幂等：同一天再写一次（上次运行被打断后续跑）→ 不报错、不追加、聚合里这一天只有一份、清单不变、日志提到复用
        before = json.dumps(manifest, sort_keys=True, default=str)
        with self.assertLogs("etl.market", level="INFO") as cm:
            counters2 = ms.apply_day(self.store, NEXT_DAY, rows, manifest, agg)
        self.assertTrue(any("复用 3 行" in m for m in cm.output), cm.output)
        self.assertEqual(counters2, expect)
        self.assertEqual(agg["dates"].count(NEXT_DAY), 1)
        self.assertEqual(len(agg["dates"]), N_DAYS + 1)
        self.assertEqual({k: agg[k][-1] for k in ms.AGG_KEYS}, expect)
        self.assertEqual(len(self.store.read_stock("sh600000").dates), N_DAYS + 1)
        self.assertEqual(len(self.store.read_stock("sh601001").dates), 1)
        after = json.loads(json.dumps(manifest, sort_keys=True, default=str))
        before_d = json.loads(before)
        after.pop("updatedAt")
        before_d.pop("updatedAt")
        self.assertEqual(after, before_d)
        # 更早的一天仍然不能写（清单 last > d）
        with self.assertRaises(PanelError):
            ms.apply_day(self.store, DATES[-1], rows[:1], manifest, agg)

    def test_apply_day_resumes_after_interrupted_run(self):
        """模拟中断：股票文件已写了 d 这一行，但清单 / 聚合还是前一天 → 重跑复用那一行，续链结果与一次写完相同。"""
        agg = ms.rebuild_agg(self.store)
        manifest = self.store.load_manifest()
        rows = [
            {"key": "sh600000", "date": NEXT_DAY, "close": 13.0, "preclose": 12.9, "pct": 1.0, "is_st": 0},
            {"key": "sz000001", "date": NEXT_DAY, "close": 70.0, "preclose": 71.4, "pct": -2.0, "is_st": 1},
        ]
        last_a = manifest["codes"]["sh600000"]["lastClose"]
        # 「上次运行」只写完了甲的文件就死掉：清单与聚合没动
        self.store.append_rows("sh600000", [(ms.date_int(NEXT_DAY), last_a * 1.01, 1.0, 0)])
        with self.assertLogs("etl.market", level="INFO") as cm:
            counters = ms.apply_day(self.store, NEXT_DAY, rows, manifest, agg)
        self.assertTrue(any("复用 1 行" in m for m in cm.output), cm.output)
        self.assertEqual(counters["nTraded"], 2)
        a = manifest["codes"]["sh600000"]
        self.assertEqual((a["last"], a["n"]), (NEXT_DAY, N_DAYS + 1))
        self.assertAlmostEqual(a["lastClose"], last_a * 1.01, delta=1e-9)
        self.assertEqual(len(self.store.read_stock("sh600000").dates), N_DAYS + 1)
        self.assertEqual(len(self.store.read_stock("sz000001").dates), N_DAYS)        # 乙缺一天停牌 + 新的一天
        self.assertEqual(agg["dates"][-1], NEXT_DAY)
        self.assertEqual(agg["dates"].count(NEXT_DAY), 1)
        self.assertEqual(manifest["lastDate"], NEXT_DAY)

    def test_apply_day_rejects_mismatching_stored_row(self):
        """文件里已有 d 的行但收盘与重算不一致（不是同一条链的产物）→ PanelError，而不是悄悄复用。"""
        agg = ms.rebuild_agg(self.store)
        manifest = self.store.load_manifest()
        rows = [{"key": "sh600000", "date": NEXT_DAY, "close": 13.0, "preclose": 12.9, "pct": 1.0, "is_st": 0}]
        self.store.append_rows("sh600000", [(ms.date_int(NEXT_DAY), 999.0, 1.0, 0)])
        with self.assertRaises(PanelError) as cm:
            ms.apply_day(self.store, NEXT_DAY, rows, manifest, agg)
        self.assertIn("已有行与重算不一致", str(cm.exception))

    def test_agg_set_day(self):
        agg = ms.empty_agg()
        c1 = {k: 1 for k in ms.AGG_KEYS}
        c2 = {k: 2 for k in ms.AGG_KEYS}
        self.assertFalse(ms.agg_set_day(agg, "2026-10-20", c1))
        self.assertTrue(ms.agg_set_day(agg, "2026-10-20", c2))                       # 同一天 → 替换
        self.assertEqual((agg["dates"], agg["adv"]), (["2026-10-20"], [2]))
        self.assertFalse(ms.agg_set_day(agg, "2026-10-21", c1))
        self.assertEqual(agg["adv"], [2, 1])
        with self.assertRaises(PanelError):
            ms.agg_set_day(agg, "2026-10-20", c1)                                      # 早于最后一天

    def test_star_board_689_threshold_in_rebuild(self):
        """689xxx（科创板 CDR）与 688 同为 ±20 %：合成一只 689 股票的 +12 % 日不计涨停。"""
        manifest = self.store.load_manifest()
        rows = [(d, 100.0 + i, 12.0 if i == 20 else 0.5, 0) for i, d in enumerate(DATES[:30])]
        ms._store_history(self.store, manifest, "sh689009", rows)
        self.store.save_manifest(manifest)
        agg = ms.rebuild_agg(self.store)
        i20 = 20
        self.assertEqual(agg["limitUp"][i20], 0)
        self.assertEqual(agg["nLimitEligible"][i20], 3)                               # 甲、乙、689 三只 ≥ 5 行
        self.assertEqual(sum(agg["limitUp"]), 1)                                       # 只有甲第 150 日
        # 对照：同样的序列换成主板代码就是一次涨停
        ms._store_history(self.store, manifest, "sh600100", rows)
        agg2 = ms.rebuild_agg(self.store)
        self.assertEqual(agg2["limitUp"][i20], 1)


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store, self.manifest = _make_store(self.root)
        ms.rebuild_agg(self.store)
        ms._UPDATE_RESULT = None
        _StubSession.instances = []
        self.cross = {
            "2026-10-21": [{"key": "sh600000", "date": "2026-10-21", "close": 13.0, "preclose": 12.9, "pct": 1.0, "is_st": 0},
                           {"key": "sz000001", "date": "2026-10-21", "close": 70.0, "preclose": 71.4, "pct": -2.0, "is_st": 1},
                           {"key": "sz300750", "date": "2026-10-21", "close": 50.0, "preclose": 50.0, "pct": 0.0, "is_st": 0}],
            "2026-10-22": [{"key": "sh600000", "date": "2026-10-22", "close": 13.1, "preclose": 13.0, "pct": 0.77, "is_st": 0},
                           {"key": "sz000001", "date": "2026-10-22", "close": 71.0, "preclose": 70.0, "pct": 1.43, "is_st": 1},
                           {"key": "sz300750", "date": "2026-10-22", "close": 51.0, "preclose": 50.0, "pct": 2.0, "is_st": 0}],
            "2026-10-23": [],
        }
        self.fetched = []

    def tearDown(self):
        ms._UPDATE_RESULT = None
        self.tmp.cleanup()

    def _fake_cross(self, sess, d):
        sess.n_queries += 1
        self.fetched.append(d)
        return self.cross.get(d, [])

    def _run(self, today="2026-10-23", closed=True, budget=18.0, clock=None):
        patches = [
            mock.patch.object(ms, "MARKET_DIR", self.root),
            mock.patch.object(ms, "Session", _StubSession),
            mock.patch.object(ms, "fetch_cross_section", self._fake_cross),
            mock.patch.object(ms, "fetch_trade_dates", lambda sess, s, e: self.fail("日历已新鲜，不应重取")),
            mock.patch.object(ms, "cache_is_fresh", lambda fa: True),
            mock.patch.object(ms, "today_str", lambda: today),
            mock.patch.object(ms, "market_closed", lambda now=None: closed),
        ]
        if clock is not None:
            patches.append(mock.patch.object(ms, "_monotonic", clock))
        for p in patches:
            p.start()
        try:
            return ms.update_incremental(budget)
        finally:
            for p in patches:
                p.stop()

    def test_fetches_missing_days_and_stops_at_unpublished_today(self):
        res = self._run()
        self.assertEqual(self.fetched, ["2026-10-21", "2026-10-22", "2026-10-23"])
        self.assertEqual(res["updated"], ["2026-10-21", "2026-10-22"])
        self.assertFalse(res["lagging"])
        self.assertEqual(res["lastDate"], "2026-10-22")
        self.assertEqual(res["today"], "2026-10-23")
        self.assertEqual(res["nQueries"], 3)
        self.assertEqual(len(_StubSession.instances), 1)
        manifest = self.store.load_manifest()
        self.assertEqual(manifest["lastDate"], "2026-10-22")
        agg = self.store.load_agg()
        self.assertEqual(agg["dates"][-2:], ["2026-10-21", "2026-10-22"])
        self.assertEqual(agg["nTraded"][-1], 3)
        self.assertEqual(agg["adv"][-1], 3)
        a = self.store.read_stock("sh600000")
        self.assertEqual(a.dates[-1], 20261022)
        # 再次调用：记忆化，返回同一对象
        self.assertIs(self._run(), res)
        self.assertEqual(len(self.fetched), 3)

    def test_zero_rows_on_past_day_is_error(self):
        self.cross["2026-10-21"] = []
        with self.assertRaises(PanelError) as cm:
            self._run()
        self.assertIn("--skip-day 2026-10-21", str(cm.exception))
        # 记忆化：第二次重新抛出同一错误，不再联网
        with self.assertRaises(PanelError):
            self._run()
        self.assertEqual(self.fetched, ["2026-10-21"])

    def test_row_share_guard(self):
        self.cross["2026-10-21"] = self.cross["2026-10-21"][:1]                    # 1 < 70 % × 3
        with self.assertRaises(PanelError):
            self._run()
        self.assertEqual(self.store.load_manifest()["lastDate"], DATES[-1])

    def test_budget_exhausted_sets_lagging(self):
        ticks = iter([0.0] + [10_000.0] * 50)
        res = self._run(budget=1.0, clock=lambda: next(ticks))
        self.assertTrue(res["lagging"])
        self.assertEqual(res["updated"], [])
        self.assertEqual(self.fetched, [])
        self.assertEqual(self.store.load_manifest()["lastDate"], DATES[-1])

    def test_up_to_date_makes_no_connection(self):
        res = self._run(today="2026-10-20")
        self.assertEqual(res["updated"], [])
        self.assertEqual(len(_StubSession.instances), 0)
        self.assertEqual(self.fetched, [])

    def test_before_close_excludes_today(self):
        res = self._run(today="2026-10-22", closed=False)
        self.assertEqual(self.fetched, ["2026-10-21"])
        self.assertEqual(res["updated"], ["2026-10-21"])

    def test_skip_days_env_skips_listed_past_day_only(self):
        """ASHARE_MARKET_SKIP_DAYS 列出的缺失交易日不向 Baostock 查询、记入 skippedDays；其它缺失日照常处理；今天不会被跳过。"""
        self.cross["2026-10-21"] = []                                                 # 这一天 Baostock 永远没有数据
        env = {ms.ENV_SKIP_DAYS: " 2026-10-21 , 2026-10-23,2026-10-21"}
        with mock.patch.dict("os.environ", env), self.assertLogs("etl.market", level="WARNING") as cm:
            res = self._run()
        self.assertEqual(self.fetched, ["2026-10-22", "2026-10-23"])                 # 10-21 没查；10-23 = 今天不跳过
        self.assertEqual(res["skipped"], ["2026-10-21"])
        self.assertEqual(res["updated"], ["2026-10-22"])
        self.assertTrue(any("跳过 2026-10-21" in m for m in cm.output), cm.output)
        manifest = self.store.load_manifest()
        self.assertEqual(manifest["skippedDays"], ["2026-10-21"])
        self.assertEqual(manifest["lastDate"], "2026-10-22")
        agg = self.store.load_agg()
        self.assertEqual(agg["dates"][-1], "2026-10-22")
        self.assertNotIn("2026-10-21", agg["dates"])
        self.assertEqual(self.store.read_stock("sh600000").dates[-1], 20261022)
        self.assertEqual(ms.skip_days_from_env(), [])                                 # 环境变量已还原
        with mock.patch.dict("os.environ", {ms.ENV_SKIP_DAYS: "20261021"}):
            with self.assertRaises(PanelError):
                ms.skip_days_from_env()

    def test_skip_days_env_only_missing_day_makes_no_connection(self):
        """唯一缺失的一天就是要跳过的那天 → 记入 skippedDays、推进 lastDate，不开 Baostock 会话。"""
        with mock.patch.dict("os.environ", {ms.ENV_SKIP_DAYS: "2026-10-21"}), \
                self.assertLogs("etl.market", level="WARNING"):
            res = self._run(today="2026-10-22", closed=False)
        self.assertEqual(self.fetched, [])
        self.assertEqual(len(_StubSession.instances), 0)
        self.assertEqual((res["skipped"], res["updated"], res["lastDate"]), (["2026-10-21"], [], "2026-10-21"))
        self.assertEqual(self.store.load_manifest()["skippedDays"], ["2026-10-21"])

    def test_cold_in_progress_or_missing_store(self):
        self.store.save_progress({"done": 25, "total": 5563, "pending": ["x"] * 5538, "failed": []})
        manifest = self.store.load_manifest()
        manifest["coldDone"] = False
        self.store.save_manifest(manifest)
        with self.assertRaises(PanelError) as cm:
            self._run()
        self.assertIn("建设中 25/5563", str(cm.exception))
        ms._UPDATE_RESULT = None
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(ms, "MARKET_DIR", Path(empty)), \
                mock.patch.object(ms, "SEED_PATH", Path(empty) / "none.zip"):
            with self.assertRaises(PanelError) as cm2:
                ms.update_incremental()
        self.assertIn("market_seed.zip", str(cm2.exception))


class SeedTest(unittest.TestCase):
    def test_export_import_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, manifest = _make_store(root / "full")
            ms.rebuild_agg(store)
            seed = root / "seed" / "market_seed.zip"
            out = ms.export_seed(seed, tail_rows=10, store=store)
            self.assertEqual(out, seed)
            self.assertTrue(seed.is_file())
            self.assertFalse(seed.with_suffix(".zip.tmp").exists())
            with zipfile.ZipFile(seed) as zf:
                names = set(zf.namelist())
                self.assertTrue({"manifest.json", "agg.json", "calendar.json"} <= names)
                self.assertEqual(sorted(n for n in names if n.startswith("stocks/")),
                                 ["stocks/sh600000.bin", "stocks/sz000001.bin", "stocks/sz300750.bin"])
                m = json.loads(zf.read("manifest.json"))
                self.assertEqual((m["kind"], m["tailRows"], m["sourceKind"], m["coldDone"]), ("tail", 10, "full", True))
                self.assertEqual(m["codes"]["sh600000"]["n"], N_DAYS)                  # 清单保留全量行数
                s = ms.unpack_series(zf.read("stocks/sh600000.bin"))
                self.assertEqual(len(s.dates), 10)
                self.assertEqual(s.dates[-1], ms.date_int(DATES[-1]))
                self.assertEqual(len(ms.unpack_series(zf.read("stocks/sz300750.bin")).dates), 10)
            # 导入到空目录
            dst = ms.MarketStore(root / "tail")
            with mock.patch.object(ms, "MIN_SEED_STOCKS", 3):
                m2 = ms.import_seed(seed, dst)
            self.assertEqual(m2["kind"], "tail")
            st = dst.status()
            self.assertEqual((st["kind"], st["coldDone"], st["nFiles"], st["aggDays"], st["lastDate"]),
                             ("tail", True, 3, N_DAYS, DATES[-1]))
            self.assertEqual(len(dst.read_stock("sh600000").dates), 10)
            with self.assertRaises(PanelError):
                ms.rebuild_agg(dst)                                                  # tail 库不能重算
            with self.assertRaises(PanelError):
                ms.import_seed(seed, dst)                                            # 已有清单，拒绝覆盖
            with self.assertRaises(PanelError):
                ms.import_seed(seed, ms.MarketStore(root / "x"))                     # 默认 MIN_SEED_STOCKS=5000
            # ensure_store 从种子初始化
            with mock.patch.object(ms, "SEED_PATH", seed), mock.patch.object(ms, "MIN_SEED_STOCKS", 3):
                m3 = ms.ensure_store(ms.MarketStore(root / "auto"))
            self.assertEqual(m3["kind"], "tail")

    def test_export_requires_cold_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _make_store(Path(tmp), cold_done=False)
            with self.assertRaises(PanelError):
                ms.export_seed(Path(tmp) / "s.zip", store=store)

    def test_export_rejects_tail_store(self):
        """从种子解出的 tail 库（每只只有最近 252 行）不能再导出成「full 来源」的种子。"""
        with tempfile.TemporaryDirectory() as tmp:
            store, manifest = _make_store(Path(tmp))
            manifest["kind"] = "tail"
            store.save_manifest(manifest)
            with self.assertRaises(PanelError) as cm:
                ms.export_seed(Path(tmp) / "s.zip", store=store)
            self.assertIn("full 形态", str(cm.exception))
            self.assertIn("kind=tail", str(cm.exception))
            self.assertFalse((Path(tmp) / "s.zip").exists())

    def test_cold_start_rejects_tail_store(self):
        """库是种子 tail 形态时 make market-cold 不能悄悄变成空操作，也不能在 tail 库上建全史：先 make clean-cache-market。"""
        with tempfile.TemporaryDirectory() as tmp:
            store, manifest = _make_store(Path(tmp))
            manifest["kind"] = "tail"
            store.save_manifest(manifest)
            with mock.patch.object(ms, "MARKET_DIR", Path(tmp)), \
                    mock.patch.object(ms, "Session", lambda: self.fail("tail 库上不应打开 Baostock 会话")):
                with self.assertRaises(PanelError) as cm:
                    ms.cold_start()
            self.assertIn("tail", str(cm.exception))
            self.assertIn("clean-cache-market", str(cm.exception))


class MiscTest(unittest.TestCase):
    def test_skip_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _make_store(Path(tmp))
            with self.assertLogs("etl.market", level="WARNING"):
                m = ms.skip_day("2026-10-22", store)
            self.assertEqual(m["skippedDays"], ["2026-10-22"])
            self.assertEqual(m["lastDate"], "2026-10-22")
            m = ms.skip_day("2026-10-21", store)
            self.assertEqual(m["skippedDays"], ["2026-10-21", "2026-10-22"])
            self.assertEqual(m["lastDate"], "2026-10-22")
            self.assertEqual(store.load_manifest()["skippedDays"], ["2026-10-21", "2026-10-22"])

    def test_ensure_calendar_prefix_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ms.MarketStore(Path(tmp))
            store.ensure_dirs()
            cal = _weekdays(80, end=date(2026, 10, 20))
            store.save_calendar({"fetchedAt": "2026-10-19T19:00:00+08:00", "dates": cal})
            sess = _StubSession()
            with mock.patch.object(ms, "today_str", lambda: "2026-10-21"), \
                    mock.patch.object(ms, "cache_is_fresh", lambda fa: False):
                # 服务端把 2026-10-19（周一）改成非交易日 → 前缀变化
                fresh_bad = [d for d in _weekdays(80, end=date(2026, 12, 20)) if d >= "2026-09-21" and d != "2026-10-19"]
                with mock.patch.object(ms, "fetch_trade_dates", lambda s, a, b: fresh_bad):
                    with self.assertRaises(PanelError):
                        ms.ensure_calendar(store, sess)
                # 一致的服务端日历 → 接上未来 60 天
                calls = []

                def fake(s, a, b):
                    calls.append((a, b))
                    return [d for d in _weekdays(90, end=date(2026, 12, 20)) if a <= d <= b]

                with mock.patch.object(ms, "fetch_trade_dates", fake):
                    merged = ms.ensure_calendar(store, sess)
                self.assertEqual(calls, [("2026-09-20", "2026-12-20")])
                self.assertEqual(merged[:len(cal)], cal)
                self.assertGreater(merged[-1], "2026-12-01")
                self.assertEqual(merged, sorted(set(merged)))
                self.assertEqual(store.load_calendar()["dates"], merged)
            # 新鲜且够长 → 不取
            with mock.patch.object(ms, "today_str", lambda: "2026-10-21"), \
                    mock.patch.object(ms, "cache_is_fresh", lambda fa: True), \
                    mock.patch.object(ms, "fetch_trade_dates", lambda s, a, b: self.fail("不应重取")):
                self.assertEqual(ms.ensure_calendar(store, sess), merged)

    def test_cold_start_with_stubs_resumes_and_finalizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            histories = {k: list(v) for k, v in HISTORIES.items()}
            histories["sz000001"] = histories["sz000001"][:-1]                     # 乙早抓一天 → 收尾要补
            fetched = []

            def fake_history(sess, key, start, end):
                sess.n_queries += 1
                fetched.append(key)
                self.assertEqual((start, end), (ms.HISTORY_START, "2026-10-20"))
                return histories[key]

            def fake_cross(sess, d):
                sess.n_queries += 1
                fetched.append(("cs", d))
                self.assertEqual(d, "2026-10-20")
                return [{"key": "sh600000", "date": d, "close": 1.0, "preclose": 1.0, "pct": 0.1, "is_st": 0},
                        {"key": "sz000001", "date": d, "close": 1.0, "preclose": 1.0, "pct": -0.1, "is_st": 1},
                        {"key": "sz300750", "date": d, "close": 1.0, "preclose": 1.0, "pct": 0.0, "is_st": 0},
                        {"key": "sh688001", "date": d, "close": 1.0, "preclose": 1.0, "pct": 0.0, "is_st": 0}]

            cal = _weekdays(N_DAYS + 60, end=date(2026, 12, 31))
            patches = [
                mock.patch.object(ms, "MARKET_DIR", root),
                mock.patch.object(ms, "Session", _StubSession),
                mock.patch.object(ms, "fetch_stock_basic", lambda sess: list(STOCKS)),
                mock.patch.object(ms, "fetch_history", fake_history),
                mock.patch.object(ms, "fetch_cross_section", fake_cross),
                mock.patch.object(ms, "fetch_trade_dates", lambda sess, s, e: [d for d in cal if s <= d <= e]),
                mock.patch.object(ms, "today_str", lambda: "2026-10-20"),
                mock.patch.object(ms, "market_closed", lambda now=None: True),
            ]
            for p in patches:
                p.start()
            try:
                st = ms.cold_start(limit=2)
                self.assertEqual((st["coldDone"], st["done"], st["pending"], st["total"], st["failed"]), (False, 2, 1, 3, 0))
                self.assertEqual(fetched, ["sh600000", "sz000001"])
                progress = ms.MarketStore(root).load_progress()
                self.assertEqual(progress["pending"], ["sz300750"])
                self.assertEqual(ms.MarketStore(root).load_manifest()["codes"]["sz000001"]["n"], N_DAYS - 2)
                st2 = ms.cold_start()                                              # 续跑 → 收尾
                self.assertEqual((st2["coldDone"], st2["pending"], st2["lastDate"], st2["aggDays"]),
                                 (True, 0, DATES[-1], N_DAYS))
                self.assertEqual(fetched[2:], ["sz300750", ("cs", "2026-10-20")])
                store = ms.MarketStore(root)
                self.assertIsNone(store.load_progress())
                m = store.load_manifest()
                self.assertEqual(m["codes"]["sz000001"]["n"], N_DAYS - 1)           # 补齐一天
                self.assertEqual(m["codes"]["sz000001"]["last"], DATES[-1])
                self.assertNotIn("sh688001", m["codes"])                            # 未知代码不在收尾里起链
                self.assertEqual(m["nStocks"], 3)
                self.assertEqual(ms.cold_start()["coldDone"], True)                 # 已完成 → 直接返回
            finally:
                for p in patches:
                    p.stop()

    def test_cold_start_marks_bad_stock_failed_and_continues(self):
        """一只股票的解析 / 校验错误（PanelError 而非 BaostockError，如 2026-10-21 sh603856 的日期不递增）只能让这一只进 failed，
        冷启动继续抓下一只并正常收尾（2026-10-21 run 1 就是因为这里只捕获 BaostockError 而整进程退出）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fetched = []

            def fake_history(sess, key, start, end):
                sess.n_queries += 1
                fetched.append(key)
                if key == "sz000001":
                    raise PanelError("Baostock 个股日线日期不是严格递增: 2026-01-28 -> 2025-02-17")
                return HISTORIES[key]

            def fake_cross(sess, d):
                sess.n_queries += 1
                return [{"key": "sh600000", "date": d, "close": 1.0, "preclose": 1.0, "pct": 0.1, "is_st": 0},
                        {"key": "sz000001", "date": d, "close": 12.0, "preclose": 12.0, "pct": -0.1, "is_st": 0},
                        {"key": "sz300750", "date": d, "close": 1.0, "preclose": 1.0, "pct": 0.0, "is_st": 0}]

            cal = _weekdays(N_DAYS + 60, end=date(2026, 12, 31))
            patches = [
                mock.patch.object(ms, "MARKET_DIR", root),
                mock.patch.object(ms, "Session", _StubSession),
                mock.patch.object(ms, "fetch_stock_basic", lambda sess: list(STOCKS)),
                mock.patch.object(ms, "fetch_history", fake_history),
                mock.patch.object(ms, "fetch_cross_section", fake_cross),
                mock.patch.object(ms, "fetch_trade_dates", lambda sess, s, e: [d for d in cal if s <= d <= e]),
                mock.patch.object(ms, "today_str", lambda: "2026-10-20"),
                mock.patch.object(ms, "market_closed", lambda now=None: True),
                mock.patch.object(ms, "MAX_FAILED_SHARE", 0.5),                      # 3 只里坏 1 只；真实阈值 3 % 另有断言
            ]
            for p in patches:
                p.start()
            try:
                with self.assertLogs("etl.market", level="WARNING") as cm:
                    st = ms.cold_start()
                self.assertEqual(fetched, ["sh600000", "sz000001", "sz300750"])     # 坏股之后继续抓
                self.assertEqual((st["coldDone"], st["pending"], st["nStocks"], st["lastDate"]), (True, 0, 2, DATES[-1]))
                self.assertTrue(any("sz000001 抓取失败" in m and "不是严格递增" in m for m in cm.output), cm.output)
                self.assertTrue(any("1 只抓取失败" in m for m in cm.output), cm.output)
                m = ms.MarketStore(root).load_manifest()
                self.assertIsNone(m["codes"]["sz000001"]["n"])                       # 失败股无数据，收尾不为它起链
                self.assertEqual(m["codes"]["sh600000"]["n"], N_DAYS)
                self.assertIsNone(ms.MarketStore(root).load_progress())
            finally:
                for p in patches:
                    p.stop()
            self.assertEqual(ms.MAX_FAILED_SHARE, 0.03)                               # 真实阈值：失败 > 3 % 才整体报错

    def test_cold_start_circuit_breaker_on_failure_streak(self):
        """连续 MAX_CONSECUTIVE_FAILURES 只失败（疑似断网）→ 这些股票退回 pending 开头、存档、抛 PanelError，而不是全部记 failed。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            many = [{"key": f"sh60{i:04d}", "name": "", "ipo": DATES[0], "out": "", "status": "1"} for i in range(12)]

            def fake_history(sess, key, start, end):
                raise ms.BaostockError("Baostock query_history_k_data_plus 重试 3 次仍失败: 网络接收错误")

            patches = [
                mock.patch.object(ms, "MARKET_DIR", root),
                mock.patch.object(ms, "Session", _StubSession),
                mock.patch.object(ms, "fetch_stock_basic", lambda sess: list(many)),
                mock.patch.object(ms, "fetch_history", fake_history),
                mock.patch.object(ms, "today_str", lambda: "2026-10-20"),
                mock.patch.object(ms, "market_closed", lambda now=None: True),
            ]
            for p in patches:
                p.start()
            try:
                with self.assertLogs("etl.market", level="WARNING"):
                    with self.assertRaises(PanelError) as err:
                        ms.cold_start()
                self.assertIn("连续 10 只失败", str(err.exception))
                progress = ms.MarketStore(root).load_progress()
                self.assertEqual(progress["failed"], [])
                self.assertEqual(len(progress["pending"]), 12)                      # 10 只退回 + 2 只未抓
                self.assertEqual(progress["pending"][:10], [s["key"] for s in many[:10]])
            finally:
                for p in patches:
                    p.stop()


if __name__ == "__main__":
    unittest.main()
