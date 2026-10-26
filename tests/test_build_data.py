# -*- coding: utf-8 -*-
"""build_data 编排器的离线测试：第二轮（网络类失败的面板暂停后再试一次）、无变化不改写文件 / manifest、退出码。

用假的面板模块（types.SimpleNamespace，带 PANEL_ID / TITLE / build）与临时 data 目录，不联网、不真睡。
"""
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from etl import build_data
from etl.common import PanelError


def _panel(pid, build, static=False, compact=False):
    mod = types.SimpleNamespace(PANEL_ID=pid, TITLE=f"面板{pid}", build=build)
    if static:
        mod.STATIC = True
    if compact:
        mod.COMPACT_JSON = True
    return mod


def _ok(pid, as_of="2026-10-23", value=1):
    return lambda: {"asOf": as_of, "sources": ["s"], "notes": ["n"], "data": {"pid": pid, "v": value}}


class BuildDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.slept = []
        self.resets = 0
        self.default_pause = build_data.SECOND_PASS_PAUSE
        self.iso = iter(f"2026-10-2{i // 10}T1{i % 10}:00:00+08:00" for i in range(1, 60))
        self.reset_patch = mock.patch.object(build_data, "reset_for_second_pass",
                                             lambda: setattr(self, "resets", self.resets + 1))
        self.patches = [
            mock.patch.object(build_data, "DATA_DIR", self.data_dir),
            mock.patch.object(build_data, "SECOND_PASS_PAUSE", 7.0),
            mock.patch.object(build_data.time, "sleep", lambda s: self.slept.append(s)),
            self.reset_patch,
            mock.patch.object(build_data, "iso_now", lambda: next(self.iso)),
            mock.patch.dict(os.environ, {"ASHARE_FORCE_FAIL": ""}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _run(self, panels, only=None):
        argv = ["--only", only] if only else []
        with mock.patch.object(build_data, "discover_panels", lambda: panels), \
                mock.patch("builtins.print", lambda *a, **k: None):
            return build_data.main(argv)

    def _manifest(self):
        return json.loads((self.data_dir / "manifest.json").read_text("utf-8"))

    def test_network_failure_gets_second_pass_after_pause(self):
        calls = {"erp": 0}

        def flaky():
            calls["erp"] += 1
            if calls["erp"] == 1:
                raise PanelError("重试 5 次仍失败 (ConnectionError: ('Connection aborted.', "
                                 "ConnectionResetError(104, 'Connection reset by peer'))) https://www.csindex.com.cn/x")
            return _ok("erp")()

        panels = [_panel("coverage", _ok("coverage")), _panel("erp", flaky)]
        with self.assertLogs("etl.build", level="WARNING") as cm:
            rc = self._run(panels)
        self.assertEqual(rc, 0)
        self.assertEqual(calls["erp"], 2)                                           # 第二轮重试了一次
        self.assertEqual(self.slept, [7.0])                                         # 暂停 SECOND_PASS_PAUSE
        self.assertEqual(self.resets, 1)                                            # 清掉进程内的失败记忆
        self.assertTrue(any("第二轮" in m and "erp" in m for m in cm.output), cm.output)
        m = self._manifest()
        self.assertTrue(m["panels"]["erp"]["ok"])
        self.assertIsNone(m["panels"]["erp"]["error"])
        self.assertTrue((self.data_dir / "erp.json").is_file())

    def test_non_network_failure_is_not_retried(self):
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise PanelError("申万一级行业应为 31 个，拿到 30 个")

        rc = self._run([_panel("coverage", _ok("coverage")), _panel("swheat", broken)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self.slept, [])
        self.assertEqual(self.resets, 0)
        self.assertFalse(self._manifest()["panels"]["swheat"]["ok"])

    def test_second_pass_failure_keeps_error_and_old_json(self):
        (self.data_dir / "widebase.json").write_text(json.dumps({"panel": "widebase", "generated": "2026-10-22T20:00:00+08:00",
                                                                  "asOf": "2026-10-22", "data": {}}), "utf-8")
        calls = {"n": 0}

        def always_reset():
            calls["n"] += 1
            raise PanelError("HTTP 403 https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000510")

        with self.assertLogs("etl.build", level="WARNING"):
            rc = self._run([_panel("widebase", always_reset)])
        self.assertEqual(rc, 1)                                                     # 唯一面板失败 → 1
        self.assertEqual(calls["n"], 2)
        e = self._manifest()["panels"]["widebase"]
        self.assertEqual((e["ok"], e["asOf"], e["generated"]), (False, "2026-10-22", "2026-10-22T20:00:00+08:00"))
        self.assertIn("HTTP 403", e["error"])

    def test_unchanged_output_is_not_rewritten_and_manifest_untouched(self):
        panels = [_panel("coverage", _ok("coverage")), _panel("loss", _ok("loss", as_of="2026-10-24"), static=True)]
        self.assertEqual(self._run(panels), 0)
        cov = self.data_dir / "coverage.json"
        man = self.data_dir / "manifest.json"
        first = cov.read_bytes()
        first_man = man.read_bytes()
        gen1 = json.loads(first)["generated"]
        with self.assertLogs("etl.build", level="INFO") as cm:
            self.assertEqual(self._run(panels), 0)
        self.assertEqual(cov.read_bytes(), first)                                    # 字节不变，generated 沿用
        self.assertEqual(man.read_bytes(), first_man)                                # manifest 也不改写
        self.assertTrue(any("无差别" in m for m in cm.output), cm.output)
        self.assertTrue(any("manifest 无变化" in m for m in cm.output), cm.output)
        self.assertEqual(self._manifest()["panels"]["coverage"]["generated"], gen1)
        # 数据变了 → 改写文件、manifest 更新 generated
        panels2 = [_panel("coverage", _ok("coverage", value=2)), panels[1]]
        self.assertEqual(self._run(panels2), 0)
        self.assertNotEqual(cov.read_bytes(), first)
        self.assertNotEqual(man.read_bytes(), first_man)
        self.assertGreater(self._manifest()["panels"]["coverage"]["generated"], gen1)

    def test_helpers(self):
        prev = {"panel": "x", "generated": "2026-10-23T20:00:00+08:00", "asOf": "2026-10-23", "data": {"a": [1, 2]}}
        same = dict(prev, generated="2026-10-24T20:00:00+08:00")
        diff = dict(same, data={"a": [1, 3]})
        self.assertTrue(build_data.unchanged_except_generated(prev, same))
        self.assertFalse(build_data.unchanged_except_generated(prev, diff))
        self.assertFalse(build_data.unchanged_except_generated(None, same))
        self.assertFalse(build_data.unchanged_except_generated({"panel": "x"}, same))   # 旧文件没有 generated
        e = {"ok": True, "asOf": "2026-10-23", "generated": "g", "error": None, "durationSec": 1.0, "static": False}
        self.assertFalse(build_data.manifest_entries_changed({"a": e}, {"a": dict(e, durationSec=9.9)}))
        self.assertTrue(build_data.manifest_entries_changed({"a": e}, {"a": dict(e, asOf="2026-10-24")}))
        self.assertTrue(build_data.manifest_entries_changed({"a": e}, {"a": e, "b": e}))
        self.assertTrue(build_data.manifest_entries_changed({}, {"a": e}))
        self.assertEqual(self.default_pause, 60.0)                                        # 真实的第二轮暂停
        self.assertEqual(build_data.MANIFEST_COMPARE_KEYS, ("ok", "asOf", "generated", "error", "static"))

    def test_reset_for_second_pass_clears_market_memo_and_csindex_circuit(self):
        from etl import market_state
        from etl.sources import csindex_hist
        self.reset_patch.stop()
        try:
            market_state._UPDATE_RESULT = (None, PanelError("x"))
            csindex_hist._circuit_open_until = 1e12
            build_data.reset_for_second_pass()
            self.assertIsNone(market_state._UPDATE_RESULT)
            self.assertIsNone(csindex_hist._circuit_open_until)
        finally:
            self.reset_patch.start()


if __name__ == "__main__":
    unittest.main()
