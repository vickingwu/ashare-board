#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 web/ 与 data/ 拼成 GitHub Pages 的发布目录。

用法：python scripts/build_site.py [--out 目录]      默认输出到仓库根下的 _site/（已存在则先整个清掉再生成）

产物布局（与 serve.py 的路由一致，页面用相对路径 data/*.json 取数，所以 data/ 必须和 index.html 同级）：
    _site/index.html  css/  js/  vendor/      <- web/ 原样复制
    _site/data/*.json                         <- data/ 原样复制
    _site/.nojekyll                           <- 空文件，关闭 Pages 的 Jekyll 处理
只用标准库，Python 3.9+。
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"


def build(out: Path) -> "tuple[int, int]":
    out = out.resolve()
    for protected in (ROOT, WEB_DIR, DATA_DIR):
        if out == protected.resolve() or protected.resolve().is_relative_to(out):
            raise SystemExit(f"输出目录不能是仓库根 / web / data 或它们的上级：{out}")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out)
    shutil.copytree(DATA_DIR, out / "data")
    (out / ".nojekyll").write_bytes(b"")
    files = [p for p in out.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 GitHub Pages 发布目录（web/ + data/）")
    ap.add_argument("--out", default=str(ROOT / "_site"), help="输出目录，默认 <仓库根>/_site")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if not (DATA_DIR / "manifest.json").is_file():
        print("提示：data/manifest.json 不存在，页面会显示「无法读取 data/manifest.json」", file=sys.stderr)
    n, size = build(out)
    print(f"已生成 {out.resolve()}：{n} 个文件，共 {size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
