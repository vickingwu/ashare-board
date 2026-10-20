# A股看板

A 股长周期看板：Python 脚本抓公开数据算成静态 JSON，浏览器用 ECharts 渲染。在线版由 GitHub Actions 每个交易日自动刷数据并发布到 GitHub Pages；本地则 `make update` 刷数据、`make serve` 打开。

目前（M0–M2）九个面板：**宽基指数覆盖范围**（coverage）、**宽基指数全收益净值**（widebase）、**主要宽基指数估值分位**（valuation）、**股债性价比**（erp）、**指数收益来源拆解**（decomp）、**申万一级行业年度涨跌幅**（swheat）、**行业交易热度**（indheat）、**全市场成交金额与换手率**（turnover）、**抄底数学题**（loss）。

## 在线版（GitHub Pages）

地址：**https://vickingwu.github.io/ashare-board/** —— 浏览器直接打开，不用 clone、不用装 Python。

- 数据由 GitHub Actions 自动刷新：每个交易日（周一至周五）**北京时间 16:40** 运行 `.github/workflows/update-and-deploy.yml`，抓数 → 把变化的 `data/*.json` 提交回 `main` → 重新发布页面；全程约 6–10 分钟。
- 手动刷新：仓库页 **Actions → update-and-deploy → Run workflow**。可选参数 `only` 填面板 id（逗号分隔，如 `erp,valuation`），只刷这几个面板；留空刷全部。
- 本地刷新也会发布：`make update` 之后 `git push`，push 到 `main` 会触发同一个 workflow 只跑发布这一步。
- 出错怎么看：某个面板抓数失败时页面照常发布，该面板标题旁出现黄色徽标「本次更新失败，显示 … 的上一版数据」，原因在 `data/manifest.json` 的 `error` 字段；完整日志在该次运行的 Artifacts 里（名为 `logs`，保留 14 天）。整个运行失败（装依赖、推送出错）则不发布，页面保持上一版。
- 页面顶部「生成于 …」是 ETL 运行时间（北京时间），「数据更新至 …」是各数据源的最新日期；工作日估算的交易日差不扣节假日。
- 本地也能生成同样的发布目录：`make site`，输出到 `_site/`。

### 启用步骤（一次性）

1. **仓库可见性**：GitHub Free 只给公开仓库提供 Pages；私有仓库要开 Pages 需要 GitHub Pro。要用免费方案就把仓库改公开：Settings → General → 最底部 Danger Zone → Change visibility → Public。
2. **开启 Pages**：Settings → Pages → Build and deployment → Source 选 **GitHub Actions**。如果第一次运行 workflow 时 `configure-pages` 已经自动开好了，这一步会显示已启用，跳过即可。
3. **跑一次**：Actions → update-and-deploy → Run workflow（分支选 `main`），等 6–10 分钟，运行页右侧 `deploy` 作业会给出页面地址，打开 https://vickingwu.github.io/ashare-board/ 。

之后无需再操作，每个交易日 16:40 自动刷新。

## 安装与运行（macOS）

需要 `python3`（3.9–3.14 均可）和 `git`。没有的话 `xcode-select --install`，或 `brew install python`。

```bash
git clone https://github.com/vickingwu/ashare-board.git
cd ashare-board
make setup     # 建 .venv 并安装 requests / xlrd / tzdata
make update    # 抓数据、生成 data/*.json（首次约 18–20 分钟：申万行业全史约 13 分钟、中债登按年下载约 4 分钟；之后每次 1–3 分钟，主要是腾讯行情）
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
- 长历史缓存（指数日行情、指数 PE、国债收益率、申万行业历史 `cache/sws/`）：以「当天 16:00」为界判断能否复用——16:00 之前抓的缓存只在 16:00 之前复用，16:00 之后抓的当天一直复用。所以同一天上午跑一次、收盘后再跑一次，各联网一次；其余重跑都不联网。往年的国债收益率文件一旦存在永不重抓。申万发布 T+1，所以 16:00 后重跑通常也拿不到当天的行业收盘，面板「数据截至」比当天早一个交易日是正常的。
- 二级→一级行业的父级映射（`cache/legulegu/sw_overview.json`）按 30 天复用，到期才重新抓乐咕的行业总览页；抓不到就沿用旧映射并在日志里 WARNING。申万行业分类一年内很少变动，30 天够用；想立刻刷新就删掉这个文件。
- 只重抓申万相关缓存：`make clean-cache-sws`（约 13 分钟），不碰中证 / 中债登 / 腾讯的缓存。
- 缓存只是加速手段，删掉任何一个文件都不影响正确性，下次构建会重新下载。

## 目录结构

```
ashare-board/
├── etl/
│   ├── build_data.py        # 编排器：发现面板 → 逐个 build → 写 data/*.json + manifest.json
│   ├── common.py            # 路径/时区/日志/礼貌抓取(http_get)/原子写 JSON/按日缓存/16:00 缓存新鲜度规则
│   ├── compute.py           # 纯数值函数：月末抽样、分位、年化、归一、收益拆解、自然年涨跌幅、排名、滚动均值（无 I/O，离线单测）
│   ├── certs/               # 申万服务器证书链缺的 DigiCert 中间证书（公开 CA 证书，拼进 cache/ca_bundle.pem）
│   ├── sources/
│   │   ├── csindex.py       # 中证指数官网成分股 .xls 下载与解析
│   │   ├── csindex_hist.py  # 中证指数官网 指数日行情（价格 / 全收益收盘、滚动 PE），全历史增量缓存
│   │   ├── legulegu.py      # 乐咕乐股 指数月度 PE（整体法），md5 日期令牌 + CSRF 两步请求；申万行业总览页 → 二级→一级父级映射
│   │   ├── sws.py           # 申万宏源研究 指数列表 / 日线 / 指数分析接口，一级·二级·申万Ａ指 全历史增量缓存
│   │   ├── chinabond.py     # 中债登 国债收益率曲线 10 年期，按年抓 HTML 表格
│   │   ├── pe_select.py     # 每个指数选一个 PE 来源（乐咕优先，否则中证 peg），供三个面板共用
│   │   └── tencent.py       # 腾讯行情批量接口（总市值）与字段位置校验
│   └── panels/
│       ├── coverage.py      # 面板：宽基指数覆盖范围
│       ├── widebase.py      # 面板：宽基指数全收益净值
│       ├── valuation.py     # 面板：主要宽基指数估值分位
│       ├── erp.py           # 面板：股债性价比
│       ├── decomp.py        # 面板：指数收益来源拆解
│       ├── swheat.py        # 面板：申万一级行业年度涨跌幅
│       ├── indheat.py       # 面板：行业交易热度（一级 + 二级收盘序列，紧凑 JSON，指标在浏览器里算）
│       ├── turnover.py      # 面板：全市场成交金额与换手率
│       └── loss.py          # 面板：抄底数学题（纯算术，STATIC）
├── web/
│   ├── index.html           # 页面骨架（不含任何面板标记，面板由 JS 注册生成）
│   ├── css/style.css
│   ├── js/app.js            # 前端框架：注册表、导航、懒加载、新鲜度条、深链接、导出 PNG、配色表
│   ├── js/lib/charts.js     # 面板共用的图表 / 表格小工具（归一、年化、对数轴、时间窗按钮、可排序表格、N 日涨幅 / 滚动均值 / RPS、热力图基础配置、下拉框、面包屑）
│   ├── js/panels/*.js       # 每个面板一个模块
│   └── vendor/echarts.min.js
├── data/                    # ETL 输出，入库（clone 下来即可直接看）；indheat.json 是无缩进的紧凑 JSON（约 0.9 MB）
├── cache/  logs/            # 运行期产物，不入库（cache/ca_bundle.pem 也在这里生成）
├── tests/                   # 离线单元测试（unittest），fixtures/ 是各接口的真实响应裁剪件（sws_* 为申万接口、legulegu_sw_overview_trim.html 为乐咕行业总览）
├── scripts/build_site.py    # 把 web/ + data/ 拼成 GitHub Pages 发布目录 _site/
├── .github/workflows/update-and-deploy.yml   # 每个交易日 16:40 抓数、提交、发布到 Pages
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
| M0 | loss 抄底数学题 | ✅ | 纯算术：亏多少要涨多少回本 |
| M3 | 全市场日线（Baostock） | 未实现 | baostock 在 Python 3.14 可安装、可登录（探测 2026-10-19：上证综指 8,743 行自 1990-12-19，含成交额），但依赖 numpy / pandas 且 login 约 50 秒，待定 |
| M4 | 宏观与基金 | 未实现 | |
| M5 | 工具：个股四面板走势图 `/api/chart`（matplotlib）、汇总页 | 未实现（`serve.py` 目前对 `/api/chart` 返回 501） | |

## 数据源与口径

- **成分股名单**：中证指数官网公开文件 `https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{指数代码}cons.xls`，旧版 Excel（OLE2），用 `xlrd` 解析。代码列是数字会丢前导零，解析时补回 6 位。名单日期取文件内「日期」列。
- **总市值**：腾讯行情 `https://qt.gtimg.cn/q=sh600519,sz000001,...`，每批 60 只，GBK 文本按 `~` 切分；`[44]` 流通市值、`[45]` 总市值（亿元）、`[30]` 行情时间。字段位置没有官方文档，因此每次构建用 贵州茅台 / 工商银行 / 宁德时代 三只参照股校验量级（工行要求 总市值 > 流通市值，用于区分两列），校验不过整个面板失败而不是出错图。北交所 `920xxx` 代码以 `bj` 前缀请求，字段数 87 但位置一致。
- **格位边界**：图里按**总市值**排名切格，而中证规模指数选样用的是**自由流通市值**并带缓冲区，所以 300/800/1800/3800 边界附近会有少量「越界」格子，是口径差异不是错误。
- **全市场**：以中证全指为全体（剔除 ST 与上市不久的新股），只数略少于交易所上市公司总数。
- **中证A500**：按行业均衡选样，不是规模层，与沪深300 的重叠度见矩阵。
- **交易日估算**：页面顶部「N 个交易日前」只按周一至周五计数，未扣节假日。
- **指数日行情（widebase / valuation / decomp）**：中证指数官网 `https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}&startDate=YYYYMMDD&endDate=YYYYMMDD`，需带 `Referer`，返回 JSON（`code` 是字符串 `"200"`）。不限区间，`startDate=20050101` 到今天一次返回全部历史（沪深300 约 5,290 行、1.8 MB）。每行用 `tradeDate`、`close`、`peg`（滚动市盈率，价格与全收益序列上数值相同）。第 0 行永远是 `20050101`（周六）的基点值，即使指数基日更晚，所以周末日期的行一律丢弃；只给已收盘交易日。不存在的代码返回 `data: []` 而不是错误。全收益指数代码以 `H` 开头：`H00300` 沪深300、`H00905` 中证500、`H00852` 中证1000、`H00906` 中证800、`H00985` 中证全指、`H20269` 红利低波。缓存 `cache/csindex_hist/{code}.json` 保存整条历史，之后每次只请求最后缓存日前 15 天到今天的增量并按日期合并。
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
  - **口径与坑**：申万 **T+1 发布**（当天收盘后拿不到当天数据，面板「数据截至」比当天早一个交易日），且偶有缺日（如 2026-10-15 整天没有行业数据，日志里会出现「增量区间无新数据」，属正常）；申万Ａ指的流通市值 **2011-01-04 → 2012-04-16 整段为空**（只有 3 天有值），换手率在这段留空、不用别的来源补；申万自己的 `turnoverrate` 用的分母与本看板不同（数值略低），只作对照列；煤炭（801950）这个代码在 2014-02-21 → 2017-01-20 返回的是另一只小指数（流通市值 231–418 亿元、成交额占比 0.1–0.3 %，而 2021-12-13 重新开始时流通市值 3416.68 亿元），中间 2017-01-23 → 2021-12-10 没有记录；`swheat.SERIES_START` 把 2021-12-13 之前的行截掉，年度表从 2022 年起、年化也从 2022 年起算；申万Ａ指 = 沪深两市全部 A 股（不含北交所与 B 股），其成交额比上证 + 深证综指成交额之和低 0.2–0.4%（差额是 B 股）。
  - **缓存** `cache/sws/`：`list_一级行业.json`（按日）、`analysis_l1.json` / `analysis_l2.json` / `analysis_801003.json`（全史，一个文件一条共用 `dates[]`，每个代码一组与之等长的数组，无数据处为 `null`）、`trend_801003.json`（全史，不新鲜就整条重下）。增量更新只请求 `[最后缓存日 − 30 天, 今天]` 一个窗口，按日期合并、新值覆盖旧值。一级从 1999-12-30 回填（约 23 次请求），二级只回填最近 900 天（约 10 次，indheat 只用最近 600 个交易日）。
- **二级→一级行业父级映射（indheat）**：乐咕乐股 申万行业总览页 `GET https://legulegu.com/stockdata/sw-industry-overview`（普通页面，不需要令牌）。`div#level1Items` / `div#level2Items` 下每个条目：`.lg-industries-item-chinese-title` 是代码（`801016.SI`，去掉 `.SI`），`.lg-industries-item-number` 是 `名称(成份数)[上级行业]`，上级按名称对回 31 个一级。申万接口本身不给父级，代码前缀也推不出来（如 801092 → 汽车、801101 → 计算机、801191 → 非银金融）。缓存 `cache/legulegu/sw_overview.json` 复用 30 天；页面抓不到时沿用旧映射并 WARNING，没有旧映射时 indheat 失败、其它面板不受影响。
- 抓取策略：单并发；同一主机两次请求间隔 ≥ 0.5 秒（腾讯 0.3 秒，中证 0.6 秒，申万 0.8 秒，乐咕与中债登 1 秒）；失败指数退避重试 5 次（2→4→8→16 秒）；普通浏览器 UA。冷启动（`cache/` 为空）约 134 + 37 次请求、约 18–20 分钟：M0/M1 部分 134 次约 5 分钟（腾讯行情 86 批、中证成分 .xls 6 个、指数日行情 15 条、乐咕 1 页 + 5 个指数、中债登 21 个年份），申万 36 次（列表 1 + 一级 23 段 + 二级 10 段 + Ａ指分析 1 + 日线 1）+ 乐咕行业总览 1 次约 5–13 分钟（取决于申万当时的响应速度，实测 4.5 分钟到 13 分钟都有）。之后同一天重跑申万与乐咕 0 次请求；次日增量 +4 次（一级窗口、二级窗口、Ａ指分析窗口、Ａ指日线全量）；乐咕行业总览每 30 天 1 次。

## 排错

| 现象 | 处理 |
|---|---|
| csindex 下载失败 / 报「不是 .xls」 | 看 `logs/build_YYYYMMDD.log`；检查 `cache/csindex/` 是否有当日文件；官网偶尔维护，稍后重跑 |
| 腾讯接口超时 | 直接重跑，`cache/tencent/quotes_YYYYMMDD.json` 每批落盘，会接着抓缺的部分 |
| 「腾讯字段位置校验失败」 | 接口字段漂移，改 `etl/sources/tencent.py` 里的 `IDX_MV_TOTAL / IDX_MV_FLOAT`，先用三只参照股核对 |
| 公司网络需要代理 | `export HTTPS_PROXY=http://host:port` 后再 `make update`（requests 会自动读取） |
| Python 版本 | 3.9–3.14 都可以；依赖只有 requests / xlrd / tzdata（纯 Python，无需编译） |
| 自测失败隔离 | `ASHARE_FORCE_FAIL=erp make update`：erp 会被强制抛错、保留旧文件，其它八个面板照常成功，退出码 0；页面上 erp 显示上一版数据并带黄色徽标 |
| Windows | 没有 `make` 时直接运行：`.venv\Scripts\python.exe etl\build_data.py`、`.venv\Scripts\python.exe serve.py 8000`、`.venv\Scripts\python.exe -m unittest discover -s tests -v`；8000 被占用时换个端口，如 `serve.py 8010` |
| 页面顶部提示「无法读取 data/manifest.json」 | 还没跑过 `make update`，或 serve.py 不是在仓库根目录启动的 |
| 乐咕 403 / 日志「乐咕页面没有 _csrf」 | 被乐咕的防护拦了（换了网络环境、短时间请求过多），或页面改版。先等几分钟或次日再跑；检查 `Referer` 是否仍是 `https://legulegu.com/stockdata/sz50-ttm-lyr`、`token` 是否为北京日期的 md5。期间面板自动回退到中证 `peg`（面板说明里会写「乐咕不可用，已回退」），不会报错 |
| 乐咕返回空数据（日志「乐咕无此指数」） | 正常：乐咕只覆盖上面列出的 12 个指数，其余本来就走中证 `peg` |
| 中债登超时 / 很慢 | 接口本身每年 5–13 秒，超时 120 秒后会重试。按年缓存在 `cache/chinabond/`，重跑只补缺的年份，不会从头下载 |
| 日志「csindex XXX 返回空数据」 | 代码不存在或接口变化：全收益代码以 `H` 开头（如 `H00300`），价格指数是 6 位数字；中证A500 / 中证2000 / 科创50 没有全收益版本 |
| 日志「中债 YYYY 只有 N 行，疑似接口变化」 | 往年数据应有约 250 行；少于 200 行多半是页面结构变了，看 `etl/sources/chinabond.py` 的表格解析 |
| 估值面板某指数标「样本短」 | 月末 PE 样本不足 60 个（中证2000 自 2023-06、中证A500 自 2024-09），分位数字参考意义有限，不是错误 |
| widebase / valuation / decomp 的「数据截至」比当天早 | 指数日行情只给已收盘交易日；收益拆解按已完结月份的月末算，`asOf` 是上个月末 |
| 日志「TLS 证书校验失败 … etl/certs」（swheat / indheat / turnover 同时失败） | 申万换了证书链。先 `rm cache/ca_bundle.pem` 重跑（会按 `etl/certs/` 重新拼）；仍失败则用 `openssl s_client -connect www.swsresearch.com:443 -showcerts` 看叶子证书的 AIA（CA Issuers）地址，下载新的中间证书、转成 PEM 放进 `etl/certs/`，或等申万修复。不要改成 `verify=False` |
| 申万接口 502 / 很慢 | 正常：每页 5–25 秒，5xx 自动退避重试（2→4→8→16 秒）；冷启动一级全史约 23 页，整体 5–13 分钟。超时阈值是 180 秒。只有 5 次都失败才算面板失败，下次重跑会接着缓存里已有的部分 |
| 日志「申万 一级行业: 增量区间 … 起无新数据，沿用缓存」 | 正常：申万 T+1 发布且偶有缺日（如 2026-10-15），当天没有新行时沿用缓存，面板 `asOf` 不变 |
| indheat 看板说明「以下二级行业在窗口内缺失的收盘超过 5%，本次未纳入」 | 源数据在该二级上有整段空洞（如 种植业 801016 在 2024-12 → 2025-06 没有行），不是抓取失败；该二级不进热力图，其它二级与一级不受影响。可用二级少于 100 个才会报错 |
| turnover 换手率 2011-01 → 2012-04 一段空白 | 申万Ａ指的流通市值在这段没有数据，换手率算不出来，成交额曲线照常；不用别的来源补 |
| 日志「乐咕行业总览抓取失败 … 沿用 N 天前的父级映射」 | 乐咕页面暂时打不开或改版，indheat 用旧映射继续构建；申万行业分类很少变，沿用几周没有影响。要强制刷新就删 `cache/legulegu/sw_overview.json` |
| 日志「申万一级行业应为 31 个，拿到 N 个」 | 申万列表接口返回异常或行业分类版本变了；看 `cache/sws/list_一级行业.json`，确认是接口问题就删掉重跑 |

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
5. 需要申万行业数据时：`etl/sources/sws.get_level_history(sws.LEVEL1 | sws.LEVEL2)` 返回 `{dates[], codes{code:{name,close[],amtShare[],mv[],turnoverSw[]}}}`（全史增量缓存，列与 `dates` 等长、无数据处为 `None`，先用 `compute.drop_none` 再算）；`sws.get_index_list(sws.LEVEL1)` 给 31 个一级的权威名单；`sws.get_a_index()` 给申万Ａ指的 `dates / close / amount / volume / mv / turnoverSw`；二级→一级父级用 `etl/sources/legulegu.get_sw_parent_map()['level2'][code]['parent']`。
