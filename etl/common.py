# -*- coding: utf-8 -*-
"""ETL 公共设施：路径、时区、日志、礼貌抓取、原子写 JSON、按日缓存。

设计约束（见 README「数据源与口径」）：
* 单线程、单并发：所有 HTTP 请求都经过 ``http_get`` / ``http_post``（共用 ``_http`` 核心），按主机维护最小间隔；
  ``http_post`` 以表单（``application/x-www-form-urlencoded``）提交 ``data``，限速、重试、4xx、TLS 规则与 GET 完全一致；
* 指数退避重试：5 次，2s → 4s → 8s → 16s（上限 60s）；``max_attempts`` 可由调用方收紧（中证日行情只试 1 次，
  由 ``csindex_hist`` 自己按 30 → 60 → 120 → 240 秒的长退避重试，并带熔断）；
* 主机间隔可带抖动（``HOST_JITTER``）：中证 12 ± 1 秒（其限流是约 100 秒窗口内第 13 个请求起 403 的突发计数，
  12 秒间隔让 12 个请求至少跨 121 秒），抖动也避免等间隔的请求串被当成脚本流量；
* ``is_network_error(msg)`` 判断一条错误文本是不是网络 / 限流类（连接重置、超时、HTTP 403 / 429 / 5xx、重试耗尽、
  Baostock 网络错误、熔断），编排器据此在全部面板跑完后对这类失败的面板再试一轮；
* 失败只影响当前面板：抛 ``PanelError`` 或任何异常，由编排器隔离；
* 写文件一律先写 ``*.json.tmp`` 再 ``os.replace``，前端永远读不到半截文件；
* 长历史数据源（指数日行情、PE、国债收益率）的缓存按「当天 16:00 前/后」判断能否复用
  （``cache_is_fresh``）：收盘前抓的缓存只在收盘前复用，收盘后抓的当天一直复用；
  按「天数」过期的缓存（如行业父级映射）用 ``cache_age_days`` 判断；
* 月度源按月缓存：release_day 之后的首次运行刷新一次。``monthly_fetch_month(now, release_day)`` 给出
  「本次抓取应记为哪个月」（当日 ≥ release_day 记当月，否则记上月）；``monthly_refresh_due(fetch_month, now,
  release_day)`` 判断是否该刷新：没抓过 → 该；缓存记的月份 ≥ 当月 → 不该；否则当日 ≥ release_day 才该。
  于是 release_day 之前冷抓一次会记成上月，到 release_day 当天再抓一次，之后整月不再联网（宏观数据
  release_day = 20，黄金日线 release_day = 1 = 每月首次运行补齐上月）；
* TLS 一律严格校验，绝不关闭证书校验。个别站点（www.swsresearch.com）握手时不发中间证书，
  把其公开的中间证书 PEM 放进 ``etl/certs/``，``ca_bundle_path()`` 会把 certifi 根证书与这些
  PEM 拼成 ``cache/ca_bundle.pem`` 交给 Session；证书校验失败直接抛 ``PanelError``，不重试；
* ``write_json_atomic(..., compact=True)`` 输出不缩进的紧凑 JSON，供体积较大的面板文件使用；
* 环境变量开关统一经 ``env_int(name, default)``（取整数，缺失或非法时用默认值）与
  ``env_flag(name)``（``"1"/"true"/"yes"`` 为真，不区分大小写）读取，例如全市场日线库的
  ``ASHARE_MARKET_BUDGET_MIN`` / ``ASHARE_MARKET_ALLOW_PARTIAL``；
* ``STATE_DIR``（``state/``）存放入库的种子快照（如 ``market_seed.zip``），与可重建的 ``cache/`` 区分。
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
import requests.certs

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
LOGS_DIR = ROOT / "logs"
CERTS_DIR = ROOT / "etl" / "certs"
STATE_DIR = ROOT / "state"
CA_BUNDLE_PATH = CACHE_DIR / "ca_bundle.pem"
for _d in (DATA_DIR, CACHE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

TZ = ZoneInfo("Asia/Shanghai")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 每个主机两次请求之间的最小间隔（秒）。腾讯行情是批量接口，稍快一点即可；
# 乐咕与中债登是单页面站点，放慢到 1 秒。
# 中证指数官网 index-perf 接口的限流是「突发计数」而不是平均速率：约 100 秒的窗口内第 13 个左右的请求起 HTTP 403，封 90–210 秒，
# 与 2–6 秒的间隔无关。观察（m4_work/_csindex_timeline.txt、probe_csindex_gap_*.txt）：2026-10-21 本机与 runner 8 秒内 12 个 → 第 13 个 403；
# 10-23 runner 2 秒间隔 12 个之后隔 76 秒的第 13 个仍 403；10-23 本机 2 秒间隔第 16 个 403；10-26 本机 3 ± 1 秒第 15 个（+40 s）403、
# 5 ± 1 秒第 13 个（+69 s）403，而 10 ± 1 秒与 12 ± 1 秒各 26 条增量全部通过（+243 s / +288 s）。因此间隔定为 12 秒（± 1 秒抖动，
# 见 HOST_JITTER）：12 个请求至少跨 121 秒，第 13 个落在窗口之外且留有余量；26 条序列约 5 分钟，比 5 秒 + 一次 210 秒的封禁更快也更礼貌。
# csindex_hist 另对 403 / 连接重置做 30 → 60 → 120 → 240 秒的长退避重试（2026-10-24 runner 上观察到第一个请求起连续 15 次
# Connection reset by peer，是 IP 级封禁而非突发限流，2–16 秒的短退避无济于事）。
# 东方财富数据中心、上海黄金交易所、中国货币网、世界银行文档站都是单页面接口，统一 1 秒。
HOST_MIN_INTERVAL: Dict[str, float] = {
    "default": 0.5,
    "qt.gtimg.cn": 0.3,
    "web.ifzq.gtimg.cn": 0.3,
    "www.csindex.com.cn": 12.0,
    "legulegu.com": 1.0,
    "yield.chinabond.com.cn": 1.0,
    "www.swsresearch.com": 0.8,
    "www.swhyresearch.com": 0.8,
    "query.sse.com.cn": 1.0,
    "datacenter-web.eastmoney.com": 1.0,
    "www.sge.com.cn": 1.0,
    "www.chinamoney.com.cn": 1.0,
    "thedocs.worldbank.org": 1.0,
}
# 主机间隔的随机抖动（秒，均匀分布 ±）：实际间隔 ∈ [gap − jitter, gap + jitter]
HOST_JITTER: Dict[str, float] = {
    "www.csindex.com.cn": 1.0,
}
MAX_ATTEMPTS = 5
BACKOFF_CAP = 60.0
# A 股收盘后的整点；在此之前抓到的「当天」数据视为盘中值
MARKET_CLOSE_HOUR = 16

# 网络 / 限流类错误文本的特征（见 is_network_error）：这些失败值得在全部面板跑完后再试一轮
_NETWORK_ERROR_RE = re.compile(
    r"重试 \d+ 次仍失败|HTTP (?:403|429|5\d\d)\b|Connection(?:Error|ResetError|RefusedError| aborted| reset)|"
    r"Timeout|timed out|RemoteDisconnected|网络接收错误|服务器连接失败|暂不可达|熔断|ReadTimeout|ConnectTimeout")


class PanelError(Exception):
    """面板级别的可预期失败（数据源格式变化、校验不过等）。"""


def is_network_error(msg: Optional[str]) -> bool:
    """错误文本是否属于网络 / 限流类（连接重置、超时、HTTP 403 / 429 / 5xx、重试耗尽、Baostock 网络错误、熔断）。

    用于编排器的第二轮：数据源格式变化、校验不过之类的错误再跑一遍结果不会变，只有这一类值得稍后重试。
    """
    return bool(msg) and _NETWORK_ERROR_RE.search(str(msg)) is not None


# --------------------------------------------------------------------------- 环境变量
def env_int(name: str, default: int) -> int:
    """读取整数环境变量；缺失、为空或不是整数时返回 ``default``。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def env_flag(name: str) -> bool:
    """布尔开关：环境变量取 ``"1"`` / ``"true"`` / ``"yes"``（不区分大小写）时为真，其它一律为假。"""
    raw = os.environ.get(name)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes")


# --------------------------------------------------------------------------- 时间
def now_sh() -> datetime:
    return datetime.now(TZ)


def iso_now() -> str:
    """ISO 8601，秒精度，带 +08:00 偏移。"""
    return now_sh().replace(microsecond=0).isoformat()


def today_str() -> str:
    return now_sh().strftime("%Y-%m-%d")


def today_compact() -> str:
    return now_sh().strftime("%Y%m%d")


def market_closed(now: Optional[datetime] = None) -> bool:
    """北京时间是否已过 MARKET_CLOSE_HOUR（16:00）。"""
    now = now if now is not None else now_sh()
    return now.astimezone(TZ).hour >= MARKET_CLOSE_HOUR


def cache_is_fresh(fetched_at: Optional[str], now: Optional[datetime] = None) -> bool:
    """同一天抓取的缓存能否直接复用。

    规则：抓取日期必须是今天；若抓取时已收盘则一直可用，若抓取时未收盘则只在当前仍未收盘时可用。
    于是「上午跑一次、晚上再跑一次」各联网一次，同一时段内重跑不联网。
    ``fetched_at`` 为 ISO 8601 字符串（``iso_now()`` 的输出）；缺失或无法解析按不新鲜处理。
    """
    if not fetched_at or not isinstance(fetched_at, str):
        return False
    try:
        t = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    t = t.astimezone(TZ)
    now = (now if now is not None else now_sh()).astimezone(TZ)
    if t.date() != now.date():
        return False
    return market_closed(t) or not market_closed(now)


def cache_age_days(fetched_at: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """缓存距今的天数（可带小数）；``fetched_at`` 缺失或无法解析返回 None。

    用于按「N 天」过期的慢变数据（如申万行业父级映射），与按交易日判断的 ``cache_is_fresh`` 互补。
    """
    if not fetched_at or not isinstance(fetched_at, str):
        return None
    try:
        t = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    now = (now if now is not None else now_sh()).astimezone(TZ)
    return (now - t.astimezone(TZ)).total_seconds() / 86400.0


# --------------------------------------------------------------------------- 日志
_LOGGER_READY: set = set()


def get_logger(name: str, verbose: bool = False) -> logging.Logger:
    """stderr + logs/build_YYYYMMDD.log 双写；verbose 时 DEBUG。"""
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    root = logging.getLogger("etl")
    if "etl" not in _LOGGER_READY:
        root.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = logging.FileHandler(LOGS_DIR / f"build_{today_compact()}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _LOGGER_READY.add("etl")
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(level)
    return logger


_log = logging.getLogger("etl.http")


def _fmt_exc(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:200]


# --------------------------------------------------------------------------- TLS
def ca_bundle_path() -> str:
    """返回 Session 应使用的 CA 文件路径。

    ``etl/certs/`` 没有 ``*.pem`` 时直接用 requests 自带的 certifi 文件；否则把 certifi 内容与
    每个 PEM 依次拼接成 ``cache/ca_bundle.pem``（先写 ``.tmp`` 再 ``os.replace``），
    仅在 bundle 缺失或比任一来源旧时重建。BEGIN/END 之外的注释文本会被 OpenSSL 忽略，
    所以 PEM 文件头部可以带说明。
    """
    certifi_path = Path(requests.certs.where())
    extra = sorted(CERTS_DIR.glob("*.pem")) if CERTS_DIR.is_dir() else []
    if not extra:
        return str(certifi_path)
    sources = [certifi_path] + extra
    if CA_BUNDLE_PATH.is_file():
        built_at = CA_BUNDLE_PATH.stat().st_mtime
        if all(p.stat().st_mtime <= built_at for p in sources):
            return str(CA_BUNDLE_PATH)
    CA_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CA_BUNDLE_PATH.with_suffix(".pem.tmp")
    with open(tmp, "wb") as fh:
        for p in sources:
            data = p.read_bytes()
            fh.write(data)
            if not data.endswith(b"\n"):
                fh.write(b"\n")
            fh.write(b"\n")
    os.replace(tmp, CA_BUNDLE_PATH)
    _log.debug("CA bundle 重建: certifi + %d 个 etl/certs PEM -> %s", len(extra), CA_BUNDLE_PATH)
    return str(CA_BUNDLE_PATH)


# --------------------------------------------------------------------------- HTTP
_session: Optional[requests.Session] = None
_last_request_at: Dict[str, float] = {}


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        s.verify = ca_bundle_path()
        _session = s
    return _session


def throttle_gap(host: str) -> float:
    """本次请求与该主机上一次请求之间应隔多少秒：HOST_MIN_INTERVAL ± HOST_JITTER（均匀抖动，不小于 0）。"""
    gap = HOST_MIN_INTERVAL.get(host, HOST_MIN_INTERVAL["default"])
    jitter = HOST_JITTER.get(host, 0.0)
    if jitter > 0:
        gap = max(0.0, gap + random.uniform(-jitter, jitter))
    return gap


def _throttle(host: str) -> None:
    """阻塞到距该主机上一次请求至少 ``throttle_gap(host)`` 秒。"""
    gap = throttle_gap(host)
    last = _last_request_at.get(host)
    if last is not None:
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _http(method: str, url: str, *, timeout: float, headers: Optional[Dict[str, str]],
          data: Optional[Dict[str, Any]] = None, max_attempts: Optional[int] = None) -> Tuple[bytes, Dict[str, str]]:
    """GET / POST 共用核心：单并发 + 主机限速 + 指数退避，返回 (body, headers)。

    429/5xx/网络错误重试；其它 4xx 立即抛 PanelError；TLS 证书校验失败不重试、立即抛 PanelError
    （证书问题不会在 16 秒内自愈）。POST 时 ``data`` 作为表单体提交。``max_attempts`` 默认 MAX_ATTEMPTS，
    调用方自己管重试时传 1。
    """
    host = urlsplit(url).hostname or "default"
    sess = _get_session()
    last_err: Optional[str] = None
    attempts = MAX_ATTEMPTS if max_attempts is None else max(1, int(max_attempts))
    for attempt in range(attempts):
        _throttle(host)
        _log.debug("%s %s (attempt %d/%d)", method, url, attempt + 1, attempts)
        try:
            if method == "POST":
                resp = sess.post(url, data=data, timeout=timeout, headers=headers)
            else:
                resp = sess.get(url, timeout=timeout, headers=headers)
        except requests.exceptions.SSLError as e:
            raise PanelError(f"TLS 证书校验失败 {url}: {_fmt_exc(e)}；"
                             f"若服务器未发中间证书，把中间证书 PEM 放进 etl/certs/ 后重试")
        except requests.RequestException as e:
            last_err = _fmt_exc(e)
        else:
            if resp.status_code == 200:
                return resp.content, dict(resp.headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                raise PanelError(f"HTTP {resp.status_code} {url}")
        if attempt < attempts - 1:
            delay = min(BACKOFF_CAP, 2.0 * (2 ** attempt))
            _log.warning("%s -> %s，%.0fs 后重试", url, last_err, delay)
            time.sleep(delay)
    raise PanelError(f"重试 {attempts} 次仍失败 ({last_err}) {url}")


def http_get(url: str, *, timeout: float = 30, headers: Optional[Dict[str, str]] = None,
             max_attempts: Optional[int] = None) -> Tuple[bytes, Dict[str, str]]:
    """GET 一个 URL，返回 (body, headers)。规则见 ``_http``。"""
    return _http("GET", url, timeout=timeout, headers=headers, max_attempts=max_attempts)


def http_post(url: str, *, data: Dict[str, Any], timeout: float = 30, headers: Optional[Dict[str, str]] = None
              ) -> Tuple[bytes, Dict[str, str]]:
    """POST 表单 ``data`` 到 URL，返回 (body, headers)。限速 / 重试 / 4xx / TLS 规则与 ``http_get`` 相同。"""
    return _http("POST", url, timeout=timeout, headers=headers, data=data)


# --------------------------------------------------------------------------- 月度缓存
def _as_sh(now: Optional[datetime]) -> datetime:
    """None → 当前北京时间；带时区的 datetime 转到北京时间；naive 的原样使用（测试里直接给日期）。"""
    if now is None:
        return now_sh()
    return now.astimezone(TZ) if now.tzinfo is not None else now


def monthly_fetch_month(now: Optional[datetime] = None, release_day: int = 1) -> str:
    """本次抓取应记为哪个月（``YYYY-MM``）：当日 ≥ ``release_day`` 记当月，否则记上月。"""
    now = _as_sh(now)
    y, m = now.year, now.month
    if now.day < release_day:
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return f"{y:04d}-{m:02d}"


def monthly_refresh_due(fetch_month: Optional[str], now: Optional[datetime] = None, release_day: int = 1) -> bool:
    """月度源是否该刷新：没抓过 → True；缓存记的月份 ≥ 当月 → False；否则当日 ≥ ``release_day``。"""
    if not fetch_month:
        return True
    now = _as_sh(now)
    current = f"{now.year:04d}-{now.month:02d}"
    if str(fetch_month) >= current:
        return False
    return now.day >= release_day


# --------------------------------------------------------------------------- JSON
def write_json_atomic(path: Path, obj: Any, compact: bool = False) -> None:
    """先写 ``*.json.tmp`` 再 ``os.replace``；``compact=True`` 时不缩进、分隔符不带空格。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.is_file():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- 缓存
def cache_path(sub: str, name: str) -> Path:
    d = CACHE_DIR / sub
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def cache_load(sub: str, name: str) -> Any:
    return read_json(cache_path(sub, name), default=None)


def cache_save(sub: str, name: str, obj: Any) -> None:
    write_json_atomic(cache_path(sub, name), obj)
