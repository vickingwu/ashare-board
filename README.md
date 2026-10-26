# A股看板

A 股长周期看板：Python 脚本抓公开数据算成静态 JSON，浏览器用 ECharts 渲染。在线版由 GitHub Actions 每个交易日自动刷数据并发布到 GitHub Pages；本地则 `make update` 刷数据、`make serve` 打开。

目前（M0–M4）十五个面板：**宽基指数覆盖范围**（coverage）、**宽基指数全收益净值**（widebase）、**主要宽基指数估值分位**（valuation）、**股债性价比**（erp）、**指数收益来源拆解**（decomp）、**申万一级行业年度涨跌幅**（swheat）、**行业交易热度**（indheat）、**全市场成交金额与换手率**（turnover）、**市场趋势温度**（trend）、**恐贪指数**（feargreed）、**不同事物的增长**（growth）、**风格因子净值**（factors）、**基金指数滚动年化**（fundroll）、**投资者结构**（investors）、**抄底数学题**（loss）。

## 在线版（GitHub Pages）

地址：**https://vickingwu.github.io/ashare-board/** —— 浏览器直接打开，不用 clone、不用装 Python。

- 数据由 GitHub Actions 自动刷新，运行 `.github/workflows/update-and-deploy.yml`，抓数 → 把变化的 `data/*.json` 提交回 `main` → 重新发布页面；缓存命中时每次约 4–8 分钟（其中全市场日线库补 1 个交易日约 1 分钟）。定时安排是**四个时段（北京时间）**：交易日 **20:17** 主运行、**22:47** 重试 / 补齐、每天 **07:53** 兜底、交易日 **11:23** 补漏。之所以不在收盘后马上跑：各数据源的当日数据要到 16:40 之后才陆续发布（2026-10-20 实测：中债登 16:39–16:57、乐咕 17:25–17:45、Baostock 全市场日线 17:30–18:00、中证指数与申万 18:25–18:45，当日即可拿到），20:17 这次拿到当日全部来源的数据；22:47 补主运行时缺失或失败的源（暖缓存，约 3–4 分钟）；07:53 补按 T+1 公布的上交所两融余额和前夜迟到的源（申万偶有缺日）；11:23 只在前一天三次都没跑成时补回前一交易日（16:00 前的运行按规则不取当日盘中数据）。**四个时段是有意冗余的**：GitHub 的定时任务不准时——实测 2026-10-20 → 10-23 的 7 次定时运行每次都延后 1.3–5.2 小时（12:30Z 那条每天约延后 5 小时、23:30Z 那条约 2.2 小时），GitHub 文档也说明负载高时排队的定时运行可能被整次丢掉，所以分钟数避开整点 / 半点的高峰并多排两次，任一时段没跑或跑失败都有后面的时段兜底；定时运行延迟几分钟到几小时是 GitHub 的行为，不是故障。周末 / 节假日的 07:53 是暖缓存空跑：各源没有新数据时不改写任何文件、没有可提交的变化，页面原样重发。
- 全市场日线库（trend / feargreed 两个面板的底层数据）：Actions 上的 `cache/market/` 放在 actions/cache 里；缓存丢失时从仓库里的种子 `state/market_seed.zip` 重建，再用 Baostock 的全市场单日横截面逐日补齐缺的交易日（每个交易日一次查询，runner 上 25–55 秒），每次运行最多补 `ASHARE_MARKET_BUDGET_MIN`（默认 18）分钟。没补完时两个面板照常出图并在「看板说明」里多一句「全市场日线库落后 N 个交易日」，下次运行接着补：trend 的 `asOf` 停在日线库的最后一天；feargreed 以中证全指的日历为轴，有分的子指标不足 4 个的末日综合分为空（页面显示 –，并标出有分的子指标个数）。逐只股票抓全史的冷启动不在 Actions 上跑（runner 上约 28 小时），只在本地做一次、以种子的形式提交。
- 手动刷新：仓库页 **Actions → update-and-deploy → Run workflow**。可选参数 `only` 填面板 id（逗号分隔，如 `erp,valuation`），只刷这几个面板；留空刷全部。可选参数 `skip_days` 填 Baostock 永远没有数据的交易日（逗号分隔 `YYYY-MM-DD`），作为 `ASHARE_MARKET_SKIP_DAYS` 传给 ETL，全市场日线库跳过这些天（见「排错」）。
- 网络类失败会自动再试一轮：全部面板跑完后，因连接重置 / 超时 / HTTP 403 / 429 / 5xx 失败的面板暂停 60 秒再构建一次，之后才写 `manifest.json`；中证指数官网的请求另有 30 → 60 → 120 → 240 秒的长退避与熔断（见「数据源与口径」）。
- 本地刷新也会发布：`make update` 之后 `git push`，push 到 `main` 会触发同一个 workflow 只跑发布这一步。bot 每个交易日都会往 `main` 推 `data: … 自动更新` 提交，所以本地提交之前先 `git pull --rebase`（再 `make update`、再 push），否则 push 会被拒（见「排错」）。
- 出错怎么看：某个面板抓数失败时页面照常发布，该面板标题旁出现黄色徽标「本次更新失败，显示 … 的上一版数据」，原因在 `data/manifest.json` 的 `error` 字段；完整日志在该次运行的 Artifacts 里（名为 `logs`，保留 14 天）。整个运行失败（装依赖、推送出错）则不发布，页面保持上一版。
- 页面顶部「生成于 …」是最近一次**产出有变化**的 ETL 运行时间（北京时间）——产出与现有文件完全相同的运行（周末、同日重跑）不改写文件，所以不更新这个时间；「数据更新至 …」是各数据源的最新日期；工作日估算的交易日差不扣节假日。
- 本地也能生成同样的发布目录：`make site`，输出到 `_site/`。

### 启用步骤（一次性）

1. **仓库可见性**：GitHub Free 只给公开仓库提供 Pages；私有仓库要开 Pages 需要 GitHub Pro。要用免费方案就把仓库改公开：Settings → General → 最底部 Danger Zone → Change visibility → Public。
2. **开启 Pages**：Settings → Pages → Build and deployment → Source 选 **GitHub Actions**。如果第一次运行 workflow 时 `configure-pages` 已经自动开好了，这一步会显示已启用，跳过即可。
3. **跑一次**：Actions → update-and-deploy → Run workflow（分支选 `main`），等 8–12 分钟（第一次要解种子、补齐全市场日线库），运行页右侧 `deploy` 作业会给出页面地址，打开 https://vickingwu.github.io/ashare-board/ 。

之后无需再操作，按上面的四个时段（交易日 20:17 / 22:47 / 11:23，每天 07:53，北京时间）自动刷新。

## 安装与运行（macOS）

需要 `python3`（**3.11–3.14**：baostock 依赖 pandas 3.x，它只给 3.11 及以上出二进制 wheel；系统自带的是 3.9 就 `brew install python`）和 `git`。没有的话 `xcode-select --install`。

```bash
git clone https://github.com/vickingwu/ashare-board.git
cd ashare-board
make setup     # 建 .venv 并安装 requests / xlrd / tzdata / baostock（连带 pandas、numpy，均为二进制 wheel，不需要编译）
make update    # 抓数据、生成 data/*.json（首次约 20 分钟：申万行业全史约 13 分钟、中债登按年下载约 4 分钟、全市场日线库解种子并补齐 1–3 分钟；之后每次 1–3 分钟，主要是腾讯行情和 Baostock 当日横截面）
make serve     # 启动本地服务，然后打开 http://localhost:8000/
```

仓库里已经带了一份 `data/*.json`，clone 下来直接 `make serve` 就能看，`make update` 只是把数据刷到最新。

`web/index.html` 不要直接在浏览器里打开：面板数据是页面加载后用 `fetch` 向 `/data/*.json` 要来的，而浏览器不允许 `file://` 页面发起 `fetch`，结果就是每个面板都报「数据加载失败」。`make serve` 背后的 `serve.py` 用标准库起一个只监听 127.0.0.1 的 HTTP 服务，把 `web/` 和 `data/` 挂在同一个端口下，页面才取得到数据。

## 手工更新流程

- 交易日 **16:30 之后**运行 `make update`，拿到的是当日收盘后的总市值（前提：当天还没跑过，见下面「当日缓存」）。
- 盘中运行也可以，会得到盘中快照，面板「看板说明」里会标注抓取时间。
- 任一面板失败不影响其它面板：失败面板保留上一版 JSON，页面顶部该面板会出现黄色徽标「本次更新失败，显示 … 的上一版数据」，`data/manifest.json` 里记录错误原因。
- 只刷一个面板：`make update-only P=coverage`。
- 当日缓存（成分名单、总市值）：按日期缓存在 `cache/csindex/`、`cache/tencent/`，同一天重跑不再联网。**这也意味着白天跑过一次之后，当天再跑拿到的仍是那一次的盘中快照**；想换成收盘市值，先 `make clean-cache-quotes` 清掉这两类当日缓存，再 `make update`（不要用 `make clean-cache`，那会把下面的长历史缓存也删掉，再跑一次要 3–5 分钟）。
- 长历史缓存（指数日行情、指数 PE、国债收益率、申万行业历史 `cache/sws/`、上交所两融 `cache/sse/`）：以「当天 16:00」为界判断能否复用——16:00 之前抓的缓存只在 16:00 之前复用，16:00 之后抓的当天一直复用。所以同一天上午跑一次、收盘后再跑一次，各联网一次；其余重跑都不联网。往年的国债收益率文件一旦存在永不重抓。申万的行业收盘与Ａ指数据在当日 18:25–18:45 发布，16:00–18:30 之间重跑拿到的仍是前一交易日；申万偶有缺日（如 2026-10-15），这两种情况下面板「数据截至」比当天早一个交易日是正常的。
- 二级→一级行业的父级映射（`cache/legulegu/sw_overview.json`）按 30 天复用，到期才重新抓乐咕的行业总览页；抓不到就沿用旧映射并在日志里 WARNING。申万行业分类一年内很少变动，30 天够用；想立刻刷新就删掉这个文件。
- 只重抓申万相关缓存：`make clean-cache-sws`（约 13 分钟），不碰中证 / 中债登 / 腾讯的缓存。
- 月度来源按月缓存（growth 面板）：东方财富数据中心的 M2 / 名义 GDP / 70 城房价在每月 **20 日**之后的首次运行刷新一次（`cache/eastmoney/{m2,gdp,house}.json` 里的 `fetchMonth` 记着抓的是哪个月），上海黄金交易所 Au99.99 日线在每月 **1 日**之后的首次运行补齐上月（`cache/sge/au9999.json`）；其余运行这几个源 0 次请求。想立刻刷新就删掉对应缓存文件。金价 2006-01 → 2016-11 的静态种子 `state/gold_seed.json` 入库，运行时不抓；只有重建它时才 `make gold-seed`（世界银行 1 次 + 中国货币网 19 次 + 上金所 1 次请求，约 25 秒）。上交所每日概况（investors 面板）按日期缓存 `cache/sse/overview_YYYYMMDD.json`，每次运行最多 1 次请求。
- 产出没有变化就不改写文件：面板的新产出除 `generated` 外与现有 `data/<id>.json` 完全相同时沿用旧文件（含旧的 `generated`），`manifest.json` 也只在任一面板条目变化时才写。所以同一天重跑、周末重跑通常 `git status` 干净。
- 全市场日线库 `cache/market/`（trend / feargreed 的底层数据，Baostock 沪深 A 股后复权日线）：`make update` 发现目录不存在时自动从 `state/market_seed.zip` 解出（约 5,500 只股票各最近 252 个交易日 + 每日聚合 + 交易日历，10–20 秒），再用 Baostock 的全市场单日横截面把缺的交易日逐日补上（本地每个交易日 25–35 秒；一次最多补 `ASHARE_MARKET_BUDGET_MIN` 分钟，默认 18，想一次补完很长的空档就 `ASHARE_MARKET_BUDGET_MIN=60 make update`）。没补完时两个面板照常出图，`asOf` 停在库的最后一天，看板说明里提示「全市场日线库落后 N 个交易日」，下次运行继续。`make market-status` 看库状态（`coldDone` / `lastDate` / 股票数 / 聚合天数 / 待抓数）。
- Baostock 在交易日 17:30–18:00 左右发布当日全市场数据：之前运行时当日没有数据，库和两个面板的「数据截至」停在前一交易日；16:00 之前运行则按规则不取当日。同一天再跑一次只在库落后时才连接 Baostock。某个过去的交易日 Baostock 一直返回 0 行时（确认它永远缺这一天）才跳过：`.venv/bin/python -m etl.market_state --skip-day YYYY-MM-DD`，或 `ASHARE_MARKET_SKIP_DAYS=YYYY-MM-DD make update`（多日用逗号分隔，Actions 上用 Run workflow 的 `skip_days` 输入传同一个变量），跳过的日子记在库清单的 `skippedDays` 里，见「排错」。
- 只有重建全史或刷新种子时才需要 `make market-cold`：**前提是 `cache/market/` 不存在或是 full 形态**——从种子解出的库是 tail 形态（每只只有最近 252 行），在它上面冷启动会报错「当前库是种子 tail 形态 … 先 make clean-cache-market」，`make market-seed` 同样拒绝从 tail 库导出。逐只抓 2004-01-01 起的全史，约 5,500 只、本机约 9–12 小时，可随时中断，重跑同一命令从 `cache/market/progress.json` 续传；期间不要同时 `make update`（会开第二个 Baostock 会话）。结束时它自动用最近几天的横截面校验并补齐早抓的股票、重算聚合、置 `coldDone`；校验窗口默认是最后日期往前 10 个日历日（`ASHARE_MARKET_PATCH_DAYS`），**冷启动跨度超过 10 天时把它调大**（如 `ASHARE_MARKET_PATCH_DAYS=20 make market-cold`），否则更早抓的股票在窗口之外的那些天不会被补上，日志会 WARNING「N 只股票最后日期早于校验窗口」。然后 `make market-seed` 导出 `state/market_seed.zip`（≤ 15 MB）并 `git add state/market_seed.zip` 提交，Actions 与新 clone 以它为起点。当前入库的种子导出于 689xxx（科创板 CDR）涨跌停阈值修正之前，其 `agg.json` 里 2020-11 的几个 `limitUp` 计数比重算值略高（页面不展示涨跌停计数，`make market-rebuild` 后本地已是修正值），下次导出种子时自然刷新。
- `make clean-cache-market` 删掉库；下次 `make update` 从种子重建并补齐缺的交易日（1–3 分钟），不会触发冷启动。`make market-rebuild` 不联网，只从股票文件重算聚合 `cache/market/agg.json`。
- 写一天是幂等的：`make update` 在写完部分股票文件之后被 Ctrl-C 或异常打断，下次运行会发现这些文件最后一行已经是那一天，复用它们（日志「复用 N 行」）而不是报「追加日期 … 不大于已有最后一行」。
- 缓存只是加速手段，删掉任何一个文件都不影响正确性，下次构建会重新下载（全市场日线库从种子重建）。

## 目录结构

```
ashare-board/
├── etl/
│   ├── build_data.py        # 编排器：发现面板 → 逐个 build → 网络类失败的面板 60 秒后再试一轮 → 写有变化的 data/*.json + manifest.json
│   ├── common.py            # 路径/时区/日志/礼貌抓取(http_get / http_post，主机间隔 ± 抖动)/原子写 JSON/按日缓存/16:00 缓存新鲜度规则/按月缓存规则/网络类错误判定
│   ├── compute.py           # 纯数值函数：月末抽样、分位、年化、归一、收益拆解、自然年涨跌幅、排名、滚动均值、EMA / MACD / RSI / 已实现波动率 / 滚动分位、窗口年化 / 滚动年化 / 累计值转当季 / 链式定基 / 月均（无 I/O，离线单测）
│   ├── market_state.py      # 全市场日线库 cache/market/：每股二进制文件、清单、交易日历、每日聚合；冷启动（可续传）、按日横截面增量、重算聚合、种子导出 / 导入、CLI
│   ├── certs/               # 申万服务器证书链缺的 DigiCert 中间证书（公开 CA 证书，拼进 cache/ca_bundle.pem）
│   ├── sources/
│   │   ├── csindex.py       # 中证指数官网成分股 .xls 下载与解析
│   │   ├── csindex_hist.py  # 中证指数官网 指数日行情（价格 / 全收益收盘、滚动 PE），全历史增量缓存
│   │   ├── legulegu.py      # 乐咕乐股 指数月度 PE（整体法），md5 日期令牌 + CSRF 两步请求；申万行业总览页 → 二级→一级父级映射
│   │   ├── sws.py           # 申万宏源研究 指数列表 / 日线 / 指数分析接口，一级·二级·申万Ａ指 全历史增量缓存；SERIES_START（煤炭 801950 起点）
│   │   ├── chinabond.py     # 中债登 国债收益率曲线 10 年期，按年抓 HTML 表格
│   │   ├── pe_select.py     # 每个指数选一个 PE 来源（乐咕优先，否则中证 peg），供三个面板共用
│   │   ├── tencent.py       # 腾讯行情批量接口（总市值）与字段位置校验
│   │   ├── baostock_src.py  # Baostock 单会话（顺序查询、失败重登）、交易日历 / 证券资料 / 个股后复权日线 / 全市场单日横截面的取数与纯解析
│   │   ├── sse_margin.py    # 上交所 融资融券汇总（沪市融资余额日序列，2010-03-31 起），分页取全史 + 增量缓存 cache/sse/margin.json
│   │   ├── sse_overview.py  # 上交所 每日概况（沪市 A 股流通市值 / 成交额 / 上市公司数，按日期缓存 cache/sse/overview_*.json）
│   │   ├── eastmoney_macro.py # 东方财富数据中心 宏观报表：M2、名义 GDP 累计值、70 城二手住宅环比（统计局数据的转载，国家统计局接口对云 IP 返回 403 时的替代来源；按月缓存）
│   │   ├── sge_gold.py      # 上海黄金交易所 Au99.99 日线（POST Dailyhq，滚动 10 年窗口，按月刷新，月均价）
│   │   └── gold_history.py  # 金价静态种子 state/gold_seed.json 的构建与读取：世界银行月均美元金价 × 中国货币网 USD/CNY 中间价月均 ÷ 31.1034768，按与上金所重叠期的几何均值 k 拼接
│   └── panels/
│       ├── coverage.py      # 面板：宽基指数覆盖范围
│       ├── widebase.py      # 面板：宽基指数全收益净值
│       ├── valuation.py     # 面板：主要宽基指数估值分位
│       ├── erp.py           # 面板：股债性价比
│       ├── decomp.py        # 面板：指数收益来源拆解
│       ├── swheat.py        # 面板：申万一级行业年度涨跌幅
│       ├── indheat.py       # 面板：行业交易热度（一级 + 二级收盘序列，紧凑 JSON，指标在浏览器里算）
│       ├── turnover.py      # 面板：全市场成交金额与换手率
│       ├── trend.py         # 面板：市场趋势温度（站上 MA250 / MA50 的股票比例 + 申万一级行业 MACD 为正的比例，紧凑 JSON）
│       ├── feargreed.py     # 面板：恐贪指数（六个子指标滚动分位等权平均，紧凑 JSON）
│       ├── growth.py        # 面板：不同事物的增长（M2 / 名义 GDP / A 股流通市值 / 中证全指与红利低波全收益 / 70 城与一线房价 / 金价，月度轴 2006-01 起）
│       ├── factors.py       # 面板：风格因子净值（红利 / 低波动 / 质量 / 动量 / 价值 / 成长 六条中证指数 + 中证全指基准，2005-01-04 起，紧凑 JSON）
│       ├── fundroll.py      # 面板：基金指数滚动年化（中证偏股型基金指数 1 / 3 / 5 / 7 年滚动年化 + 对照序列，紧凑 JSON）
│       ├── investors.py     # 面板：投资者结构（沪市两融 + 上交所每日概况 + 申万Ａ指 8 张卡片与两融日序列，紧凑 JSON）
│       └── loss.py          # 面板：抄底数学题（纯算术，STATIC）
├── web/
│   ├── index.html           # 页面骨架（不含任何面板标记，面板由 JS 注册生成）
│   ├── css/style.css
│   ├── js/app.js            # 前端框架：注册表、导航、懒加载、新鲜度条、深链接、导出 PNG、配色表
│   ├── js/lib/charts.js     # 面板共用的图表 / 表格小工具（归一、年化、对数轴、时间轴、区间底色、时间窗按钮、可排序表格、N 日涨幅 / 滚动均值 / RPS、热力图基础配置、下拉框、面包屑）
│   ├── js/panels/*.js       # 每个面板一个模块
│   └── vendor/echarts.min.js
├── data/                    # ETL 输出，入库（clone 下来即可直接看）；indheat / trend / feargreed / factors / fundroll / investors .json 是无缩进的紧凑 JSON（各 ≤ 1 MB）
├── state/market_seed.zip    # 全市场日线库种子（每股最近 252 行 + 聚合 + 日历，≤ 15 MB），入库；Actions 与新 clone 的库由它初始化，只在本地 make market-seed 后手工更新
├── state/gold_seed.json     # 金价静态种子：2006-01 → 2016-11 的月均人民币金价（世界银行 × 中间价 × k），入库；只在 make gold-seed 重建时更新
├── cache/  logs/            # 运行期产物，不入库（cache/ca_bundle.pem 也在这里生成；cache/market/ 是全市场日线库，本地 full 形态约 215 MB）
├── tests/                   # 离线单元测试（unittest），fixtures/ 是各接口的真实响应裁剪件（sws_* 申万接口、legulegu_sw_overview_trim.html 乐咕行业总览、bs_* Baostock、sse_* 上交所两融与每日概况、em_* 东方财富、sge_* 上金所、chinamoney_* / worldbank_* 金价种子）
├── scripts/build_site.py    # 把 web/ + data/ 拼成 GitHub Pages 发布目录 _site/
├── scripts/diagnose_sources.py               # 数据源连通性探测（配 .github/workflows/diagnose-sources.yml 手动运行，看 runner 上各源的可达性与速度；--no-baostock 只测 HTTP 源）
├── .github/workflows/update-and-deploy.yml   # 交易日 20:17 / 22:47 / 11:23 与每天 07:53（北京时间）抓数、提交、发布到 Pages
├── .github/workflows/diagnose-sources.yml    # 手动触发的数据源探测（输入 http_only 跳过 Baostock）
├── serve.py                 # 本地 HTTP 服务（web/ + /data/，只监听 127.0.0.1）
├── Makefile  requirements.txt  NOTICE.md
```

## 数据契约

每个面板一个 `data/<id>.json`，统一外壳（envelope）：

| 字段 | 含义 |
|---|---|
| `panel` | 面板 id，与文件名、`etl/panels/<id>.py`、页面 `#panel-<id>` 一致 |
| `title` | 面板标题 |
| `generated` | 生成时间，ISO 8601，`+08:00` |
| `asOf` | 数据截至日期 `YYYY-MM-DD`（coverage = 行情日期；loss = 生成日） |
| `sources` | 数据来源字符串数组 |
| `notes` | 口径说明数组，页面「看板说明」会追加显示 |
| `data` | 面板私有数据 |

`data/manifest.json`：

| 字段 | 含义 |
|---|---|
| `generated` | 本次运行结束时间 |
| `panels.<id>.ok` | 本次是否成功 |
| `panels.<id>.asOf` / `generated` | 成功时为本次值；失败时沿用上一版文件里的值（可能为 `null`） |
| `panels.<id>.error` | 失败原因，一行字符串 `类型: 消息` |
| `panels.<id>.durationSec` | 耗时 |
| `panels.<id>.static` | 为 `true` 的面板不参与页面顶部「数据更新至」的计算 |

约定：

- 列表类数据用「平行数组」或对象数组，不混用；百分比一律是 0–100 的数（不是 0–1）。
- 所有文件先写 `*.json.tmp` 再原子替换，浏览器不会读到半截文件。
- `etl/build_data.py` 退出码：`0` 至少一个面板成功；`1` 全部失败；`2` 参数错误。

`coverage.json.data` 主要字段：`universe{count,countListed,sizeUnionCount,mvTotal}`、`indexes[]{code,name,count,mvSum,mvShare,top3,notInUniverse}`、`cells[]{k,rankFrom,rankTo,mvMax,mvMin,counts{code:n},uncovered}`、`uncovered{count,mvSum,mvShare,cells[]}`、`overlap{codes,names,matrix}`、`mvLadder[]{code,name,mvShare}`、`boundaries`、`cellSize`、`fieldCheck[]`、`asOfQuote`、`quoteTime`、`asOfConstituents`。

`widebase.json.data`：`dates[]`（全部指数共用的交易日轴，2005-01-04 起）、`series[]{code,trCode,name,kind,start,end,values[]}`（`values` 与 `dates` 等长，起点之前为 `null`；`kind` 为 `tr` 全收益或 `price` 价格）、`summary[]{code,name,kind,start,end,first,last,multiple,years,cagrAll,cagr10y,cagr5y,cagr3y,maxDrawdown}`（年化为 %，历史不够长的窗口为 `null`）。

`valuation.json.data`：`indexes[]{code,name,source,sourceNote,fallback,peNow,peDate,sampleStart,sampleEnd,nMonths,pctAll,pct10y,pct5y,min,max,median,shortSample,monthly{dates[],values[]}}`、`windows{all,10y,5y}`、`shortSampleMonths`。分位为 0–100，窗口样本不足时为 `null`。

`erp.json.data`：平行数组 `dates[]`（月末）、`pe[]`、`ey[]`（= 100 ÷ PE）、`y10[]`、`erp[]`（= ey − y10，百分点）、`ratio[]`（= ey ÷ y10）；`summary{latest{date,pe,ey,y10,y10Date,erp,ratio},erpPct,ratioPct,mean,std,bands{p1,m1,p2,m2},ratioMean,sampleStart,sampleEnd,nMonths}`；`equity{code,name,peSource,peSourceNote}` 说明股票端用的是哪个指数、PE 来自哪里。

`decomp.json.data`：`indexes[]{code,trCode,name,peSource,peSourceNote,startDate,endDate,months,years,priceStart,priceEnd,trStart,trEnd,peStart,peEnd,totalReturn,priceReturn,dividend,earnings,valuation,cagrTotal,cagrPrice,perYear{...},byYear[]{year,from,to,partial,totalReturn,dividend,earnings,valuation,peFrom,peTo}}`、`formula`。每个指数与每个年份都满足 `dividend + earnings + valuation == totalReturn`（构建时断言到 1e-6）。

`swheat.json.data`：`baseDate`（`1999-12-30`）、`years[]`（2000 … 上一个完整自然年）、`ytdYear`、`ytdAsOf`、`nByYear[]`（每年有数据的行业数）、`nYtd`、`industries[]{code,name,firstYear,gapYears[],rets[],ytd,ytdRank,cagr{all,y10,y5,y3},cagrYears{all,from},ranks[]}`（按申万列表顺序；`rets` / `ranks` 与 `years` 等长，`firstYear` 之前和 `gapYears` 里为 `null`；`cagr` 只用完整年度算，`cagrYears.from` 是年化起算年，窗口跨过断档或样本不够时为 `null`）。涨跌幅与年化都是 %。

`indheat.json.data`：`days`（600，体积超预算时自动退到 500）、`dates[]`（最近 `days` 个申万交易日，一级与二级共用）、`level1[]{code,name,closes[],amtShare[]}`（31 个，按代码排序；`amtShare` 为该行业成交额占全部 A 股的 %）、`level2[]{code,name,parent,closes[]}`（`parent` 为一级代码；窗口内缺失收盘超过 5% 的二级不纳入，列在 `dropped[]{code,name,nullShare}`）、`parentSource`、`periods[]`（RPS 可选周期 3/5/10/20/60/120/250）、`columns[]`（可选显示列数）。RPS 与 MA20 站上率不落盘，由浏览器按当前选项现算。此文件以 `COMPACT_JSON = True` 写出：无缩进、单行，约 0.9 MB，构建时断言 ≤ 1 MiB。

`turnover.json.data`：平行数组 `dates[]`（2000-01-04 起）、`amount[]`（成交额 亿元）、`mv[]`（申万Ａ指成分流通市值 亿元，缺失为 `null`）、`turnover[]`（= amount ÷ mv × 100，%，`mv` 为空处为 `null`）、`ma20[]`（成交额 20 日均，前 19 行 `null`）、`swTurnover[]`（申万自己发布的换手率，仅供对照）；`mvKind`、`mvLabel`、`scope`；`gaps[]{from,to,n,nPresent}`（流通市值连续缺失段，目前只有 2011-01-04 → 2012-04-16）；`summary{latest{date,amount,ma20,mv,turnover,swTurnover},pctAll,nDays,nTurnover,start}`。

`trend.json.data`（紧凑 JSON，只有比例与计数，没有任何个股数据）：`daily{dates[],pct250[],pct50[],n[],above250[]}`（日度，从分母首次 ≥ 300 只的交易日起，2005 年 1 月中旬；`pct250` = 收盘站上自身 MA250 的股票数 ÷ `n`，`n` = 当日有成交且已有 ≥ 250 个交易日记录的股票数，分母不足 300 的日子为 `null`；`pct50` 同口径的 MA50 版本）、`monthly{dates[],pctIndustryMacdPos[],pctHist[],nIndustries[],partialLast}`（月末，2000-03 起；`pctIndustryMacdPos` = 申万一级行业里 MACD `DIF > 0` 的占比，`pctHist` = `MACD 柱 > 0` 的占比，`nIndustries` 16 → 27 → 31，`partialLast` 表示最后一个月未结束）、`industryDaily{dates[],pct[],pctHist[]}`（同口径的日度序列，最近 750 个交易日）、`overlay{dates[],csiAll[]}`（中证全指收盘，对齐到 `daily.dates`）、`latest{date,pct250,pct50,n,above250,industryPct,industryPctHist,industryN,industryDate}`、`universe{source,start,nStocksNow,nStocksEver,storeLastDate}`。百分比 0–100，比例 2 位小数（行业 1 位）。

`feargreed.json.data`（紧凑 JSON）：`dates[]`（中证全指交易日，从首个有综合分的日期起，2006 年初）、`composite[]`（0–100，2 位小数，有分的子指标不足 4 个时 `null`）、`subs{vol|turnover|margin|breadth|rsi|strength:{name,unit,raw[],score[],definition}}`（六个子指标：原始值与 0–100 的分位分，各与 `dates` 等长，无值处 `null`）、`overlay{csiAll[]}`、`bands[]{lo,hi,label}`（0–25 极度恐慌 / 25–45 恐慌 / 45–55 中性 / 55–75 贪婪 / 75–100 极度贪婪，左闭右开，100 归入最后一档）、`latest{date,composite,band,subs[]{key,name,unit,raw,score,definition}}`、`window`（500）、`minObs`（250）、`minSubs`（4）、`start`、`nDays`。

`growth.json.data`：`months[]`（`YYYY-MM`，2006-01 → 中证全指最后一个完整月，连续）、`monthEnds[]`（对应的日历月末 `YYYY-MM-DD`，前端用作类目轴）、`start`、`end`、`baseMonth`（八条序列都有值的第一个月，仅供参考）、`series[]{key,name,unit,kind,cadence,source,start,end,values[]}`（与 `months` 等长、起点前与缺失处为 `null`；`key` 依次 `m2` M2 亿元、`gdp` 名义 GDP 四季度滚动求和 亿元（只在季末月有值）、`mv` A 股流通市值 亿元（申万Ａ指口径，月内最后一个交易日；2011-01 → 2012-03 为空且空档前后口径不同）、`csiAllTR` 中证全指全收益、`divLowVolTR` 红利低波全收益、`house70` 70 城二手住宅等权链式指数（2010-12 = 100）、`houseTier1` 一线 4 城同口径、`gold` 黄金人民币价 元/克 月均；`kind` 为 `level` / `price` / `index`，`cadence` 为 `monthly` / `quarterly` / `monthEnd` / `monthlyAvg`）、`summary[]{key,name,start,end,first,last,multiple,years,cagrAll,cagr10y,note}`（`multiple = last ÷ first`，年化为 %）、`dropped[]{key,name,reason}`（人民币存款余额、城镇居民人均可支配收入：没有可用来源）、`houseDropped[]`（环比缺失的城市，当前为空）。原始值落盘，归一到 100 在浏览器里按当前时间窗做。

`factors.json.data`（紧凑 JSON）：`dates[]`（六条因子与两条基准交易日的并集，2005-01-04 起）、`start`、`series[]{key,name,code,kind,universe,values[]}`（`key` 依次 `dividend` H00922 中证红利全收益、`lowvol` 930847 中证800行业中性低波动（价格）、`quality` 931156 中证800质量成长（价格）、`momentum` H20260 沪深300动量全收益、`value` H20356 中证800价值全收益、`growth` H20355 中证800成长全收益；`kind` 为 `tr` / `price`；点位 2 位小数，缺失日为 `null`）、`bench{tr{code,name,values[]},price{code,name,values[]}}`（H00985 中证全指全收益与 000985 中证全指价格，相对强弱按同口径相除）、`summary[]{key,name,code,kind,start,end,first,last,multiple,years,cagrAll,cagr10y,cagr5y,cagr3y,maxDrawdown}`、`lagging[]`（最新日期落后于其它序列的因子 key）。

`fundroll.json.data`（紧凑 JSON）：`dates[]`（中证偏股型基金指数 930950 的交易日，2007-12-28 起）、`windows{"1":252,"3":756,"5":1260,"7":1764}`（窗口长度，交易日）、`rolling{"1":[],"3":[],"5":[],"7":[]}`（滚动年化 %，2 位小数，`((末 ÷ 初) ^ (252 ÷ n) − 1) × 100`，窗口未满为 `null`）、`series[]{key,name,code,kind,extra,values[]}`（`equityFund` 930950 偏股基金、`bondPlusFund` 930897 非纯债债基（可持股 / 转债的债券型基金，近似偏债）、`csi300TR` H00300 沪深300全收益、`a500` 000510 中证A500（价格）；`extra: true` 的 `stockFund` H11021 / `mixedFund` H11022 / `bondFund` H11023 默认隐藏；`kind` 为 `nav` / `tr` / `price`）、`startOptions[]`（归一起点选项）、`latest{date,close,r1,r3,r5,r7}`、`stats{"1"|"3"|"5"|"7":{min,max,latest,pctPositive,n}}`。

`investors.json.data`（紧凑 JSON）：`cards[]{key,label,value,unit,date,scope,note}`（顺序 `marginTotal` 沪市融资融券余额、`marginFin` 沪市融资余额、`marginRatio` 两融余额 ÷ 沪市 A 股流通市值 %、`sseNego` 沪市 A 股流通市值、`sseTurnover` 沪市 A 股当日换手率 %、`aMv` 全 A 流通市值（申万口径）、`aAmount` 全 A 成交额、`aTurnover20` 20 日均成交额 ÷ 流通市值 %；金额 亿元；`scope` 标明 沪市 / 沪市 A 股 / 全 A；当日概况缺失时 `marginRatio.value` 为 `null` 并在 `note` 里说明）、`chart{dates[],total[],rzye[],csiAll[]}`（沪市融资融券余额与融资余额日序列 2010-03-31 起、中证全指收盘对齐，缺失为 `null`）、`latest{date,total,rzye,ratioPct}`、`dropped[]{key,name,reason}`（新增投资者、公募基金规模、北向持股、深市两融：没有机器可读或可区间查询的来源）。

全市场日线库 `cache/market/`（不入库；本地、种子、Actions 三处格式相同）：`manifest.json`（`version`、`kind` full / tail、`start` 2004-01-01、`coldDone`、`lastDate`、`nStocks`、`skippedDays[]`、`codes{key:{name,ipo,out,status,first,last,n,lastClose}}`，`key` 形如 `sh600519`）、`agg.json`（每个交易日的计数：`dates[]`、`nTraded`、`n250`、`above250`、`n50`、`above50`、`adv`、`dec`、`flat`、`limitUp`、`limitDown`、`nLimitEligible`）、`calendar.json`（Baostock 交易日历，含未来约 60 天）、`progress.json`（只在冷启动期间存在）、`stocks/<key>.bin`（12 字节头 `ASHM` + 版本 + 行数，随后四段等长数组：`int32` 日期 `yyyymmdd`、`float32` 后复权收盘、`float32` 涨跌幅 %、`uint8` 标志位 bit0 = ST，每行 13 字节；只存有成交的日子）。`state/market_seed.zip` = 清单（`kind: "tail"`、`tailRows`、`exportedAt`）+ `agg.json` + `calendar.json` + 每只股票最后 252 行的 `stocks/*.bin`；导入后只能按日追加，不能重算全史聚合（`make market-rebuild` 要求 full 库）。

## 面板清单与路线图

| 里程碑 | 面板 | 状态 | 口径一句话 |
|---|---|---|---|
| M0 | coverage 宽基指数覆盖范围 | ✅ | 全市场按总市值排名切格，数各宽基指数覆盖了哪些格子 |
| M1 | widebase 宽基指数全收益净值 | ✅ | 中证全收益 / 价格指数日线，各自基日起，时间窗起点归一为 100，对数坐标 |
| M1 | valuation 主要宽基指数估值分位 | ✅ | 整体法 PE-TTM 月末序列的含等号分位（全历史 / 近 10 年 / 近 5 年）；乐咕 2005 起、中证 2011 起 |
| M1 | erp 股债性价比 | ✅ | 中证800 盈利收益率（100 ÷ PE）− 中债 10 年期国债收益率，月末，2007-01 起 |
| M1 | decomp 指数收益来源拆解 | ✅ | 全收益 = 股息 + 盈利 + 估值，残差法，月末对齐，全区间 + 分年 |
| M2 | swheat 申万一级行业年度涨跌幅 | ✅ | 申万 2021 版 31 个一级行业的自然年涨跌幅 + 今年以来 + 完整年度年化，价格指数，1999-12-30 基点；各行业起点不同（16 个 2000 年起、11 个 2015 年起、4 个 2022 年起） |
| M2 | indheat 行业交易热度 | ✅ | 一级 / 二级行业 RPS（N 日涨幅在同级里的百分位）与 MA20 站上率热力图，最近 600 个交易日，指标在浏览器内计算，点一级可下钻到二级 |
| M2 | turnover 全市场成交金额与换手率 | ✅ | 申万Ａ指成交额 ÷ 申万Ａ指成分流通市值（申万口径），2000-01 起，附成交额 20 日均与申万自己发布的换手率 |
| M3 | trend 市场趋势温度 | ✅ | 沪深 A 股（Baostock 全市场，后复权）收盘站上自身 250 日均线的比例（分母 = 当日有成交且已有 ≥ 250 个交易日记录的股票），2005-01 起，附 MA50 版本；下图为申万 31 个一级行业 MACD(12,26,9) `DIF > 0` 的占比（月度 2000-03 起 + 近 3 年日度，`柱 > 0` 作对照），叠加中证全指 |
| M3 | feargreed 恐贪指数 | ✅ | 自建：中证全指 20 日年化波动率（反向）、申万Ａ指相对换手（÷ 60 日均）、沪市融资余额 20 日变化率、站上 MA50 比例、中证全指 RSI(14)、5 日涨跌家数强度，各取滚动 500 个交易日的含等号分位（≥ 250 个样本才计分），等权平均（≥ 4 个有分），0 恐慌 — 100 贪婪，2006 年起 |
| M4 | growth 不同事物的增长 | ✅ | 月度轴 2006-01 起：M2、名义 GDP（四季度滚动）、A 股流通市值（申万口径）、中证全指与红利低波全收益、70 城 / 一线二手住宅链式指数、黄金人民币月均价；各自起点归一为 100、对数坐标；宏观三项来自东方财富数据中心（统计局数据的转载），金价两段拼接 |
| M4 | factors 风格因子净值 | ✅ | 红利 H00922 / 低波动 930847 / 质量 931156 / 动量 H20260 / 价值 H20356 / 成长 H20355 六条中证指数自 2005-01-04 归一，第二图为相对中证全指（同口径 全收益 ÷ 全收益、价格 ÷ 价格）的相对强弱 |
| M4 | fundroll 基金指数滚动年化 | ✅ | 中证偏股型基金指数 930950 的 1 / 3 / 5 / 7 年（252 / 756 / 1260 / 1764 个交易日）滚动年化，附偏股 / 非纯债债基 / 沪深300全收益 / 中证A500 自选起点归一对照 |
| M4 | investors 投资者结构 | ✅ | 沪市融资融券余额与融资余额（上交所，T+1）、两融 ÷ 沪市 A 股流通市值、沪市 A 股流通市值与换手（上交所每日概况）、全 A 流通市值 / 成交额 / 20 日均换手（申万口径）八张卡片 + 两融日序列叠加中证全指 |
| M0 | loss 抄底数学题 | ✅ | 纯算术：亏多少要涨多少回本 |
| M5 | 工具：个股四面板走势图 `/api/chart`（matplotlib）、汇总页 | 未实现（`serve.py` 目前对 `/api/chart` 返回 501） | |

## 数据源与口径

- **成分股名单**：中证指数官网公开文件 `https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{指数代码}cons.xls`，旧版 Excel（OLE2），用 `xlrd` 解析。代码列是数字会丢前导零，解析时补回 6 位。名单日期取文件内「日期」列。
- **总市值**：腾讯行情 `https://qt.gtimg.cn/q=sh600519,sz000001,...`，每批 60 只，GBK 文本按 `~` 切分；`[44]` 流通市值、`[45]` 总市值（亿元）、`[30]` 行情时间。字段位置没有官方文档，因此每次构建用 贵州茅台 / 工商银行 / 宁德时代 三只参照股校验量级（工行要求 总市值 > 流通市值，用于区分两列），校验不过整个面板失败而不是出错图。北交所 `920xxx` 代码以 `bj` 前缀请求，字段数 87 但位置一致。
- **格位边界**：图里按**总市值**排名切格，而中证规模指数选样用的是**自由流通市值**并带缓冲区，所以 300/800/1800/3800 边界附近会有少量「越界」格子，是口径差异不是错误。
- **全市场**：以中证全指为全体（剔除 ST 与上市不久的新股），只数略少于交易所上市公司总数。
- **中证A500**：按行业均衡选样，不是规模层，与沪深300 的重叠度见矩阵。
- **交易日估算**：页面顶部「N 个交易日前」只按周一至周五计数，未扣节假日。
- **指数日行情（widebase / valuation / decomp）**：中证指数官网 `https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}&startDate=YYYYMMDD&endDate=YYYYMMDD`，需带 `Referer`，返回 JSON（`code` 是字符串 `"200"`）。不限区间，`startDate=20050101` 到今天一次返回全部历史（沪深300 约 5,290 行、1.8 MB）。每行用 `tradeDate`、`close`、`peg`（滚动市盈率，价格与全收益序列上数值相同）。**第 0 行的日期永远等于请求的 `startDate`**，哪怕那天不是交易日：此时它是一行占位，开高低收与 `peg` 照抄 `startDate` 之后第一个交易日的值（全量请求的第 0 行是 `20050101` 周六、值同 2005-01-04；`startDate=20261005` 国庆假期时第 0 行日期是 10-05、值同 10-08）。所以周末日期的行一律丢弃；第 0 行日期等于 `startDate` 且这天不在缓存里时也丢弃（唯一例外：全量请求且该行收盘价与下一行不同，说明 `startDate` 本身是交易日）。只给已收盘交易日。不存在的代码返回 `data: []` 而不是错误。全收益指数代码以 `H` 开头：`H00300` 沪深300、`H00905` 中证500、`H00852` 中证1000、`H00906` 中证800、`H00985` 中证全指、`H20269` 红利低波。缓存 `cache/csindex_hist/{code}.json` 保存整条历史，之后每次只请求「最后缓存日当天 → 今天」的增量并按日期合并（最后缓存日是已知交易日，接口第 0 行就是它的真实行情，同日新值覆盖旧值）；读缓存时丢掉周末日期的行。早期版本的增量窗口是「最后缓存日 − 15 天」，`startDate` 落在工作日假期时会把占位行合并进缓存，用 `python -m etl.sources.csindex_hist --purge-dates 2026-10-01` 按日期清掉。
- **没有全收益指数的三条**：中证A500（000510）、中证2000（932000）、科创50（000688）在中证官网没有发布全收益版本（`H00510` / `H32000` / `H00688` 返回空），图中用价格指数代替并标「价格」，长期会低估它们的真实回报。中证A500 于 2024 年发布，2005–2024 的点位是官方回溯值。创业板指全收益（399606）腾讯 K 线接口返回空，暂未纳入。
- **指数 PE（valuation / erp / decomp）**，每个指数只用一个来源，不混接：
  - 乐咕乐股（沪深300 2005-04 起、上证50 2005-01 起、中证500 / 中证800 2007-01 起、中证1000 2014-10 起）：两步请求。先 `GET https://legulegu.com/stockdata/sz50-ttm-lyr` 拿页面，页面 `<meta name="_csrf">` 是 CSRF 令牌，cookie 由共享 Session 保存；再 `GET https://legulegu.com/api/stockdata/index-basic-pe?token=<md5(当天日期 'YYYY-MM-DD')>&indexCode=000300.SH`，头部 `Referer` 指向第一步页面、`X-CSRF-TOKEN` 为 meta 值、`X-Requested-With: XMLHttpRequest`；`indexCode` 必须带 `.SH` / `.SZ` 后缀；日期用北京时间。响应里 **`addTtmPe` 才是整体法（市值加权）滚动市盈率，`ttmPe` 是等权口径**（沪深300 等权约 29 vs 整体法约 12），本看板一律用 `addTtmPe`。非会员只返回每月一条（月末交易日）加最新交易日一条，正好够用。乐咕只覆盖 000016 / 000300 / 000009 / 399673 / 000905 / 000010 / 399324 / 399330 / 000852 / 000015 / 000903 / 000906，其它指数返回 `data: []`。缓存 `cache/legulegu/{code}_{YYYYMMDD}.json`（空结果也缓存，当天不再重问）。
  - 中证指数官网日行情的 `peg` 字段（中证全指 2011-09 起、红利低波 2013-12 起、科创50 2020-07 起、中证2000 2023-06 起、中证A500 2024-09 起），日度取月末。乐咕某个指数失败（403 / 页面没 `_csrf` / 空数据）时自动回退到 `peg`，`sourceNote` 里会写明。两个来源算法细节不同，同一天乐咕比中证约低 4–10%，所以绝不在一条序列里混接。
  - ERP 的股票端用中证800（= 沪深300 + 中证500，覆盖两市绝大部分市值，PE 可追溯到 2007-01）；中证800 不可用时退到沪深300，面板说明里会标注。
- **10 年期国债收益率（erp）**：中债登 `https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?startDate=YYYY-01-01&endDate=YYYY-12-31&gjqx=0&qxId=ycqx&locale=cn_ZH`，带 `Referer`。返回的是 HTML 表格（不是 JSON）：第二个 `<table>` 表头 `曲线名称 / 日期 / 3月 / 6月 / 1年 / 3年 / 5年 / 7年 / 10年 / 30年`，三条曲线的行交错排列，只保留 `曲线名称 == 中债国债收益率曲线`，日期降序要反转。一次最多查一年；2005 年无数据，2006 年从 3 月 1 日起。很慢，每年 5–13 秒，超时设 120 秒。缓存 `cache/chinabond/{year}.json`，往年文件存在就不再请求，只有当年会刷新。
- **月末与分位口径**：月末 = 该自然月最后一个有数据的交易日；序列最后一条若不是其所在月的最后一个工作日，这个月不算已完结，只作「最新值」单独显示。分位 = 历史月末值中「小于或等于当前值」的占比（0–100）；近 10 年 / 近 5 年分别要求 120 / 60 个月末样本，不够的留空；月末样本不足 60 个的指数标「样本短」。
- **收益拆解口径**：估值 = PE 期末 ÷ PE 期初 − 1；盈利 = 价格涨幅 − 估值（残差，含成分调整等）；股息 = 全收益涨幅 − 价格涨幅；三者相加恒等于全收益涨幅。分年拆解以每个自然年最后一个月末为节点，年内同样加法拆分，各年之间是复利关系，不能直接相加。
- **申万行业指数与申万Ａ指（swheat / indheat / turnover）**：申万宏源研究官网，域名是 `www.swsresearch.com`（旧域名 `www.swhyresearch.com` 能解析但连不上，不要用）。
  - **证书**：服务器只发叶子证书、不发中间证书 `GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1`，requests 默认会报 `unable to get local issuer certificate`。处理办法是把这张公开的 DigiCert 中间证书放在 `etl/certs/geotrust_g2_tls_cn_2022_ca1.pem`，构建时 `common.ca_bundle_path()` 把 certifi 自带的根证书包和 `etl/certs/*.pem` 拼成 `cache/ca_bundle.pem`，共享 Session 用它校验；**不关闭证书校验**。证书校验失败时直接报 `PanelError`，不重试。
  - **指数列表** `GET /institute-sw/api/index_publish/current/?page=1&page_size=200&indextype=一级行业`，带 `Referer: https://www.swsresearch.com/institute_sw/allIndex/releasedIndex`；返回 `data.results[]{swindexcode,swindexname,…}`。只用它拿 31 个一级行业的权威代码和名称（数量不是 31 直接报错），按日缓存 `cache/sws/list_一级行业.json`。二级行业这里只列 124 个，而分析接口和乐咕都有 **131** 个，所以二级的集合以分析接口为准。
  - **指数分析** `GET /institute-sw/api/index_analysis/index_analysis_report/?page=1&page_size=10000&index_type=<一级行业|二级行业|市场表征>&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&type=DAY&swindexcode=<all|801003>`，带 `Referer: https://www.swsresearch.com/institute_sw/allIndex/analysisIndex`。每行 `swindexcode, swindexname, bargaindate, closeindex, bargainsumrate（成交额占全部 A 股 %）, negotiablessharesum1（流通市值 亿元）, turnoverrate（申万口径换手率 %）, pe, pb, dp …`。**数值全是字符串**（`"4130.80"`），缺失为 `null`；日期形如 `2026-10-16T08:00:00+08:00`，只取前 10 位。`page_size=10000` 有效，一页能放下 31 个行业约 300 个交易日；按日期窗口分段下载（一级 440 天一段、二级 100 天一段），`count` 大于返回行数时再翻页。每次请求 5–25 秒，偶发 HTTP 502，`http_get` 对 5xx 自动退避重试。行业收盘、成交额占比、申万Ａ指流通市值都来自这个接口。
  - **指数日线** `GET /institute-sw/api/index_publish/trend/?swindexcode=801003&period=DAY`：只用来拿申万Ａ指的成交额。无日期参数，每次返回全史（约 1.5 MB、7–10 秒）；`bargainsum` 是成交额（**亿元**），`bargainamount` 是成交量（**亿股**），`closeindex` 收盘。成交额为 0 的基日行丢弃。
  - **口径与坑**：申万的行业与Ａ指数据在当日 **18:25–18:45** 发布（2026-10-20 实测；此前「T+1」的说法不准确，20:17 的自动更新能拿到当日），在此之前运行拿到的是前一交易日，且偶有缺日（如 2026-10-15 整天没有行业数据，日志里会出现「增量区间无新数据」，属正常）；申万Ａ指的流通市值 **2011-01-04 → 2012-04-16 整段为空**（只有 3 天有值），换手率在这段留空、不用别的来源补；申万自己的 `turnoverrate` 用的分母与本看板不同（数值略低），只作对照列；煤炭（801950）这个代码在 2014-02-21 → 2017-01-20 返回的是另一只小指数（流通市值 231–418 亿元、成交额占比 0.1–0.3 %，而 2021-12-13 重新开始时流通市值 3416.68 亿元），中间 2017-01-23 → 2021-12-10 没有记录；`swheat.SERIES_START` 把 2021-12-13 之前的行截掉，年度表从 2022 年起、年化也从 2022 年起算；申万Ａ指 = 沪深两市全部 A 股（不含北交所与 B 股），其成交额比上证 + 深证综指成交额之和低 0.2–0.4%（差额是 B 股）。
  - **缓存** `cache/sws/`：`list_一级行业.json`（按日）、`analysis_l1.json` / `analysis_l2.json` / `analysis_801003.json`（全史，一个文件一条共用 `dates[]`，每个代码一组与之等长的数组，无数据处为 `null`）、`trend_801003.json`（全史，不新鲜就整条重下）。增量更新只请求 `[最后缓存日 − 30 天, 今天]` 一个窗口，按日期合并、新值覆盖旧值。一级从 1999-12-30 回填（约 23 次请求），二级只回填最近 900 天（约 10 次，indheat 只用最近 600 个交易日）。
- **二级→一级行业父级映射（indheat）**：乐咕乐股 申万行业总览页 `GET https://legulegu.com/stockdata/sw-industry-overview`（普通页面，不需要令牌）。`div#level1Items` / `div#level2Items` 下每个条目：`.lg-industries-item-chinese-title` 是代码（`801016.SI`，去掉 `.SI`），`.lg-industries-item-number` 是 `名称(成份数)[上级行业]`，上级按名称对回 31 个一级。申万接口本身不给父级，代码前缀也推不出来（如 801092 → 汽车、801101 → 计算机、801191 → 非银金融）。缓存 `cache/legulegu/sw_overview.json` 复用 30 天；页面抓不到时沿用旧映射并 WARNING，没有旧映射时 indheat 失败、其它面板不受影响。
- **全市场日线（trend / feargreed）**：Baostock（`baostock==0.9.4`，纯 socket 协议，服务器 `public-api.baostock.com:10030`，不是 HTTP）。
  - **会话**：`bs.login()` 后顺序查询，绝不并发；库自带的 socket 没有超时，登录前先 `socket.setdefaulttimeout(120)`；查询出错或连接断掉就重新登录再试（最多 3 次，间隔 2 / 4 / 8 秒）；`baostock` 在函数内按需 `import`，其它面板与单测不用付 pandas 的导入开销。结果集只用 `rs.next()` / `rs.get_row_data()` 逐行读，不用 `rs.get_data()`（它调用 pandas 2 已删除的 `DataFrame.append`）。登录成功时库会向 stdout 打印一行，构建时把它吞掉。
  - **个股日线** `query_history_k_data_plus(code, "date,close,preclose,pctChg,tradestatus,isST", start, end, "d", adjustflag)`：`adjustflag` 1 = 后复权、2 = 前复权、3 = 不复权；`pctChg` 是交易所口径的涨跌幅（原始收盘 ÷ 除权除息后的昨收 − 1），三种复权方式下数值相同，所以 `后复权收盘(t) = 后复权收盘(t−1) × (1 + pctChg/100)` 恰好就是 Baostock 自己的后复权构造（2026 年 190 个交易日逐日验证一致，含 2026-06-26 茅台除息日）；一只股票上市首日的后复权收盘等于原始收盘（宁德时代 2018-06-11 = 36.20）。停牌日也有行：`tradestatus` 为 `0`、`close == preclose`、`pctChg` 为 0 或空——这些行一律丢弃，库里只存有成交的日子。每页 2000 行自动翻页；只在冷启动里逐只调用。
  - **全市场单日横截面** `query_daily_history_k_AStock(date)`：一次返回当天全部沪深 A 股（2026-10-20 为 5,224 行；字段 `date, code, open, high, low, close, preclose, volume, amount, adjustflag, turn, tradestatus, pctChg, peTTM, pbMRQ, psTTM, pcfNcfTTM, isST`，按字段名取列，不按位置）。日增量就是对每个缺失的交易日调一次，再按上面的公式把每只股票的后复权链续上；首次出现的代码从原始收盘起链。当天 **17:30–18:00 左右**才发布，之前（以及周末、节假日）返回 0 行：当天 0 行就停下等下次，过去的交易日 0 行则报错（下次重试；确认 Baostock 永远缺这一天时可用 `python -m etl.market_state --skip-day YYYY-MM-DD` 跳过，代价是所有股票的后复权链都少了这一天的涨跌）；行数少于前一交易日的 70 % 也报错，防止把半截数据写进库。
  - **证券资料与日历** `query_stock_basic()`（9,015 行，`type == "1"` 的 5,563 只 = 沪市 60 / 68 开头 + 深市 00 / 30 开头，其中 339 只已退市但历史仍可查；没有北交所代码，B 股不在内）、`query_trade_dates(start, end)`（支持未来日期，库里缓存到今天 + 60 天，开头部分变了就报错要求重建）。
  - **范围与起点**：全部沪深 A 股含已退市公司在上市期间的历史（退市日早于 2004-01-01 的不抓）、含 ST、不含北交所与 B 股；历史从 **2004-01-01** 起，这样 MA250 从 2005 年初就有值，日度序列从 2005-01 中旬发布。
  - **涨跌停计数**（存在聚合里，页面暂未展示）：主板 60 / 00 开头 ±10 %（ST ±5 %）、科创板 688 / 689（CDR，如 689009）±20 %、创业板 30 开头 2020-08-24 前 ±10 %（ST ±5 %）、之后 ±20 %（含 ST）；判定阈值各留 0.2 个百分点容差吸收价格最小变动单位带来的四舍五入，2.5 元以下的低价股可能因此漏判。上市首日与已有记录不足 5 天的股票不计。
  - **速度（2026-10-21 实测）**：逐只抓全史本机 7.9 秒/只（2004 年起的大盘股 10–16 秒，近几年上市的 2–5 秒），GitHub runner 18.2 秒/只 → 5,563 只本机约 9–12 小时、runner 约 28 小时；全市场单日横截面本机 25–35 秒、runner 25–55 秒。所以冷启动只在本地跑一次并以种子提交，Actions 和新 clone 只做按日追加。
- **上交所 融资融券（feargreed 的两融子指标）**：`GET https://query.sse.com.cn/marketdata/tradedata/queryMargin.do?isPagination=true&tabType=&stockCode=&beginDate=YYYYMMDD&endDate=YYYYMMDD&pageHelp.pageSize=2000&pageHelp.pageNo=N&pageHelp.beginPage=N&pageHelp.cacheSize=1&pageHelp.endPage=N&_=<毫秒时间戳>`，必须带 `Referer: https://www.sse.com.cn/` 与 `Accept: application/json, text/javascript, */*; q=0.01`。返回 `pageHelp{total,pageSize,pageCount,pageNo}` 与按日期倒序的 `result[]{opDate "YYYYMMDD", rzye 融资余额, rzmre, rzche, rqyl, rqylje 融券余额, rzrqjyzl 融资融券余额, …}`，金额单位是**元**（库里换成亿元，4 位小数）；`pageSize` 最大 2000，2010-03-31 到今天约 4,000 行，全史 3 页、每页约 950 KB、0.3–0.7 秒。只用**沪市**融资余额；深交所只提供按单日查询的报表（没有区间接口，拿全史要 4,000 次请求），未纳入。上交所按 T+1 公布（次日上午），所以两融子指标比其它子指标晚一个交易日，07:53 的运行补上。缓存 `cache/sse/margin.json` 存全史，之后每次只请求 `[最后缓存日 − 7 天, 今天]` 并按日期合并。
- **中证全指与申万Ａ指的复用（feargreed）**：波动率、RSI 与两个面板的叠加线用 `cache/csindex_hist/000985.json`（与 widebase 同一来源、同一缓存）；相对换手用 turnover 面板已有的申万Ａ指成交额 ÷ 流通市值（`sws.get_a_index()`），所以 2011-01-04 → 2012-04-16 的流通市值空档在这里表现为换手子分为空。不另开数据源。
- **东方财富数据中心 宏观报表（growth 的 M2 / 名义 GDP / 70 城房价）**：首选的国家统计局数据库接口 `data.stats.gov.cn/easyquery.htm` 对云端 IP（本机出口与 GitHub runner）一律返回 HTTP 403（WAF `UrlACL`），连 HTML 入口页也进不去，所以这三项用东方财富数据中心对统计局数据的转载作为明确记录的替代来源，隔离在 `etl/sources/eastmoney_macro.py` 里，面板说明里写明。接口 `GET https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=<报表>&columns=ALL&pageNumber=N&pageSize=500&sortColumns=REPORT_DATE&sortTypes=1&source=WEB&client=WEB&_=<毫秒>`（可选 `filter=(REPORT_DATE>='YYYY-MM-01')`），带 `Referer: https://data.eastmoney.com/cjsj/`；返回 `text/plain` 的 JSON `{"result":{"pages","data":[…],"count"},"success":true,"code":0}`。三个报表：`RPT_ECONOMY_CURRENCY_SUPPLY`（`REPORT_DATE` 为数据月、`BASIC_CURRENCY` = M2 期末值 亿元，2008-01 起 1 页）、`RPT_ECONOMY_GDP`（`DOMESTICL_PRODUCT_BASE` = **累计值** 亿元，2006Q1 起 1 页；当季值 = 本季累计 − 上季累计（一季度 = 累计），再四季度滚动求和放在季末月）、`RPT_ECONOMY_HOUSE_PRICE`（每月 70 个城市一行，`SECOND_HOUSE_SEQUENTIAL` = 二手住宅环比（上月 = 100）；`SECOND_HOUSE_BASE` 在 2026 年为空，所以自己从 2010-12 = 100 逐城链式定基 `index(m) = index(m−1) × 环比 ÷ 100`，70 城 = 各城指数的算术平均（每月 ≥ 68 城才算，不全的城市剔除并记入 `houseDropped`），一线 = 北京 / 上海 / 广州 / 深圳均值；冷启动 27 页，之后每月 1 页过滤增量）。统计局在每月 10–20 日发布上月数据，所以这三项按月缓存、**每月 20 日之后的首次运行刷新一次**（`fetchMonth`），其余运行 0 次请求。人民币存款余额没有对应报表（且与 M2 近似共线）、城镇居民人均可支配收入没有来源，两项不纳入并写在 `dropped` 里。
- **上海黄金交易所 Au99.99（growth 的金价 2016-12 起）**：`POST https://www.sge.com.cn/graph/Dailyhq` 表单 `instid=Au99.99`，头部 `Referer: https://www.sge.com.cn/sjzx/mrhq`、`X-Requested-With: XMLHttpRequest`、`Origin: https://www.sge.com.cn`；返回 `{"time": [[日期, 开, 收, 低, 高], …]}`——列序是按蜡烛图惯例推断的（开 / 收落在 [低, 高] 之内的行占 99 %），解析只断言收盘 > 0 且相邻（≤ 14 个日历日）两行的日变动 ≤ 15 %。接口只给**滚动 10 年**（2016-12-19 起约 2,400 行），按月刷新：每月 1 日之后的首次运行 POST 一次、按日期合并进 `cache/sge/au9999.json`，取月均价（当月未结束不计）。
- **金价静态种子 `state/gold_seed.json`（growth 的金价 2006-01 → 2016-11）**：上金所只回看 10 年，更早的月均价由两个公开来源拼成并**一次性入库**：世界银行 Pink Sheet `CMO-Historical-Data-Monthly.xlsx`（`Monthly Prices` 表的 `Gold ($/troy oz)` 列，月均美元金价）× 中国货币网 USD/CNY 中间价月均（`POST https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew`，按年取，`Referer: https://www.chinamoney.com.cn/chinese/bkccpr/`）÷ 31.1034768 克/盎司，再乘以拼接系数 `k` = 两段在重叠期 2016-12 → 2024-12（97 个月）上「上金所月均 ÷ 换算价」的几何均值（当前 1.008273，即上金所比国际价换算高约 0.8 %）。xlsx 用标准库 `zipfile` + `xml.etree` 解析，不加依赖。重建：`make gold-seed`（= `python -m etl.sources.gold_history --build-seed`，约 21 次请求），然后 `git add state/gold_seed.json`。运行时只读种子、只抓上金所。
- **上交所 每日概况（investors 的沪市 A 股流通市值 / 成交额）**：`GET https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&PRODUCT_CODE=01,02,03,11,17&type=inParams&SEARCH_DATE=YYYY-MM-DD&_=<毫秒>`，`Referer: https://www.sse.com.cn/`；`result[]{PRODUCT_CODE, TRADE_DATE, LIST_NUM, TOTAL_VALUE, NEGO_VALUE, TRADE_AMT, …}`（亿元，字符串）。沪市 A 股 = 代码 01 + 03 + 11 的合计。按日期缓存 `cache/sse/overview_YYYYMMDD.json`（当天尚未发布时存空标记，16:00 后抓到的当天不再重问），每次运行只问两融序列最后一天那一个日期（最多 1 次请求），拿不到就回退到最近有数据的一天并在卡片 `note` 里注明。
- **中证风格与基金指数代码（factors / fundroll）**：全收益代码以 `H` 开头（`000xxx → H00xxx`、`H3xxxx → H2xxxx`），但 `93xxxx` 系列在 index-perf 接口上**没有全收益代码**（`H30847` / `H31156` 返回空），所以低波动 930847 与质量 931156 用价格指数、其余四个因子用全收益；相对强弱严格同口径（全收益 ÷ H00985、价格 ÷ 000985），面板说明里标出两条价格指数不含股息。基金指数（`930950` 偏股、`930897` 非纯债债基、`H11021` 股票基金、`H11022` 混合基金、`H11023` 债券基金）是中证指数公司按基金复权单位净值编制的公开指数；中证没有「偏债型基金指数」，用非纯债债券型基金指数近似并如实标注。
- 抓取策略：单并发；同一主机两次请求间隔 ≥ 0.5 秒（腾讯 0.3 秒，**中证指数官网 3 ± 1 秒**（随机抖动）——其 index-perf 接口对短时突发限流，2026-10-21 本机与 GitHub runner 都在 8 秒内 12 个小增量请求后连续 HTTP 403，2026-10-23 又在 2 秒间隔的第 16 个全史请求上触发 403，几分钟后自愈；2026-10-24 早上 runner 从第一个请求起就连续 `Connection reset by peer`，是 IP 级封禁；申万 0.8 秒，乐咕、中债登、上交所、东方财富、上金所、中国货币网、世界银行 1 秒）；失败指数退避重试 5 次（2→4→8→16 秒）；普通浏览器 UA。**中证指数日行情另有长退避与熔断**：403 / 429 / 5xx / 连接重置 / 超时按 30 → 60 → 120 → 240 秒等待再试（共 5 次，约 7.5 分钟），一条序列用完退避仍失败则 60 秒内本进程不再请求中证（直接报「暂不可达」），避免逐条等 7 分钟；编排器在全部面板跑完后暂停 60 秒，对网络类失败的面板再构建一轮。冷启动（`cache/` 为空）约 134 + 37 + 4 + 11 + 29 + 1 + 1 次请求、约 22 分钟：M0/M1 部分 134 次约 5 分钟（腾讯行情 86 批、中证成分 .xls 6 个、指数日行情 15 条、乐咕 1 页 + 5 个指数、中债登 21 个年份），申万 36 次（列表 1 + 一级 23 段 + 二级 10 段 + Ａ指分析 1 + 日线 1）+ 乐咕行业总览 1 次约 5–13 分钟（取决于申万当时的响应速度，实测 4.5 分钟到 13 分钟都有），上交所两融 3 页 + 全市场日线库从种子解出后每个缺失交易日 1 次 Baostock 查询（1–3 分钟），M4 新增中证日行情 11 条（六个因子 + 五个基金指数，3 秒间隔约 35 秒）+ 东方财富 29 页（M2 1 + GDP 1 + 70 城 27）+ 上金所 1 次 + 上交所每日概况 1 次。之后同一天重跑申万、乐咕、上交所、Baostock、东方财富、上金所 0 次请求（每日概况最多 1 次）；次日增量 +4 次申万（一级窗口、二级窗口、Ａ指分析窗口、Ａ指日线全量）+ 中证 26 条小增量 + 上交所两融 1 次 + 每日概况 1 次 + Baostock 1 次横截面；乐咕行业总览每 30 天 1 次；东方财富每月 ≤ 3 次（20 日后）、上金所每月 1 次（1 日后）。逐只抓个股全史的 Baostock 查询只发生在 `make market-cold`。

## 排错

| 现象 | 处理 |
|---|---|
| csindex 下载失败 / 报「不是 .xls」 | 看 `logs/build_YYYYMMDD.log`；检查 `cache/csindex/` 是否有当日文件；官网偶尔维护，稍后重跑 |
| 腾讯接口超时 | 直接重跑，`cache/tencent/quotes_YYYYMMDD.json` 每批落盘，会接着抓缺的部分 |
| 「腾讯字段位置校验失败」 | 接口字段漂移，改 `etl/sources/tencent.py` 里的 `IDX_MV_TOTAL / IDX_MV_FLOAT`，先用三只参照股核对 |
| 公司网络需要代理 | `export HTTPS_PROXY=http://host:port` 后再 `make update`（requests 会自动读取） |
| Python 版本 | 3.11–3.14；requests / xlrd / tzdata 是纯 Python，baostock 依赖 pandas / numpy（二进制 wheel，只发到 3.11 及以上，无需编译） |
| macOS 上 `make setup` 装 pandas 失败（找不到 wheel / 开始编译） | 系统 `python3` 太老（3.9 / 3.10）。`brew install python@3.13`（或 3.12）后删掉 `.venv`，用 `python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt` 重建 |
| 自测失败隔离 | `ASHARE_FORCE_FAIL=erp make update`：erp 会被强制抛错、保留旧文件，其它十四个面板照常成功，退出码 0；页面上 erp 显示上一版数据并带黄色徽标 |
| Windows | 没有 `make` 时直接运行：`.venv\Scripts\python.exe etl\build_data.py`、`.venv\Scripts\python.exe serve.py 8000`、`.venv\Scripts\python.exe -m unittest discover -s tests -v`；8000 被占用时换个端口，如 `serve.py 8010` |
| 页面顶部提示「无法读取 data/manifest.json」 | 还没跑过 `make update`，或 serve.py 不是在仓库根目录启动的 |
| 乐咕 403 / 日志「乐咕页面没有 _csrf」 | 被乐咕的防护拦了（换了网络环境、短时间请求过多），或页面改版。先等几分钟或次日再跑；检查 `Referer` 是否仍是 `https://legulegu.com/stockdata/sz50-ttm-lyr`、`token` 是否为北京日期的 md5。期间面板自动回退到中证 `peg`（面板说明里会写「乐咕不可用，已回退」），不会报错 |
| 乐咕返回空数据（日志「乐咕无此指数」） | 正常：乐咕只覆盖上面列出的 12 个指数，其余本来就走中证 `peg` |
| 中债登超时 / 很慢 | 接口本身每年 5–13 秒，超时 120 秒后会重试。按年缓存在 `cache/chinabond/`，重跑只补缺的年份，不会从头下载 |
| 日志「csindex XXX 返回空数据」 | 代码不存在或接口变化：全收益代码以 `H` 开头（如 `H00300`），价格指数是 6 位数字；中证A500 / 中证2000 / 科创50 没有全收益版本 |
| 日志「csindex XXX: HTTP 403 …（限流或封禁），30 s 后第 2/5 次重试」/「Connection reset by peer」/「中证指数官网暂不可达（熔断中）」（valuation 跳过几个指数，widebase / factors / fundroll / investors 失败） | 中证指数官网对短时突发请求限流（403）或对 IP 封禁一段时间（连接被重置）。请求间隔 3 ± 1 秒；403 / 连接重置 / 超时按 30 → 60 → 120 → 240 秒长退避重试 5 次（约 7.5 分钟），仍失败则 60 秒内不再请求中证，全部面板跑完后 60 秒再给失败面板一轮机会。还是失败就等 10–30 分钟重跑 `make update-only P=widebase,factors,fundroll`（同一天已成功的序列走缓存，只补失败的几条）；Actions 上下一个时段会自动重试 |
| 日志「面板 growth 失败 … datacenter-web.eastmoney.com」 | 东方财富数据中心不可达或改了接口。只影响 growth（其它面板不读它）；growth 保留上一版 JSON。等几分钟重跑 `make update-only P=growth`；缓存里已有的月份不受影响（`cache/eastmoney/*.json`），若接口字段改名看 `etl/sources/eastmoney_macro.py` 的 `rows_to_*` |
| 日志「面板 growth 失败 … www.sge.com.cn」/「上金所 Dailyhq …」 | 上海黄金交易所接口不可达或列序 / 形状变了（解析断言收盘 > 0、相邻日变动 ≤ 15 %）。只影响 growth 的金价一段；按月刷新所以下月 1 日后才会再问。确认接口变了就看 `etl/sources/sge_gold.py` 的 `parse_dailyhq` |
| 日志「金价种子 … 不存在 / 校验失败」（growth 失败） | `state/gold_seed.json` 被删或被改。`git checkout state/gold_seed.json` 找回；要重建就 `make gold-seed`（世界银行 xlsx + 中国货币网 19 年 + 上金所 1 次，约 25 秒），核对 `k` 在 0.98–1.05 之间后 `git add state/gold_seed.json` |
| 本地 `make update` 后 `git push` 被拒（non-fast-forward） | bot 每个交易日会往 `main` 推一个 `data: … 自动更新` 提交。本地改数据、推送之前先 `git pull`（再 `make update`、再 push）；已经本地提交了就 `git pull --rebase` 后再 push |
| 在线版的数据停在前一个交易日，Actions 页里某个时段没有定时运行的记录，或记录比排定时间晚了几小时 | GitHub 的定时任务常延后（实测 1.3–5.2 小时），负载高时排队的定时运行还可能被整次丢掉，都不是 workflow 出错。四个时段（20:17 / 22:47 / 07:53 / 11:23 北京时间）就是为此排的冗余，下一个时段会自动补上；等不及就 Actions → update-and-deploy → Run workflow 手动跑一次（留空 `only`）。先确认那天确实是交易日（周末 / 节假日各源没有新数据，站点停在上一个交易日是正常的）。Actions 页「This scheduled workflow is disabled」则是仓库 60 天没有提交活动被自动停用，点 Enable 即可 |
| widebase / valuation / decomp 的日期序列里出现假期（如 2026-10-01） | 早期版本的增量请求从「最后缓存日 − 15 天」起，`startDate` 落在工作日假期时接口会补一行占位（日期 = `startDate`、值同下一交易日）。现已改为从最后缓存日当天起并丢弃占位首行；已经合并进缓存的用 `python -m etl.sources.csindex_hist --purge-dates 2026-10-01` 清掉后重跑 `make update` |
| 日志「中债 YYYY 只有 N 行，疑似接口变化」 | 往年数据应有约 250 行；少于 200 行多半是页面结构变了，看 `etl/sources/chinabond.py` 的表格解析 |
| 估值面板某指数标「样本短」 | 月末 PE 样本不足 60 个（中证2000 自 2023-06、中证A500 自 2024-09），分位数字参考意义有限，不是错误 |
| widebase / valuation / decomp 的「数据截至」比当天早 | 指数日行情只给已收盘交易日；收益拆解按已完结月份的月末算，`asOf` 是上个月末 |
| 日志「TLS 证书校验失败 … etl/certs」（swheat / indheat / turnover 同时失败） | 申万换了证书链。先 `rm cache/ca_bundle.pem` 重跑（会按 `etl/certs/` 重新拼）；仍失败则用 `openssl s_client -connect www.swsresearch.com:443 -showcerts` 看叶子证书的 AIA（CA Issuers）地址，下载新的中间证书、转成 PEM 放进 `etl/certs/`，或等申万修复。不要改成 `verify=False` |
| 申万接口 502 / 很慢 | 正常：每页 5–25 秒，5xx 自动退避重试（2→4→8→16 秒）；冷启动一级全史约 23 页，整体 5–13 分钟。超时阈值是 180 秒。只有 5 次都失败才算面板失败，下次重跑会接着缓存里已有的部分 |
| 日志「申万 一级行业: 增量区间 … 起无新数据，沿用缓存」 | 正常：在申万 18:30 前后发布之前运行，或申万偶有缺日（如 2026-10-15），当天没有新行时沿用缓存，面板 `asOf` 不变 |
| indheat 看板说明「以下二级行业在窗口内缺失的收盘超过 5%，本次未纳入」 | 源数据在该二级上有整段空洞（如 种植业 801016 在 2024-12 → 2025-06 没有行），不是抓取失败；该二级不进热力图，其它二级与一级不受影响。可用二级少于 100 个才会报错 |
| turnover 换手率 2011-01 → 2012-04 一段空白 | 申万Ａ指的流通市值在这段没有数据，换手率算不出来，成交额曲线照常；不用别的来源补 |
| 日志「乐咕行业总览抓取失败 … 沿用 N 天前的父级映射」 | 乐咕页面暂时打不开或改版，indheat 用旧映射继续构建；申万行业分类很少变，沿用几周没有影响。要强制刷新就删 `cache/legulegu/sw_overview.json` |
| 日志「申万一级行业应为 31 个，拿到 N 个」 | 申万列表接口返回异常或行业分类版本变了；看 `cache/sws/list_一级行业.json`，确认是接口问题就删掉重跑 |
| 日志「Baostock 登录失败」/「服务器连接失败」/「网络接收错误」（trend 与 feargreed 同时失败） | Baostock 服务器 `public-api.baostock.com:10030` 走裸 TCP，公司网络可能拦截该端口（HTTP 代理对它无效）；短时故障会自动重登 3 次。换网络或稍后重跑 `make update-only P=trend`（feargreed 复用同一次更新）；其它十三个面板不受影响 |
| 日志「Baostock 没有 YYYY-MM-DD 的全市场数据」 | 某个过去的交易日 Baostock 返回 0 行。通常是对方补数延迟，下次运行自动重试。确认它永远缺这一天时跳过——代价是所有股票的后复权链都少了这一天的涨跌（之后的站上均线比例略有漂移），`manifest.skippedDays` 会记下来。本地：`.venv/bin/python -m etl.market_state --skip-day YYYY-MM-DD`。**Actions 上**（库在 actions/cache 里，本地跳过不会传过去）：Actions → update-and-deploy → Run workflow，`skip_days` 填该日期（多日用逗号分隔），它作为 `ASHARE_MARKET_SKIP_DAYS` 让这次运行把那一天记入 `skippedDays` 并继续补后面的交易日，之后的定时运行不再重试它。另一条路是本地跳过后 `make market-seed` 提交新种子并在 Actions → Caches 里删掉 `etl-cache-*`，让下次运行从新种子重建 |
| 日志「… 追加日期 YYYYMMDD 不大于已有最后一行」（trend / feargreed 失败） | 旧版本在写一天的途中被打断后会一直报这个；现在写入是幂等的（已写的行复用，日志「复用 N 行」），正常不会再出现。若仍出现，说明股票文件与清单彻底不一致：`make clean-cache-market && make update` 从种子重建 |
| 日志「… 已有行与重算不一致」 | 某只股票文件里最后一行的日期已是今天，但收盘与按清单 `lastClose` 续链的结果对不上（库被别的进程改过、或两次运行之间 Baostock 修订了昨收）。`make clean-cache-market && make update` 从种子重建 |
| 看板说明「全市场日线库落后 N 个交易日（预算限制）」 | 一次运行最多补 `ASHARE_MARKET_BUDGET_MIN` 分钟（默认 18）的交易日。再跑一次 `make update` 接着补，或 `ASHARE_MARKET_BUDGET_MIN=60 make update` 一次补完；Actions 上下一次定时运行会自动继续 |
| 日志「全市场日线库不存在：本地先运行 make market-cold，或提交 state/market_seed.zip」 | `cache/market/` 与种子都没有（种子被删或 clone 不完整）。`git checkout state/market_seed.zip` 找回种子；要重建全史则 `make market-cold`（约 9–12 小时）后 `make market-seed` |
| 日志「全市场日线库建设中 done/total」 | 冷启动没跑完就运行了 `make update`。等 `make market-cold` 结束（或重跑它续传）；开发时想先看局部数据可 `ASHARE_MARKET_ALLOW_PARTIAL=1 make update-only P=trend`（不联网、不检查 coldDone，看板说明标「开发模式：部分股票」，不要提交这样的数据） |
| Actions 日志「从种子初始化全市场日线库」后补了很多天 | actions/cache 被淘汰（7 天没命中或超过 10 GB 配额），库从种子重建、按日补齐，属正常；补不完的部分下次运行继续 |
| 日志「交易日历前缀变化，请 make clean-cache-market 后重建」 | Baostock 的历史交易日历与本地缓存不一致（极少见）。`make clean-cache-market && make update` 从种子重建 |
| 日志「冷启动校验 … 不一致（> 1%）」 | 冷启动期间 Baostock 修订了最近几天的数据。重跑 `make market-cold`（待抓清单已空，只会重做收尾校验）；仍不过就 `make clean-cache-market` 后重来 |
| 日志「种子 … 超过 15 MB 上限」 | `make market-seed` 时股票数增长超预期。把 `etl/market_state.py` 的 `TAIL_ROWS` 降到 251（不能再低：MA250 需要 250 行加当日）或提高 `MAX_SEED_BYTES`（GitHub 单文件上限 100 MB） |

## 如何新增面板

一个面板 = 一个 Python 模块 + 一个 JS 模块，不需要在任何地方登记。

1. **ETL**：新建 `etl/panels/<id>.py`，导出
   - `PANEL_ID = '<id>'`、`TITLE = '…'`，可选 `STATIC = True`（不依赖行情、不参与新鲜度计算），可选 `COMPACT_JSON = True`（输出无缩进的单行 JSON，数据量大于几百 KB 时用，indheat 就是这样）；
   - `build() -> {'asOf': 'YYYY-MM-DD', 'sources': [...], 'notes': [...], 'data': {...}}`。
   - 抓数一律走 `etl.common.http_get`（自带限速与重试），按日缓存用 `cache_load / cache_save`。
   - 把纯计算抽成函数并在 `tests/` 里加离线测试。`make update` 会自动发现新模块。
2. **前端**：新建 `web/js/panels/<id>.js`：
   ```js
   AshareBoard.registerPanel({
     id: '<id>', title: '…', subtitle: '…', dataFile: '<id>.json',
     notes: ['口径说明…'],
     render(body, json, ctx) {            // ctx = {echarts, palette, fmt}
       // 往 body 里放 DOM / echarts.init(...)
       return { charts: [chart] };        // 有图表就返回，页面据此显示「导出 PNG」；纯表格面板不返回
     }
   });
   ```
   然后在 `web/index.html` 的 `<head>` 里追加一行 `<script defer src="js/panels/<id>.js"></script>`（注册顺序 = 页面顺序）。
   归一、年化、对数轴配置、时间窗按钮、可排序表格、N 日涨幅 / 滚动均值 / RPS / 分位、热力图基础配置（`heatmapBase`）、下拉框（`selectControl`）、面包屑（`crumbs`）等已有现成函数在 `web/js/lib/charts.js`（`window.AshareCharts`），指数配色与语义色统一取 `ctx.palette`，面板里不要再写一套。
3. 导出 PNG 只拼接 `render` 返回的 ECharts 实例；HTML 表格不进 PNG（M0 的 loss 面板因此没有导出按钮）。
4. 需要指数 PE 时用 `etl/sources/pe_select.get_monthly_pe(code, name)`，它负责选来源、月末抽样和回退；需要指数日线用 `etl/sources/csindex_hist.get_index_history(code)`。
5. 需要申万行业数据时：`etl/sources/sws.get_level_history(sws.LEVEL1 | sws.LEVEL2)` 返回 `{dates[], codes{code:{name,close[],amtShare[],mv[],turnoverSw[]}}}`（全史增量缓存，列与 `dates` 等长、无数据处为 `None`，先用 `compute.drop_none` 再算）；`sws.get_index_list(sws.LEVEL1)` 给 31 个一级的权威名单；`sws.get_a_index()` 给申万Ａ指的 `dates / close / amount / volume / mv / turnoverSw`；二级→一级父级用 `etl/sources/legulegu.get_sw_parent_map()['level2'][code]['parent']`；煤炭等起点特殊的代码先过 `sws.cut_series_start(code, dates, values)`。
6. 需要全市场日线的统计量时不要再抓个股：先 `etl.market_state.update_incremental(market_state.budget_from_env())` 把库追到最新（每进程只做一次，多个面板共用），再 `market_state.MarketStore().load_agg()` 拿每日计数（`dates / nTraded / n250 / above250 / n50 / above50 / adv / dec / flat / limitUp / limitDown / nLimitEligible`）；`trend.load_market()` / `trend.market_notes()` 封装了这两步和「库落后 / 开发模式」的提示句。面板输出只放比例与计数，不放个股明细（每个 JSON ≤ 1 MB）。需要新的个股级统计量就在 `market_state._accumulate_series` / `stock_day_flags` 里加一个计数并 `make market-rebuild`。沪市融资余额用 `etl/sources/sse_margin.get_margin_history()`（`dates / rzye / rqylje / total`，亿元）；技术指标用 `compute.ema / macd / rsi / realized_vol / rolling_percentile`。
