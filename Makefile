# A股看板 —— macOS 常用命令（需要 python3 ≥ 3.9；Windows 请直接用 .venv\Scripts\python.exe 运行同名脚本）
.PHONY: setup update update-only test serve clean-cache clean-cache-quotes clean-cache-sws

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

# 清掉全部缓存：下次 make update 会重新下载长历史（申万行业全史约 13 分钟 + 中债登按年抓约 4 分钟，整体约 20 分钟）
clean-cache:
	rm -rf cache/* logs/*.log

# 只清当日行情类缓存（成分名单、腾讯总市值）：白天跑过、想换成收盘市值时用，不碰长历史
clean-cache-quotes:
	rm -rf cache/tencent cache/csindex

# 只清申万相关缓存（行业历史、申万Ａ指、二级→一级父级映射）：下次 make update 重抓申万全史，约 13 分钟（每页 5–25 秒）；不碰中证 / 中债登 / 腾讯
clean-cache-sws:
	rm -rf cache/sws cache/legulegu/sw_overview.json
