# -*- coding: utf-8 -*-
"""ETL 公共设施：路径、时区、日志、礼貌抓取、原子写 JSON、按日缓存。

设计约束（见 README「数据源与口径」）：
* 单线程、单并发：所有 HTTP 请求都经过 ``http_get``，按主机维护最小间隔；
* 指数退避重试：5 次，2s → 4s → 8s → 16s（上限 60s）；
* 失败只影响当前面板：抛 ``PanelError`` 或任何异常，由编排器隔离；
* 写文件一律先写 ``*.json.tmp`` 再 ``os.replace``，前端永远读不到半截文件；
* 长历史数据源（指数日行情、PE、国债收益率）的缓存按「当天 16:00 前/后」判断能否复用
  （``cache_is_fresh``）：收盘前抓的缓存只在收盘前复用，收盘后抓的当天一直复用。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
LOGS_DIR = ROOT / "logs"
for _d in (DATA_DIR, CACHE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

TZ = ZoneInfo("Asia/Shanghai")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 每个主机两次请求之间的最小间隔（秒）。腾讯行情是批量接口，稍快一点即可；
# 乐咕与中债登是单页面站点，放慢到 1 秒。
HOST_MIN_INTERVAL: Dict[str, float] = {
    "default": 0.5,
    "qt.gtimg.cn": 0.3,
    "web.ifzq.gtimg.cn": 0.3,
    "www.csindex.com.cn": 0.6,
    "legulegu.com": 1.0,
    "yield.chinabond.com.cn": 1.0,
}
MAX_ATTEMPTS = 5
BACKOFF_CAP = 60.0
# A 股收盘后的整点；在此之前抓到的「当天」数据视为盘中值
MARKET_CLOSE_HOUR = 16


class PanelError(Exception):
    """面板级别的可预期失败（数据源格式变化、校验不过等）。"""


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
        _session = s
    return _session


def _throttle(host: str) -> None:
    """阻塞到距该主机上一次请求至少 HOST_MIN_INTERVAL 秒。"""
    gap = HOST_MIN_INTERVAL.get(host, HOST_MIN_INTERVAL["default"])
    last = _last_request_at.get(host)
    if last is not None:
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def http_get(url: str, *, timeout: float = 30, headers: Optional[Dict[str, str]] = None
             ) -> Tuple[bytes, Dict[str, str]]:
    """GET 一个 URL，返回 (body, headers)。

    单并发 + 主机限速 + 指数退避。429/5xx/网络错误重试；其它 4xx 立即抛 PanelError。
    """
    host = urlsplit(url).hostname or "default"
    sess = _get_session()
    last_err: Optional[str] = None
    for attempt in range(MAX_ATTEMPTS):
        _throttle(host)
        _log.debug("GET %s (attempt %d/%d)", url, attempt + 1, MAX_ATTEMPTS)
        try:
            resp = sess.get(url, timeout=timeout, headers=headers)
        except requests.RequestException as e:
            last_err = _fmt_exc(e)
        else:
            if resp.status_code == 200:
                return resp.content, dict(resp.headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
            else:
                raise PanelError(f"HTTP {resp.status_code} {url}")
        if attempt < MAX_ATTEMPTS - 1:
            delay = min(BACKOFF_CAP, 2.0 * (2 ** attempt))
            _log.warning("%s -> %s，%.0fs 后重试", url, last_err, delay)
            time.sleep(delay)
    raise PanelError(f"重试 {MAX_ATTEMPTS} 次仍失败 ({last_err}) {url}")


# --------------------------------------------------------------------------- JSON
def write_json_atomic(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
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
