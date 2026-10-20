# -*- coding: utf-8 -*-
"""legulegu 申万行业总览的离线测试：二级→一级父级解析（非前缀样例）、未知上级报错、30 天 TTL 与失败时沿用旧缓存。"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from etl import common
from etl.common import PanelError
from etl.sources import legulegu

FX = Path(__file__).resolve().parent / "fixtures"
HTML = (FX / "legulegu_sw_overview_trim.html").read_text(encoding="utf-8")

# 代码前缀推不出父级的 8 个样例（探测 §0.2）
NON_PREFIX_PARENTS = {
    "801092": "801880",   # 汽车
    "801101": "801750",   # 计算机
    "801102": "801770",   # 通信
    "801191": "801790",   # 非银金融
    "801072": "801890",   # 机械设备
    "801991": "801170",   # 交通运输
    "801993": "801210",   # 社会服务
    "801995": "801760",   # 传媒
}


class ParseOverviewTest(unittest.TestCase):
    def test_level1(self):
        parsed = legulegu.parse_sw_overview(HTML)
        self.assertEqual(len(parsed["level1"]), 31)
        self.assertEqual(parsed["level1"]["801010"], "农林牧渔")
        self.assertEqual(parsed["level1"]["801880"], "汽车")
        self.assertEqual(parsed["level1"]["801980"], "美容护理")

    def test_level2_parents_non_prefix_cases(self):
        parsed = legulegu.parse_sw_overview(HTML)
        self.assertEqual(len(parsed["level2"]), 28)
        for code, parent in NON_PREFIX_PARENTS.items():
            self.assertEqual(parsed["level2"][code]["parent"], parent, code)
            self.assertEqual(parsed["level2"][code]["parentName"], parsed["level1"][parent], code)
        self.assertEqual(parsed["level2"]["801092"]["parentName"], "汽车")

    def test_level2_fields(self):
        parsed = legulegu.parse_sw_overview(HTML)
        item = parsed["level2"]["801016"]
        self.assertEqual(item, {"name": "种植业", "parent": "801010", "parentName": "农林牧渔", "count": 20})
        for v in parsed["level2"].values():
            self.assertIn(v["parent"], parsed["level1"])
            self.assertGreater(v["count"], 0)

    def test_unknown_parent_is_error(self):
        bad = HTML.replace("[农林牧渔]", "[未知行业]", 1)
        with self.assertRaises(PanelError) as ctx:
            legulegu.parse_sw_overview(bad)
        self.assertIn("未知行业", str(ctx.exception))

    def test_too_few_level1_is_error(self):
        cut = HTML[:HTML.index('<div id="801880.SI"')]
        with self.assertRaises(PanelError):
            legulegu.parse_sw_overview(cut)


class ParentMapCacheTest(unittest.TestCase):
    NOW = datetime(2026, 10, 19, 12, 0, tzinfo=common.TZ)

    def _cached(self, days_ago):
        fetched = self.NOW - timedelta(days=days_ago)
        return {"fetchedAt": fetched.isoformat(),
                "level1": {"801010": "农林牧渔"},
                "level2": {"801016": {"name": "种植业", "parent": "801010", "parentName": "农林牧渔", "count": 20}}}

    def _run(self, cached, http):
        store = {}
        with mock.patch.object(common, "now_sh", lambda: self.NOW), \
                mock.patch.object(legulegu, "cache_load", lambda sub, key: cached), \
                mock.patch.object(legulegu, "cache_save", lambda sub, key, obj: store.update({key: obj})), \
                mock.patch.object(legulegu, "http_get", http), \
                mock.patch.object(legulegu, "MIN_LEVEL2", 20), \
                mock.patch.object(legulegu, "iso_now", lambda: self.NOW.isoformat()):
            result = legulegu.get_sw_parent_map()
        return result, store

    def test_cache_used_within_ttl(self):
        cached = self._cached(10)
        result, store = self._run(cached, lambda *a, **k: self.fail("10 天内不应联网"))
        self.assertIs(result, cached)
        self.assertEqual(store, {})

    def test_refetch_after_ttl(self):
        cached = self._cached(31)
        calls = []

        def http(url, timeout=None, headers=None):
            calls.append((url, headers.get("Referer")))
            return HTML.encode("utf-8"), {}

        result, store = self._run(cached, http)
        self.assertEqual(calls, [(legulegu.SW_OVERVIEW_URL, "https://legulegu.com/")])
        self.assertEqual(len(result["level1"]), 31)
        self.assertEqual(result["level2"]["801092"]["parent"], "801880")
        self.assertEqual(result["fetchedAt"], self.NOW.isoformat())
        self.assertIn(legulegu.CACHE_KEY_SW, store)

    def test_stale_cache_used_with_warning_when_fetch_fails(self):
        cached = self._cached(45)

        def http(url, timeout=None, headers=None):
            raise PanelError("HTTP 503")

        with self.assertLogs("etl.legulegu", level="WARNING") as cm:
            result, store = self._run(cached, http)
        self.assertIs(result, cached)
        self.assertEqual(store, {})
        self.assertIn("45 天前", cm.output[0])

    def test_no_cache_and_fetch_fails_raises(self):
        def http(url, timeout=None, headers=None):
            raise PanelError("HTTP 503")

        with self.assertRaises(PanelError):
            self._run(None, http)

    def test_too_few_level2_from_live_page_falls_back_or_raises(self):
        def http(url, timeout=None, headers=None):
            return HTML.encode("utf-8"), {}

        with mock.patch.object(common, "now_sh", lambda: self.NOW), \
                mock.patch.object(legulegu, "cache_load", lambda sub, key: None), \
                mock.patch.object(legulegu, "http_get", http):
            with self.assertRaises(PanelError):          # 夹具只有 28 个二级 < MIN_LEVEL2(100)
                legulegu.get_sw_parent_map()


if __name__ == "__main__":
    unittest.main()
