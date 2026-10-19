# A股看板 —— macOS 常用命令（需要 python3 ≥ 3.9；Windows 请直接用 .venv\Scripts\python.exe 运行同名脚本）
.PHONY: setup update update-only test serve clean-cache clean-cache-quotes

PY := .venv/bin/python

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

update:
	$(PY) etl/build_data.py

# 只刷新一个面板：make update-only P=coverage
update-only:
	$(PY) etl/build_data.py --only $(P)

test:
	$(PY) -m unittest discover -s tests -v

serve:
	python3 serve.py 8000

# 清掉全部缓存：下次 make update 会重新下载长历史（中债登按年抓，约 3–5 分钟）
clean-cache:
	rm -rf cache/* logs/*.log

# 只清当日行情类缓存（成分名单、腾讯总市值）：白天跑过、想换成收盘市值时用，不碰长历史
clean-cache-quotes:
	rm -rf cache/tencent cache/csindex
