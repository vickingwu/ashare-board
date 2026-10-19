# -*- coding: utf-8 -*-
"""chinabond 的离线测试：HTML 表解析（只留国债曲线、日期升序）、无表报错、数值范围与行数校验。"""
import unittest
from pathlib import Path

from etl.common import PanelError
from etl.sources import chinabond

FX = Path(__file__).resolve().parent / "fixtures"


class ChinabondTest(unittest.TestCase):
    def test_parse_fixture(self):
        rows = chinabond.parse_history_html((FX / "chinabond_2024_trim.html").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 15)                       # 45 行里只有 15 行是国债曲线
        dates = [d for d, _ in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(rows[-1], ("2024-12-31", 1.6752))
        self.assertEqual(rows[0][0], "2024-12-11")
        self.assertTrue(all(1.5 < y < 2.0 for _, y in rows))

    def test_no_table_raises(self):
        with self.assertRaises(PanelError):
            chinabond.parse_history_html("<html><body><table><tr><td>a</td></tr></table></body></html>")
        with self.assertRaises(PanelError):
            chinabond.parse_history_html("<html><body>系统维护</body></html>")

    def test_header_only_table_gives_empty(self):
        html = ("<table><tr><td>曲线名称</td><td>日期</td><td>3月</td><td>6月</td><td>1年</td><td>3年</td>"
                "<td>5年</td><td>7年</td><td>10<!-- 年 -->年</td><td>30年</td></tr></table>")
        self.assertEqual(chinabond.parse_history_html(html), [])

    def test_validate_year(self):
        good = [(f"2024-01-{i:02d}", 2.5) for i in range(1, 29)] * 8   # 224 行
        chinabond.validate_year(2024, good, 2026)
        with self.assertRaises(PanelError):
            chinabond.validate_year(2024, [("2024-01-02", 12.0)] + good, 2026)      # 收益率超范围
        with self.assertRaises(PanelError):
            chinabond.validate_year(2024, good[:50], 2026)                          # 往年行数太少
        chinabond.validate_year(2026, [(d.replace("2024", "2026"), y) for d, y in good[:50]], 2026)  # 当年允许不满
        with self.assertRaises(PanelError):
            chinabond.validate_year(2024, [("2023-12-29", 2.5)] + good, 2026)       # 混入别的年份


if __name__ == "__main__":
    unittest.main()
