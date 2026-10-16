#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 HTTP 服务：把 web/ 当作站点根目录，把 data/ 挂到 /data/。

用法：python3 serve.py [端口]      默认 8000，只监听 127.0.0.1。
需要它的原因：面板数据靠 fetch 取 /data/*.json，而浏览器不允许 file:// 页面发起 fetch，
所以 index.html 得通过这个服务打开，而不是直接从磁盘打开。

路由：
    /            -> web/index.html
    /css /js /vendor -> web/ 下对应文件
    /data/*.json -> data/ 目录（ETL 输出）
    /api/chart   -> 501（个股出图接口留给 M5）
所有响应带 Cache-Control: no-store，刷新页面即见最新数据。只用标准库，Python 3.9+。
"""
import http.server
import json
import posixpath
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".txt": "text/plain; charset=utf-8",
        "": "application/octet-stream",
    }

    def translate_path(self, path):
        path = urlsplit(path).path
        path = posixpath.normpath(unquote(path))
        parts = [p for p in path.split("/") if p and p not in (".", "..")]
        if parts and parts[0] == "data":
            base, parts = DATA_DIR, parts[1:]
        else:
            base = WEB_DIR
        target = base.joinpath(*parts) if parts else base
        # 规范化后再确认仍在根目录内（防止 .. 穿越）
        try:
            target.resolve().relative_to(base.resolve())
        except ValueError:
            return str(base / "__forbidden__")
        if target.is_dir():
            target = target / "index.html"
        return str(target)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if urlsplit(self.path).path == "/api/chart":
            body = json.dumps({"error": "个股出图接口将在 M5 实现"}, ensure_ascii=False).encode("utf-8")
            self.send_response(501)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if not (DATA_DIR / "manifest.json").is_file():
        print("提示：data/manifest.json 不存在，先运行 make update（或 python etl/build_data.py）", file=sys.stderr)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"已启动：http://127.0.0.1:{port}/ （Ctrl-C 退出）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("已退出")


if __name__ == "__main__":
    main()
