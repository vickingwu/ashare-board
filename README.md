# A股看板

A 股长周期看板：Python 脚本抓公开数据算成静态 JSON，浏览器用 ECharts 渲染；`make update` 手工刷数据，`make serve` 本地打开。

目前（M0）两个面板：**抄底数学题**（loss）、**宽基指数覆盖范围**（coverage）。

## 安装与运行（macOS）

需要 `python3`（3.9–3.14 均可）和 `git`。没有的话 `xcode-select --install`，或 `brew install python`。

```bash
git clone https://github.com/vickingwu/ashare-board.git
cd ashare-board
make setup     # 建 .venv 并安装 requests / xlrd / tzdata
make update    # 抓数据、生成 data/*.json（首次约 30–60 秒）
make serve     # 启动本地服务，然后打开 http://localhost:8000/
```

为什么不能直接双击 `web/index.html`：页面用 `fetch` 读取 `data/*.json`，浏览器在 `file://` 下会拦截这类请求，所以必须经由 `serve.py`（只监听 127.0.0.1，纯标准库）。

## 手工更新流程

- 交易日 **16:30 之后**运行 `make update`，拿到的是当日收盘后的总市值。
- 盘中运行也可以，会得到盘中快照，面板「看板说明」里会标注抓取时间。
- 任一面板失败不影响其它面板：失败面板保留上一版 JSON，页面顶部该面板会出现黄色徽标「本次更新失败，显示 … 的上一版数据」，`data/manifest.json` 里记录错误原因。
- 只刷一个面板：`make update-only P=coverage`。
- 当日缓存：成分名单与行情按日期缓存在 `cache/`，同一天重跑不再联网（约 1 秒）。

## 目录结构

```
ashare-board/
├── etl/
│   ├── build_data.py        # 编排器：发现面板 → 逐个 build → 写 data/*.json + manifest.json
│   ├── common.py            # 路径/时区/日志/礼貌抓取(http_get)/原子写 JSON/按日缓存
│   ├── sources/
│   │   ├── csindex.py       # 中证指数官网成分股 .xls 下载与解析
│   │   └── tencent.py       # 腾讯行情批量接口（总市值）与字段位置校验
│   └── panels/
│       ├── loss.py          # 面板：抄底数学题（纯算术，STATIC）
│       └── coverage.py      # 面板：宽基指数覆盖范围
├── web/
│   ├── index.html           # 页面骨架（不含任何面板标记，面板由 JS 注册生成）
│   ├── css/style.css
│   ├── js/app.js            # 前端框架：注册表、导航、懒加载、新鲜度条、深链接、导出 PNG
│   ├── js/panels/*.js       # 每个面板一个模块
│   └── vendor/echarts.min.js
├── data/                    # ETL 输出，入库（clone 下来即可直接看）
├── cache/  logs/            # 运行期产物，不入库
├── tests/                   # 离线单元测试（unittest）
├── serve.py                 # 本地静态服务器
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

## 面板清单与路线图

| 里程碑 | 面板 | 状态 |
|---|---|---|
| M0 | loss 抄底数学题 | ✅ |
| M0 | coverage 宽基指数覆盖范围 | ✅ |
| M1 | 宽基与估值簇 | 未实现 |
| M2 | 行业与成交 | 未实现 |
| M3 | 全市场日线（Baostock） | 未实现 |
| M4 | 宏观与基金 | 未实现 |
| M5 | 工具：个股四面板走势图 `/api/chart`（matplotlib）、汇总页 | 未实现（`serve.py` 目前对 `/api/chart` 返回 501） |

## 数据源与口径

- **成分股名单**：中证指数官网公开文件 `https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{指数代码}cons.xls`，旧版 Excel（OLE2），用 `xlrd` 解析。代码列是数字会丢前导零，解析时补回 6 位。名单日期取文件内「日期」列。
- **总市值**：腾讯行情 `https://qt.gtimg.cn/q=sh600519,sz000001,...`，每批 60 只，GBK 文本按 `~` 切分；`[44]` 流通市值、`[45]` 总市值（亿元）、`[30]` 行情时间。字段位置没有官方文档，因此每次构建用 贵州茅台 / 工商银行 / 宁德时代 三只参照股校验量级（工行要求 总市值 > 流通市值，用于区分两列），校验不过整个面板失败而不是出错图。北交所 `920xxx` 代码以 `bj` 前缀请求，字段数 87 但位置一致。
- **格位边界**：图里按**总市值**排名切格，而中证规模指数选样用的是**自由流通市值**并带缓冲区，所以 300/800/1800/3800 边界附近会有少量「越界」格子，是口径差异不是错误。
- **全市场**：以中证全指为全体（剔除 ST 与上市不久的新股），只数略少于交易所上市公司总数。
- **中证A500**：按行业均衡选样，不是规模层，与沪深300 的重叠度见矩阵。
- **交易日估算**：页面顶部「N 个交易日前」只按周一至周五计数，未扣节假日。
- 抓取策略：单并发；同一主机两次请求间隔 ≥ 0.5 秒（腾讯 0.3 秒）；失败指数退避重试 5 次（2→4→8→16 秒）；普通浏览器 UA。

## 排错

| 现象 | 处理 |
|---|---|
| csindex 下载失败 / 报「不是 .xls」 | 看 `logs/build_YYYYMMDD.log`；检查 `cache/csindex/` 是否有当日文件；官网偶尔维护，稍后重跑 |
| 腾讯接口超时 | 直接重跑，`cache/tencent/quotes_YYYYMMDD.json` 每批落盘，会接着抓缺的部分 |
| 「腾讯字段位置校验失败」 | 接口字段漂移，改 `etl/sources/tencent.py` 里的 `IDX_MV_TOTAL / IDX_MV_FLOAT`，先用三只参照股核对 |
| 公司网络需要代理 | `export HTTPS_PROXY=http://host:port` 后再 `make update`（requests 会自动读取） |
| Python 版本 | 3.9–3.14 都可以；依赖只有 requests / xlrd / tzdata（纯 Python，无需编译） |
| 自测失败隔离 | `ASHARE_FORCE_FAIL=coverage make update`：coverage 会被强制抛错、保留旧文件，loss 照常成功，退出码 0 |
| Windows | 没有 `make` 时直接运行：`.venv\Scripts\python.exe etl\build_data.py`、`.venv\Scripts\python.exe serve.py 8000`、`.venv\Scripts\python.exe -m unittest discover -s tests -v` |
| 页面顶部提示「无法读取 data/manifest.json」 | 还没跑过 `make update`，或 serve.py 不是在仓库根目录启动的 |

## 如何新增面板

一个面板 = 一个 Python 模块 + 一个 JS 模块，不需要在任何地方登记。

1. **ETL**：新建 `etl/panels/<id>.py`，导出
   - `PANEL_ID = '<id>'`、`TITLE = '…'`，可选 `STATIC = True`（不依赖行情、不参与新鲜度计算）；
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
3. 导出 PNG 只拼接 `render` 返回的 ECharts 实例；HTML 表格不进 PNG（M0 的 loss 面板因此没有导出按钮）。
