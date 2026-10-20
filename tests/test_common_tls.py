# -*- coding: utf-8 -*-
"""TLS 相关的离线测试：etl/certs 中间证书并入 CA bundle、Session 使用该 bundle、证书错误不重试直接 PanelError。"""
import ssl
import unittest
from pathlib import Path
from unittest import mock

import requests
import requests.certs

from etl import common
from etl.common import PanelError

INTERMEDIATE_CN = "GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1"


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CaBundleTest(unittest.TestCase):
    def test_shipped_intermediate_pem_exists(self):
        pems = sorted(common.CERTS_DIR.glob("*.pem"))
        self.assertTrue(any(p.name == "geotrust_g2_tls_cn_2022_ca1.pem" for p in pems))
        text = (common.CERTS_DIR / "geotrust_g2_tls_cn_2022_ca1.pem").read_text(encoding="utf-8")
        self.assertIn("-----BEGIN CERTIFICATE-----", text)
        self.assertIn("05dc9edc0fddfa975a1432ef806ec780078b5362ad45af76db15c907630db25d", text)

    def test_bundle_loads_and_contains_intermediate(self):
        path = common.ca_bundle_path()
        self.assertTrue(Path(path).is_file())
        self.assertEqual(Path(path).name, "ca_bundle.pem")
        ctx = ssl.create_default_context(cafile=path)
        cns = set()
        for cert in ctx.get_ca_certs():
            for rdn in cert.get("subject", ()):
                for key, value in rdn:
                    if key == "commonName":
                        cns.add(value)
        self.assertIn(INTERMEDIATE_CN, cns)
        self.assertIn("DigiCert Global Root G2", cns)      # certifi 的根仍在

    def test_bundle_starts_with_certifi_content(self):
        bundle = Path(common.ca_bundle_path()).read_bytes()
        certifi_bytes = Path(requests.certs.where()).read_bytes()
        self.assertEqual(bundle[:200], certifi_bytes[:200])
        self.assertGreater(len(bundle), len(certifi_bytes))

    def test_bundle_is_reused_when_up_to_date(self):
        p1 = Path(common.ca_bundle_path())
        mtime = p1.stat().st_mtime
        p2 = Path(common.ca_bundle_path())
        self.assertEqual(p1, p2)
        self.assertEqual(p2.stat().st_mtime, mtime)

    def test_session_verify_points_to_bundle(self):
        with mock.patch.object(common, "_session", None):
            sess = common._get_session()
            self.assertEqual(sess.verify, common.ca_bundle_path())
            self.assertNotEqual(sess.verify, False)

    def test_without_extra_pems_falls_back_to_certifi(self):
        with mock.patch.object(common, "CERTS_DIR", Path(common.CACHE_DIR) / "_no_such_dir_"):
            self.assertEqual(common.ca_bundle_path(), requests.certs.where())


class SslErrorTest(unittest.TestCase):
    def test_ssl_error_raises_panel_error_immediately(self):
        clock = _Clock()
        sess = _FakeSession([requests.exceptions.SSLError("certificate verify failed"), object()])
        with mock.patch.object(common, "_session", sess), \
                mock.patch.object(common.time, "monotonic", clock.monotonic), \
                mock.patch.object(common.time, "sleep", clock.sleep), \
                mock.patch.dict(common._last_request_at, {}, clear=True):
            with self.assertRaises(PanelError) as ctx:
                common.http_get("https://www.swsresearch.com/institute-sw/api/x")
        self.assertEqual(len(sess.calls), 1)
        self.assertEqual(clock.sleeps, [])
        msg = str(ctx.exception)
        self.assertIn("etl/certs", msg)
        self.assertIn("SSLError", msg)

    def test_sws_hosts_have_0_8_second_interval(self):
        self.assertEqual(common.HOST_MIN_INTERVAL["www.swsresearch.com"], 0.8)
        self.assertEqual(common.HOST_MIN_INTERVAL["www.swhyresearch.com"], 0.8)


if __name__ == "__main__":
    unittest.main()
