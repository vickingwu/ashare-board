# -*- coding: utf-8 -*-
"""compute 的 M3 技术指标：EMA 种子、MACD 预热、RSI Wilder、对数收益/已实现波动率、n 期变化、滚动分位、区间标签。"""
import math
import unittest

from etl import compute
from etl.common import PanelError

BANDS = [
    {"lo": 0, "hi": 25, "label": "极度恐慌"},
    {"lo": 25, "hi": 45, "label": "恐慌"},
    {"lo": 45, "hi": 55, "label": "中性"},
    {"lo": 55, "hi": 75, "label": "贪婪"},
    {"lo": 75, "hi": 100, "label": "极度贪婪"},
]


class EmaMacdTest(unittest.TestCase):
    def test_ema_constant_and_seed(self):
        self.assertEqual(compute.ema([1, 1, 1, 1], 3), [1.0, 1.0, 1.0, 1.0])
        out = compute.ema([None, None, 5.0, 7.0], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertEqual(out[2], 5.0)                                        # 以首个非 None 值为种子
        self.assertAlmostEqual(out[3], 5.0 + 0.5 * (7.0 - 5.0))

    def test_ema_lags_a_ramp(self):
        ramp = [float(i) for i in range(1, 101)]
        out = compute.ema(ramp, 10)
        self.assertTrue(all(out[i] < ramp[i] for i in range(1, 100)))
        self.assertTrue(all(out[i] > out[i - 1] for i in range(1, 100)))
        self.assertAlmostEqual(ramp[-1] - out[-1], 4.5, delta=0.01)        # 稳态滞后 (n−1)/2

    def test_ema_internal_none_rejected(self):
        with self.assertRaises(PanelError):
            compute.ema([1.0, None, 2.0], 3)
        with self.assertRaises(PanelError):
            compute.ema([1.0, 2.0], 0)

    def test_macd_constant_series(self):
        dif, dea, hist = compute.macd([10.0] * 60)
        self.assertEqual(compute.MACD_WARMUP, 34)
        for arr in (dif, dea, hist):
            self.assertEqual(len(arr), 60)
            self.assertTrue(all(v is None for v in arr[:34]))
            self.assertTrue(all(v is not None and abs(v) < 1e-12 for v in arr[34:]))

    def test_macd_rising_series_positive_and_hist_identity(self):
        values = [100.0 * 1.01 ** i for i in range(80)]
        dif, dea, hist = compute.macd(values)
        self.assertIsNone(dif[33])
        self.assertTrue(all(v > 0 for v in dif[34:]))
        for i in range(34, 80):
            self.assertAlmostEqual(hist[i], 2.0 * (dif[i] - dea[i]), places=12)


class RsiTest(unittest.TestCase):
    def test_monotone_series(self):
        rising = [float(i) for i in range(1, 31)]
        out = compute.rsi(rising)
        self.assertTrue(all(v is None for v in out[:14]))
        self.assertTrue(all(v == 100.0 for v in out[14:]))
        falling = [float(31 - i) for i in range(1, 31)]
        out2 = compute.rsi(falling)
        self.assertIsNone(out2[13])
        self.assertTrue(all(v == 0.0 for v in out2[14:]))

    def test_hand_computed_sample(self):
        values = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                  45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
        n = 14
        # 独立循环重算 Wilder RSI
        changes = [values[i] - values[i - 1] for i in range(1, len(values))]
        gains = [max(c, 0.0) for c in changes]
        losses = [max(-c, 0.0) for c in changes]
        avg_g = sum(gains[:n]) / n
        avg_l = sum(losses[:n]) / n
        expected = [None] * n
        expected.append(100.0 - 100.0 / (1.0 + avg_g / avg_l))
        for k in range(n, len(changes)):
            avg_g = (avg_g * (n - 1) + gains[k]) / n
            avg_l = (avg_l * (n - 1) + losses[k]) / n
            expected.append(100.0 - 100.0 / (1.0 + avg_g / avg_l))
        out = compute.rsi(values, n)
        self.assertEqual(len(out), len(values))
        self.assertEqual(out[:n], [None] * n)
        for got, exp in zip(out[n:], expected[n:]):
            self.assertAlmostEqual(got, exp, delta=0.01)
        self.assertAlmostEqual(out[14], 70.46, delta=0.05)                  # 经典样本：首个 RSI ≈ 70.5

    def test_short_or_none_input(self):
        self.assertEqual(compute.rsi([1.0, 2.0, 3.0]), [None, None, None])
        self.assertEqual(compute.rsi([]), [])
        with self.assertRaises(PanelError):
            compute.rsi([1.0, None, 2.0] * 10)


class VolatilityTest(unittest.TestCase):
    def test_log_returns(self):
        out = compute.log_returns([100.0, 110.0])
        self.assertIsNone(out[0])
        self.assertAlmostEqual(out[1], math.log(1.1), places=12)
        self.assertEqual(compute.log_returns([100.0, None, 0.0, 50.0]), [None, None, None, None])
        self.assertEqual(compute.log_returns([]), [])

    def test_realized_vol_constant_return_is_zero(self):
        values = [100.0 * 1.005 ** i for i in range(40)]
        out = compute.realized_vol(values, 20)
        self.assertTrue(all(v is None for v in out[:20]))                   # 第 0 位收益 None，故前 20 位为 None
        self.assertTrue(all(abs(v) < 1e-9 for v in out[20:]))

    def test_realized_vol_alternating_returns(self):
        values = [100.0]
        for i in range(1, 60):
            values.append(values[-1] * (1.01 if i % 2 else 0.99))
        out = compute.realized_vol(values, 20, 250)
        rets = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]
        window = rets[-20:]
        m = sum(window) / 20
        std = math.sqrt(sum((r - m) ** 2 for r in window) / 19)
        self.assertAlmostEqual(out[-1], std * math.sqrt(250) * 100.0, delta=1e-9)
        self.assertGreater(out[-1], 10.0)

    def test_rolling_std(self):
        self.assertEqual(compute.rolling_std([1.0, 2.0, 3.0, 4.0], 2)[:1], [None])
        self.assertAlmostEqual(compute.rolling_std([1.0, 2.0, 3.0, 4.0], 2)[1], math.sqrt(0.5))
        self.assertAlmostEqual(compute.rolling_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0], 8, ddof=0)[-1], 2.0)
        self.assertEqual(compute.rolling_std([1.0, None, 3.0], 2), [None, None, None])


class ChangeAndRatioTest(unittest.TestCase):
    def test_pct_change_n(self):
        self.assertEqual(compute.pct_change_n([100, None, 120], 2), [None, None, 20.0])
        out = compute.pct_change_n([100.0, 110.0, 121.0], 1)
        self.assertIsNone(out[0])
        self.assertAlmostEqual(out[1], 10.0)
        self.assertAlmostEqual(out[2], 10.0)
        self.assertEqual(compute.pct_change_n([0.0, 5.0], 1), [None, None])

    def test_ratio_to_rolling_mean(self):
        out = compute.ratio_to_rolling_mean([2, 2, 2, 4], 2)
        self.assertIsNone(out[0])
        self.assertEqual(out[1], 1.0)
        self.assertEqual(out[2], 1.0)
        self.assertAlmostEqual(out[3], 4.0 / 3.0)
        self.assertEqual(compute.ratio_to_rolling_mean([0.0, 0.0], 2), [None, None])   # 均值为 0


class PercentileTest(unittest.TestCase):
    def test_increasing_series(self):
        out = compute.rolling_percentile(list(range(1, 11)), window=5, min_obs=3)
        self.assertEqual(out[:2], [None, None])
        self.assertEqual(out[2:], [100.0] * 8)

    def test_decreasing_series_once_full(self):
        out = compute.rolling_percentile([float(10 - i) for i in range(10)], window=5, min_obs=3)
        self.assertAlmostEqual(out[2], 100.0 / 3.0)                         # [10,9,8] 中 ≤ 8 的 1 个
        self.assertEqual(out[4:], [20.0] * 6)                               # 窗口满后 1/5

    def test_none_skipped_and_min_obs(self):
        vals = [5.0, None, 3.0, None, 4.0, 1.0]
        out = compute.rolling_percentile(vals, window=4, min_obs=2)
        self.assertIsNone(out[0])                                            # N = 1 < 2
        self.assertIsNone(out[1])                                            # 当前值 None
        self.assertEqual(out[2], 50.0)                                       # 窗口 [5, None, 3] → N=2，≤3 的 1 个
        self.assertIsNone(out[3])
        self.assertEqual(out[4], 100.0)                                      # 窗口 [None,3,None,4]（5 已掉出）→ N=2，≤4 的 2 个
        self.assertAlmostEqual(out[5], 100.0 / 3.0)                          # 窗口 [3,None,4,1] → ≤1 的 1 个 / 3

    def test_ties_inclusive_and_window_eviction(self):
        out = compute.rolling_percentile([1.0, 1.0, 1.0, 2.0, 1.0], window=3, min_obs=1)
        self.assertEqual(out[0], 100.0)
        self.assertEqual(out[2], 100.0)
        self.assertEqual(out[3], 100.0)
        self.assertAlmostEqual(out[4], 100.0 * 2 / 3)                        # 窗口 [1,2,1]: ≤1 的 2 个
        self.assertEqual(compute.rolling_percentile([], 5, 1), [])


class MeanAndBandTest(unittest.TestCase):
    def test_mean_of_available(self):
        self.assertEqual(compute.mean_of_available([1, None, 3], 2), (2.0, 2))
        self.assertEqual(compute.mean_of_available([1], 2), (None, 1))
        self.assertEqual(compute.mean_of_available([None, None], 1), (None, 0))
        self.assertEqual(compute.mean_of_available([], 0), (None, 0))

    def test_band_label(self):
        self.assertEqual(compute.band_label(25, BANDS), "恐慌")               # 左闭右开
        self.assertEqual(compute.band_label(24.99, BANDS), "极度恐慌")
        self.assertEqual(compute.band_label(0, BANDS), "极度恐慌")
        self.assertEqual(compute.band_label(55, BANDS), "贪婪")
        self.assertEqual(compute.band_label(74.9, BANDS), "贪婪")
        self.assertEqual(compute.band_label(75, BANDS), "极度贪婪")
        self.assertEqual(compute.band_label(100, BANDS), "极度贪婪")          # 最后一个区间右端闭合
        self.assertIsNone(compute.band_label(None, BANDS))
        self.assertIsNone(compute.band_label(100.5, BANDS))
        self.assertIsNone(compute.band_label(-1, BANDS))


if __name__ == "__main__":
    unittest.main()
