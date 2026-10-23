# A股看板 —— macOS 常用命令（需要 python3 ≥ 3.11，见 setup；Windows 请直接用 .venv\Scripts\python.exe 运行同名脚本）
.PHONY: setup update update-only test serve site clean-cache clean-cache-quotes clean-cache-sws \
        market-cold market-rebuild market-seed market-status clean-cache-market

PY := .venv/bin/python

# python3 ≥ 3.11（3.11–3.14 均可）：baostock 依赖 pandas 3.x / numpy，二进制 wheel 只发到 3.11 及以上；系统自带 3.9 的话先 brew install python
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

# 生成 GitHub Pages 发布目录 _site/（web/ + data/），与 Actions 里 deploy 作业做的事相同
site:
	$(PY) scripts/build_site.py

# 清掉全部缓存：下次 make update 会重新下载长历史（申万行业全史约 13 分钟 + 中债登按年抓约 4 分钟，整体约 20 分钟）。
# 全市场日线库 cache/market/ 也会被删掉，但它下次从 state/market_seed.zip 重建并补齐缺的交易日（1–3 分钟），不用重跑十几小时的冷启动
clean-cache:
	rm -rf cache/* logs/*.log

# 只清当日行情类缓存（成分名单、腾讯总市值）：白天跑过、想换成收盘市值时用，不碰长历史
clean-cache-quotes:
	rm -rf cache/tencent cache/csindex

# 只清申万相关缓存（行业历史、申万Ａ指、二级→一级父级映射）：下次 make update 重抓申万全史，约 13 分钟（每页 5–25 秒）；不碰中证 / 中债登 / 腾讯
clean-cache-sws:
	rm -rf cache/sws cache/legulegu/sw_overview.json

# ---- 全市场日线库 cache/market/（trend / feargreed 两个面板的底层数据，Baostock 沪深 A 股后复权日线）----
# 冷启动：逐只抓 2004 年起的全史，约 5,500 只、本机约 9–12 小时（随时可 Ctrl-C，重跑同一命令从 cache/market/progress.json 续传）。
# 只在重建全史 / 刷新种子时需要：日常 make update 在库不存在时自动从 state/market_seed.zip 初始化并逐日补齐，不会触发冷启动。
# 运行期间不要同时跑 make update（两个 Baostock 会话会互相干扰）
market-cold:
	$(PY) -m etl.market_state --cold --verbose

# 不联网，只从 cache/market/stocks/*.bin 重算每日聚合 cache/market/agg.json（冷启动结束时会自动做一次）
market-rebuild:
	$(PY) -m etl.market_state --rebuild --verbose

# 把本地 full 库导出为种子 state/market_seed.zip（每只最后 252 行 + 聚合 + 日历，≤ 15 MB），然后 git add state/market_seed.zip 提交
market-seed:
	$(PY) -m etl.market_state --export-seed

# 库状态：coldDone / lastDate / 股票数 / 聚合天数 / 待抓数
market-status:
	$(PY) -m etl.market_state --status

# 删掉全市场日线库：下次 make update 从种子重建并补齐缺的交易日（1–3 分钟）；不碰其它缓存
clean-cache-market:
	rm -rf cache/market
