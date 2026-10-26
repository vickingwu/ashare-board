# -*- coding: utf-8 -*-
"""http_get 的离线测试：主机限速、429/5xx/网络错误退避重试、其它 4xx 直抛、浏览器 UA。

用假 Session 和假时钟（time.monotonic / time.sleep 在测试期间被替换），不联网、不真睡。
"""
import unittest
from unittest import mock

import requests

from etl import common
from etl.common import PanelError


class _Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


class _FakeSession:
    """按脚本依次返回响应（或抛异常）；记录每次请求的 URL 与假时钟时间。"""

    def __init__(self, clock, script):
        self.clock = clock
        self.script = list(script)
        self.calls = []
        self.posts = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, self.clock.now))
        return self._next()

    def post(self, url, data=None, timeout=None, headers=None):
        self.calls.append((url, self.clock.now))
        self.posts.append((url, data, headers))
        return self._next()

    def _next(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class HttpGetTest(unittest.TestCase):
    CSI = "https://oss-ch.csindex.com.cn/x/{}.xls"
    QT = "https://qt.gtimg.cn/q={}"

    def _patched(self, script):
        clock = _Clock()
        sess = _FakeSession(clock, script)
        patches = [
            mock.patch.object(common, "_session", sess),
            mock.patch.object(common.time, "monotonic", clock.monotonic),
            mock.patch.object(common.time, "sleep", clock.sleep),
            mock.patch.dict(common._last_request_at, {}, clear=True),
        ]
        return clock, sess, patches

    def _run(self, script, urls):
        clock, sess, patches = self._patched(script)
        for p in patches:
            p.start()
        try:
            results = [common.http_get(u) for u in urls]
        finally:
            for p in reversed(patches):
                p.stop()
        return clock, sess, results

    def test_default_host_interval_is_half_second(self):
        clock, sess, results = self._run([_Resp(200, b"a"), _Resp(200, b"b")],
                                         [self.CSI.format(1), self.CSI.format(2)])
        self.assertEqual([r[0] for r in results], [b"a", b"b"])
        self.assertEqual(clock.sleeps, [0.5])
        self.assertAlmostEqual(sess.calls[1][1] - sess.calls[0][1], 0.5)

    def test_tencent_interval_is_0_3_second(self):
        clock, sess, _ = self._run([_Resp(200), _Resp(200)], [self.QT.format("a"), self.QT.format("b")])
        self.assertEqual(clock.sleeps, [0.3])
        self.assertAlmostEqual(sess.calls[1][1] - sess.calls[0][1], 0.3)

    def test_interval_is_per_host(self):
        clock, sess, _ = self._run([_Resp(200), _Resp(200)], [self.CSI.format(1), self.QT.format("a")])
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(len(sess.calls), 2)

    def test_5xx_retried_with_backoff_then_succeeds(self):
        with self.assertLogs("etl.http", level="WARNING") as cm:
            clock, sess, results = self._run([_Resp(503), _Resp(502), _Resp(200, b"ok")], [self.CSI.format(1)])
        self.assertEqual(results[0][0], b"ok")
        self.assertEqual(len(sess.calls), 3)
        # 退避 2s、4s；退避后距上次请求已超过 0.5s，不再额外限速
        self.assertEqual(clock.sleeps, [2.0, 4.0])
        self.assertEqual(len(cm.output), 2)
        self.assertIn("HTTP 503", cm.output[0])

    def test_429_exhausts_five_attempts(self):
        with self.assertLogs("etl.http", level="WARNING"):
            with self.assertRaises(PanelError) as ctx:
                self._run([_Resp(429)] * 5, [self.CSI.format(1)])
        self.assertIn("重试 5 次仍失败", str(ctx.exception))
        self.assertIn("HTTP 429", str(ctx.exception))

    def test_backoff_sequence_is_2_4_8_16(self):
        clock, sess, patches = self._patched([_Resp(500)] * 5)
        for p in patches:
            p.start()
        try:
            with self.assertLogs("etl.http", level="WARNING"):
                with self.assertRaises(PanelError):
                    common.http_get(self.CSI.format(1))
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(len(sess.calls), 5)
        self.assertEqual(clock.sleeps, [2.0, 4.0, 8.0, 16.0])

    def test_other_4xx_raises_immediately_without_retry(self):
        clock, sess, patches = self._patched([_Resp(404), _Resp(200)])
        for p in patches:
            p.start()
        try:
            with self.assertRaises(PanelError) as ctx:
                common.http_get(self.CSI.format(1))
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIn("HTTP 404", str(ctx.exception))
        self.assertEqual(len(sess.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_network_error_is_retried(self):
        with self.assertLogs("etl.http", level="WARNING") as cm:
            clock, sess, results = self._run([requests.ConnectionError("boom"), _Resp(200, b"ok")],
                                             [self.CSI.format(1)])
        self.assertEqual(results[0][0], b"ok")
        self.assertEqual(len(sess.calls), 2)
        self.assertEqual(clock.sleeps, [2.0])
        self.assertIn("ConnectionError", cm.output[0])

    # ---- http_post：与 GET 共用 _http 核心
    SGE = "https://www.sge.com.cn/graph/Dailyhq"

    def _run_post(self, script, data, headers=None):
        clock, sess, patches = self._patched(script)
        for p in patches:
            p.start()
        try:
            result = common.http_post(self.SGE, data=data, headers=headers)
        finally:
            for p in reversed(patches):
                p.stop()
        return clock, sess, result

    def test_post_sends_form_data_via_session_post(self):
        hdr = {"Referer": "https://www.sge.com.cn/sjzx/mrhq"}
        clock, sess, result = self._run_post([_Resp(200, b'{"time":[]}')], {"instid": "Au99.99"}, hdr)
        self.assertEqual(result[0], b'{"time":[]}')
        self.assertEqual(len(sess.posts), 1)
        self.assertEqual(sess.posts[0], (self.SGE, {"instid": "Au99.99"}, hdr))
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(common.HOST_MIN_INTERVAL["www.sge.com.cn"], 1.0)

    def test_post_retries_5xx_like_get(self):
        with self.assertLogs("etl.http", level="WARNING") as cm:
            clock, sess, result = self._run_post([_Resp(502), _Resp(200, b"ok")], {"instid": "Au99.99"})
        self.assertEqual(result[0], b"ok")
        self.assertEqual(len(sess.posts), 2)
        self.assertEqual(clock.sleeps, [2.0])
        self.assertIn("HTTP 502", cm.output[0])

    def test_post_4xx_raises_immediately(self):
        clock, sess, patches = self._patched([_Resp(403), _Resp(200)])
        for p in patches:
            p.start()
        try:
            with self.assertRaises(PanelError) as ctx:
                common.http_post(self.SGE, data={"instid": "Au99.99"})
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertEqual(len(sess.posts), 1)
        self.assertEqual(clock.sleeps, [])

    def test_new_hosts_have_polite_intervals(self):
        self.assertEqual(common.HOST_MIN_INTERVAL["www.csindex.com.cn"], 3.0)
        for host in ("datacenter-web.eastmoney.com", "www.sge.com.cn", "www.chinamoney.com.cn", "thedocs.worldbank.org"):
            self.assertGreaterEqual(common.HOST_MIN_INTERVAL[host], 1.0)

    # ---- M4 carry-over：中证间隔抖动、max_attempts=1、网络类错误判定
    def test_csindex_gap_has_plus_minus_one_second_jitter(self):
        self.assertEqual(common.HOST_JITTER["www.csindex.com.cn"], 1.0)
        with mock.patch.object(common.random, "uniform", lambda a, b: a):          # 最小
            self.assertAlmostEqual(common.throttle_gap("www.csindex.com.cn"), 2.0)
        with mock.patch.object(common.random, "uniform", lambda a, b: b):          # 最大
            self.assertAlmostEqual(common.throttle_gap("www.csindex.com.cn"), 4.0)
        seen = {common.throttle_gap("www.csindex.com.cn") for _ in range(50)}
        self.assertTrue(all(2.0 <= g <= 4.0 for g in seen))
        self.assertGreater(len(seen), 5)                                            # 真的在抖
        self.assertEqual(common.throttle_gap("qt.gtimg.cn"), 0.3)                    # 其它主机无抖动
        # 实际限速：两次中证请求之间 sleep 了抖动后的间隔
        clock, sess, patches = self._patched([_Resp(200), _Resp(200)])
        patches.append(mock.patch.object(common.random, "uniform", lambda a, b: 0.5))
        for p in patches:
            p.start()
        try:
            common.http_get("https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000300")
            common.http_get("https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300")
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(clock.sleeps, [3.5])

    def test_max_attempts_one_disables_inner_retry(self):
        clock, sess, patches = self._patched([_Resp(503), _Resp(200)])
        for p in patches:
            p.start()
        try:
            with self.assertRaises(PanelError) as ctx:
                common.http_get(self.CSI.format(1), max_attempts=1)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertIn("重试 1 次仍失败", str(ctx.exception))
        self.assertIn("HTTP 503", str(ctx.exception))
        self.assertEqual(len(sess.calls), 1)
        self.assertEqual(clock.sleeps, [])

    def test_is_network_error(self):
        yes = [
            "PanelError: HTTP 403 https://www.csindex.com.cn/x",
            "PanelError: 重试 5 次仍失败 (ConnectionError: ('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))) https://x",
            "PanelError: 重试 1 次仍失败 (HTTP 502) https://x",
            "HTTP 429 https://x",
            "ReadTimeout: HTTPSConnectionPool(host='x', port=443): Read timed out.",
            "PanelError: Baostock login 重试 3 次仍失败: 网络接收错误",
            "PanelError: csindex 000300: 中证指数官网暂不可达（熔断中）",
        ]
        no = [
            "PanelError: HTTP 404 https://x",
            "PanelError: csindex perf 不是 JSON: ValueError",
            "PanelError: 申万一级行业应为 31 个，拿到 30 个",
            "PanelError: TLS 证书校验失败 https://x",
            "KeyError: 'close'",
            None, "",
        ]
        for m in yes:
            self.assertTrue(common.is_network_error(m), m)
        for m in no:
            self.assertFalse(common.is_network_error(m), m)

    def test_real_session_uses_browser_user_agent(self):
        with mock.patch.object(common, "_session", None):
            sess = common._get_session()
            self.assertTrue(sess.headers["User-Agent"].startswith("Mozilla/5.0"))


if __name__ == "__main__":
    unittest.main()
