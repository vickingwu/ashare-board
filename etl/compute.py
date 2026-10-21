# -*- coding: utf-8 -*-
"""纯数值工具：日期序列合并、月末抽样、分位、年化、归一、收益拆解。

全部是无 I/O、无日志的纯函数，输入输出都是「平行数组」（dates[] 与 values[] 等长），
日期一律是 ``YYYY-MM-DD`` 字符串。面板模块把这些函数拼起来，离线单测只测这里。

口径约定：
* 月末 = 该自然月内最后一个有数据的日期（最后一个交易日），按 ``YYYY-MM`` 分组取最后一条；
* 「当前未完结的月」不算月末：序列最后一条若不是其所在月的最后一个工作日（周一至周五），
  这个月不进入月末序列，只作为「最新值」单独给出（``split_latest``）；
* 分位 = 含等号的份额：100 × #(历史值 ≤ 当前值) / 历史条数；
* 年化 = (期末/期初)^(1/年数) − 1，年数 = 天数 / 365.25；
* 收益拆解（加法）：全收益 = 股息 + 盈利 + 估值，其中 估值 = PE 期末/期初 − 1，
  盈利 = 价格涨幅 − 估值，股息 = 全收益涨幅 − 价格涨幅；
* 自然年收益：年 Y 的「年末收盘」= 日期落在 ``[Y-12-20, Y-12-31]`` 内最后一条非空记录
  （指数基日 1999-12-30 的 1000 点因此是 2000 年的基准）；年度涨跌幅 = 年末/上年末 − 1，
  两端任一缺失即 None，不用邻近年份补；年化只跨完整年度；
* 排名 ``rank_desc``：1 = 最大，相同值共享较小名次（1,2,2,4），None 不参与也不占名次。

技术指标口径（``ema`` / ``macd`` / ``rsi`` / ``realized_vol`` / ``rolling_percentile`` 等，全部 None 安全、平行数组）：
* EMA 种子：k = 2/(n+1)，以首个非 None 值为种子（不是前 n 个的均值），前导 None 保留；
  序列中间出现 None 视为输入错误抛 ``PanelError``，调用方先 ``drop_none`` 再算；
* MACD(12,26,9)：DIF = EMA12 − EMA26，DEA = EMA9(DIF)，柱 = 2 × (DIF − DEA)；
  下标 < ``MACD_WARMUP``（34，即第 35 个观测起）一律置 None 作为预热期；
* RSI(n=14) 用 Wilder 平滑：下标 n 处 = 100 − 100/(1 + 前 n 个涨跌的简单均值之比)，
  之后 avg = (avg × (n−1) + x) / n；平均跌幅为 0 时 RSI = 100；下标 < n 为 None；
* 已实现波动率 = 最近 n 个对数收益的样本标准差（n−1）× √250 × 100（年化按 250 个交易日）；
* 滚动分位 ``rolling_percentile``：含等号的名次 100 × #(w ≤ x) / N，窗口 = 含当前值的最近
  ``window`` 条记录，N = 窗口内非 None 的条数，N < ``min_obs`` 或当前值为 None 时为 None；
* 区间标签 ``band_label``：区间左闭右开 ``[lo, hi)``，最后一个区间右端闭合，None → None。
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from etl.common import PanelError

DateList = List[str]
ValueList = List[Optional[float]]


# --------------------------------------------------------------------------- 日期
def parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def is_weekend(s: str) -> bool:
    return parse_date(s).weekday() >= 5


def is_last_weekday_of_month(s: str) -> bool:
    """该日期之后、同一自然月内是否再无周一至周五。"""
    d = parse_date(s)
    nxt = d + timedelta(days=1)
    while nxt.month == d.month:
        if nxt.weekday() < 5:
            return False
        nxt += timedelta(days=1)
    return True


def month_key(s: str) -> str:
    return s[:7]


def years_between(d0: str, d1: str) -> float:
    return (parse_date(d1) - parse_date(d0)).days / 365.25


def shift_years(s: str, years: int) -> str:
    """同月同日往前/后推 N 年；2 月 29 日落到 2 月 28 日。"""
    d = parse_date(s)
    y = d.year + years
    try:
        return d.replace(year=y).isoformat()
    except ValueError:
        return d.replace(year=y, day=28).isoformat()


def first_index_on_or_after(dates: Sequence[str], target: str) -> Optional[int]:
    """首个 ≥ target 的下标；全部早于 target 时返回 None。dates 需升序。"""
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return None


def last_index_on_or_before(dates: Sequence[str], target: str) -> Optional[int]:
    """最后一个 ≤ target 的下标；全部晚于 target 时返回 None。dates 需升序。"""
    idx = None
    for i, d in enumerate(dates):
        if d <= target:
            idx = i
        else:
            break
    return idx


def assert_ascending(dates: Sequence[str], what: str = "日期") -> None:
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            raise PanelError(f"{what}不是严格递增: {dates[i - 1]} -> {dates[i]}")


# --------------------------------------------------------------------------- 序列
def merge_series(dates_a: Sequence[str], vals_a: Sequence, dates_b: Sequence[str], vals_b: Sequence
                 ) -> Tuple[DateList, list]:
    """按日期合并两段序列，b 覆盖 a 的同日值，结果按日期升序。"""
    merged: Dict[str, object] = {}
    for d, v in zip(dates_a, vals_a):
        merged[d] = v
    for d, v in zip(dates_b, vals_b):
        merged[d] = v
    keys = sorted(merged)
    return keys, [merged[k] for k in keys]


def drop_none(dates: Sequence[str], values: Sequence) -> Tuple[DateList, list]:
    out_d, out_v = [], []
    for d, v in zip(dates, values):
        if v is not None:
            out_d.append(d)
            out_v.append(v)
    return out_d, out_v


def month_end_sample(dates: Sequence[str], values: Sequence, complete_only: bool = False
                     ) -> Tuple[DateList, list]:
    """每个自然月取最后一条有值的记录。

    ``complete_only=True`` 时，若最后一条记录不是其所在月的最后一个工作日，则该（未完结的）月不保留。
    """
    last_by_month: Dict[str, Tuple[str, object]] = {}
    for d, v in zip(dates, values):
        if v is None:
            continue
        last_by_month[month_key(d)] = (d, v)
    keys = sorted(last_by_month)
    out_d = [last_by_month[k][0] for k in keys]
    out_v = [last_by_month[k][1] for k in keys]
    if complete_only and out_d and not is_last_weekday_of_month(out_d[-1]):
        out_d.pop()
        out_v.pop()
    return out_d, out_v


def split_latest(dates: Sequence[str], values: Sequence
                 ) -> Tuple[DateList, list, Optional[str], Optional[float]]:
    """拆成「已完结月份的月末序列」与「最新一条」。

    最新一条永远是最后一个有值的记录；只有当它恰好是其所在月的最后一个工作日时，
    它同时也作为该月的月末进入月末序列。
    """
    d2, v2 = drop_none(dates, values)
    if not d2:
        return [], [], None, None
    me_d, me_v = month_end_sample(d2, v2, complete_only=True)
    return me_d, me_v, d2[-1], v2[-1]


def normalize(values: Sequence[Optional[float]], base_index: int, base: float = 100.0) -> ValueList:
    """以 values[base_index] 为 base 归一；None 保留；基准值无效时全部返回 None。"""
    b = values[base_index] if 0 <= base_index < len(values) else None
    if b is None or b <= 0:
        return [None] * len(values)
    return [None if v is None else v / b * base for v in values]


def trailing_window(values: Sequence, n: int) -> Optional[list]:
    """最后 n 个值；不足 n 个时返回 None。"""
    if n <= 0 or len(values) < n:
        return None
    return list(values[-n:])


def rolling_mean(values: Sequence[Optional[float]], n: int) -> ValueList:
    """n 日简单均值（含当日）；前 n−1 个位置、或窗口内任一值为 None 时为 None。"""
    out: ValueList = []
    for i in range(len(values)):
        if i + 1 < n or n <= 0:
            out.append(None)
            continue
        window = values[i + 1 - n:i + 1]
        if any(v is None for v in window):
            out.append(None)
        else:
            out.append(sum(window) / n)
    return out


def safe_div_pct(a: Optional[float], b: Optional[float], nd: int = 4) -> Optional[float]:
    """a / b × 100 四舍五入到 nd 位；任一为 None 或 b 为 0 时返回 None。"""
    if a is None or b is None or b == 0:
        return None
    return round(a / b * 100.0, nd)


# --------------------------------------------------------------------------- 自然年
def last_index_per_year(dates: Sequence[str]) -> Dict[int, int]:
    """每个自然年最后一条记录的下标 {year: index}。dates 需升序。"""
    out: Dict[int, int] = {}
    for i, d in enumerate(dates):
        out[int(d[:4])] = i
    return out


def year_end_close(dates: Sequence[str], values: Sequence[Optional[float]], year: int
                   ) -> Optional[Tuple[str, float]]:
    """年 ``year`` 的年末收盘 (date, value)：日期在 [year-12-20, year-12-31] 内最后一条非空记录；没有则 None。"""
    lo, hi = f"{year:04d}-12-20", f"{year:04d}-12-31"
    found: Optional[Tuple[str, float]] = None
    for d, v in zip(dates, values):
        if d > hi:
            break
        if d >= lo and v is not None:
            found = (d, v)
    return found


def calendar_year_returns(dates: Sequence[str], values: Sequence[Optional[float]], years: Sequence[int]
                          ) -> ValueList:
    """各自然年涨跌幅（%）= 年末收盘 / 上年末收盘 − 1；任一端缺失或非正时为 None。"""
    out: ValueList = []
    for y in years:
        base = year_end_close(dates, values, y - 1)
        end = year_end_close(dates, values, y)
        if base is None or end is None or base[1] <= 0:
            out.append(None)
        else:
            out.append((end[1] / base[1] - 1.0) * 100.0)
    return out


def ytd_return(dates: Sequence[str], values: Sequence[Optional[float]], year: int) -> Optional[float]:
    """年初至今涨跌幅（%）：``year`` 内最后一条非空值 vs 上年末收盘；任一缺失为 None。"""
    base = year_end_close(dates, values, year - 1)
    if base is None or base[1] <= 0:
        return None
    last: Optional[float] = None
    prefix = f"{year:04d}-"
    for d, v in zip(dates, values):
        if d.startswith(prefix) and v is not None:
            last = v
        elif d > prefix + "12-31":
            break
    if last is None:
        return None
    return (last / base[1] - 1.0) * 100.0


def first_full_year(dates: Sequence[str], values: Sequence[Optional[float]], years: Sequence[int]
                    ) -> Optional[int]:
    """``years`` 中第一个有完整年度涨跌幅（基准与年末都存在）的年份；没有则 None。"""
    for y, r in zip(years, calendar_year_returns(dates, values, years)):
        if r is not None:
            return y
    return None


def cagr_over_years(dates: Sequence[str], values: Sequence[Optional[float]], last_year: int, n: int
                    ) -> Optional[float]:
    """最近 n 个完整自然年的年化（%）：从 ``last_year−n`` 年末到 ``last_year`` 年末；任一端缺失为 None。"""
    if n <= 0:
        return None
    base = year_end_close(dates, values, last_year - n)
    end = year_end_close(dates, values, last_year)
    if base is None or end is None:
        return None
    return cagr(base[1], end[1], float(n))


# --------------------------------------------------------------------------- 排名
def rank_desc(values: Sequence[Optional[float]]) -> List[Optional[int]]:
    """降序名次：1 = 最大；相同值共享较小名次（如 [3, None, 5, 3] → [2, None, 1, 2]）；None 保留。"""
    present = sorted((v for v in values if v is not None), reverse=True)
    rank_of: Dict[float, int] = {}
    for i, v in enumerate(present):
        if v not in rank_of:
            rank_of[v] = i + 1
    return [None if v is None else rank_of[v] for v in values]


def count_not_none(values: Sequence) -> int:
    return sum(1 for v in values if v is not None)


# --------------------------------------------------------------------------- 统计
def percentile_rank(values: Sequence[float], x: float) -> Optional[float]:
    """含等号分位：100 × #(v ≤ x) / n；空序列返回 None。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return 100.0 * sum(1 for v in vals if v <= x) / len(vals)


def cagr(v0: Optional[float], v1: Optional[float], years: float) -> Optional[float]:
    """年化收益率（%）；期限不足半年或数值无效时返回 None。"""
    if v0 is None or v1 is None or v0 <= 0 or v1 <= 0 or years < 0.5:
        return None
    return ((v1 / v0) ** (1.0 / years) - 1.0) * 100.0


def mean_std(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """样本均值与样本标准差（n−1）；n<2 时 std=0；空序列返回 (None, None)。"""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return None, None
    m = sum(vals) / n
    if n < 2:
        return m, 0.0
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, math.sqrt(var)


def median(values: Sequence[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def max_drawdown(values: Sequence[Optional[float]]) -> Optional[float]:
    """历史最大回撤（%，≤ 0）：任一时点相对此前最高点的最大跌幅。"""
    peak = None
    worst = 0.0
    seen = False
    for v in values:
        if v is None:
            continue
        seen = True
        if peak is None or v > peak:
            peak = v
        dd = (v / peak - 1.0) * 100.0
        if dd < worst:
            worst = dd
    return worst if seen else None


# --------------------------------------------------------------------------- 收益拆解
def decompose(p0: float, p1: float, tr0: float, tr1: float, pe0: float, pe1: float) -> dict:
    """加法拆解（%）：totalReturn = dividend + earnings + valuation。

    结果四舍五入到 4 位小数；earnings 由已四舍五入的 total − dividend − valuation 得出，
    所以 JSON 里的三个分项之和与 totalReturn 相差不超过浮点误差。
    """
    for name, v in (("价格期初", p0), ("价格期末", p1), ("全收益期初", tr0), ("全收益期末", tr1),
                    ("PE期初", pe0), ("PE期末", pe1)):
        if v is None or v <= 0:
            raise PanelError(f"收益拆解输入无效: {name}={v}")
    total = (tr1 / tr0 - 1.0) * 100.0
    price = (p1 / p0 - 1.0) * 100.0
    dividend = total - price
    valuation = (pe1 / pe0 - 1.0) * 100.0
    earnings = price - valuation
    if abs(dividend + earnings + valuation - total) > 1e-6:
        raise PanelError("收益拆解恒等式不成立")
    total_r = round(total, 4)
    dividend_r = round(dividend, 4)
    valuation_r = round(valuation, 4)
    earnings_r = round(total_r - dividend_r - valuation_r, 4)
    return {
        "totalReturn": total_r,
        "priceReturn": round(price, 4),
        "dividend": dividend_r,
        "valuation": valuation_r,
        "earnings": earnings_r,
    }


def align_by_month(series: Dict[str, Tuple[Sequence[str], Sequence]]
                   ) -> Tuple[List[str], Dict[str, list], Dict[str, DateList]]:
    """多条序列按 ``YYYY-MM`` 对齐（交集）。

    每条先做月末抽样；返回 (months[], {name: values[]}, {name: dates[]})，
    其中 dates 是各自序列在该月的实际月末日期。
    """
    sampled: Dict[str, Dict[str, Tuple[str, object]]] = {}
    common_months = None
    for name, (dates, values) in series.items():
        me_d, me_v = month_end_sample(dates, values)
        by_month = {month_key(d): (d, v) for d, v in zip(me_d, me_v)}
        sampled[name] = by_month
        keys = set(by_month)
        common_months = keys if common_months is None else (common_months & keys)
    months = sorted(common_months or [])
    out_vals = {name: [sampled[name][m][1] for m in months] for name in sampled}
    out_dates = {name: [sampled[name][m][0] for m in months] for name in sampled}
    return months, out_vals, out_dates


# --------------------------------------------------------------------------- 技术指标
MACD_WARMUP = 34


def _first_present(values: Sequence[Optional[float]]) -> Optional[int]:
    for i, v in enumerate(values):
        if v is not None:
            return i
    return None


def ema(values: Sequence[Optional[float]], n: int) -> ValueList:
    """指数移动平均：k = 2/(n+1)，以首个非 None 值为种子；前导 None 保留，中间出现 None 抛 PanelError。"""
    if n <= 0:
        raise PanelError(f"EMA 周期必须为正: {n}")
    k = 2.0 / (n + 1.0)
    out: ValueList = []
    prev: Optional[float] = None
    for i, v in enumerate(values):
        if v is None:
            if prev is not None:
                raise PanelError(f"EMA 输入第 {i} 位为 None（序列中间不允许缺失，请先 drop_none）")
            out.append(None)
            continue
        prev = float(v) if prev is None else prev + k * (float(v) - prev)
        out.append(prev)
    return out


def macd(values: Sequence[Optional[float]], fast: int = 12, slow: int = 26, signal: int = 9
         ) -> Tuple[ValueList, ValueList, ValueList]:
    """MACD → (dif, dea, hist)：DIF = EMA(fast) − EMA(slow)，DEA = EMA(signal)(DIF)，hist = 2 × (DIF − DEA)。

    自首个非 None 值起的前 ``MACD_WARMUP`` 个位置（即任何下标 < MACD_WARMUP）一律为 None 作为预热期。
    """
    e_fast = ema(values, fast)
    e_slow = ema(values, slow)
    dif: ValueList = [None if a is None or b is None else a - b for a, b in zip(e_fast, e_slow)]
    dea = ema(dif, signal)
    hist: ValueList = [None if a is None or b is None else 2.0 * (a - b) for a, b in zip(dif, dea)]
    first = _first_present(values)
    cut = len(values) if first is None else min(len(values), first + MACD_WARMUP)
    for i in range(cut):
        dif[i] = dea[i] = hist[i] = None
    return dif, dea, hist


def rsi(values: Sequence[Optional[float]], n: int = 14) -> ValueList:
    """RSI(n)，Wilder 平滑。

    下标 n（相对首个非 None 值）处 = 100 − 100/(1 + avgGain/avgLoss)，avgGain/avgLoss 为前 n 个涨跌幅
    的简单均值；之后 avg = (avg × (n−1) + x) / n。avgLoss 为 0 时 RSI = 100；前 n 个位置为 None；
    序列中间出现 None 抛 PanelError。
    """
    if n <= 0:
        raise PanelError(f"RSI 周期必须为正: {n}")
    out: ValueList = [None] * len(values)
    first = _first_present(values)
    if first is None:
        return out
    gains: List[float] = []
    losses: List[float] = []
    avg_gain: Optional[float] = None
    avg_loss: Optional[float] = None
    for i in range(first + 1, len(values)):
        v, prev = values[i], values[i - 1]
        if v is None or prev is None:
            raise PanelError(f"RSI 输入第 {i} 位为 None（序列中间不允许缺失，请先 drop_none）")
        x = float(v) - float(prev)
        g, l = (x, 0.0) if x > 0 else (0.0, -x)
        if avg_gain is None:
            gains.append(g)
            losses.append(l)
            if len(gains) < n:
                continue
            avg_gain = sum(gains) / n
            avg_loss = sum(losses) / n
        else:
            avg_gain = (avg_gain * (n - 1) + g) / n
            avg_loss = (avg_loss * (n - 1) + l) / n
        if avg_loss == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def log_returns(values: Sequence[Optional[float]]) -> ValueList:
    """对数收益 ``[None] + ln(v[i] / v[i−1])``；任一端为 None 或 ≤ 0 时为 None。"""
    if not values:
        return []
    out: ValueList = [None]
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        if a is None or b is None or a <= 0 or b <= 0:
            out.append(None)
        else:
            out.append(math.log(b / a))
    return out


def rolling_std(values: Sequence[Optional[float]], n: int, ddof: int = 1) -> ValueList:
    """n 期滚动标准差（含当期，自由度 n − ddof）；前 n−1 个位置、或窗口内任一值为 None 时为 None。"""
    out: ValueList = []
    if n - ddof <= 0:
        return [None] * len(values)
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
            continue
        window = values[i + 1 - n:i + 1]
        if any(v is None for v in window):
            out.append(None)
            continue
        m = sum(window) / n
        var = sum((v - m) ** 2 for v in window) / (n - ddof)
        out.append(math.sqrt(var))
    return out


def realized_vol(values: Sequence[Optional[float]], n: int = 20, periods: int = 250) -> ValueList:
    """已实现波动率（%，年化）= 最近 n 个对数收益的样本标准差 × √periods × 100。"""
    scale = math.sqrt(periods) * 100.0
    return [None if s is None else s * scale for s in rolling_std(log_returns(values), n)]


def pct_change_n(values: Sequence[Optional[float]], n: int) -> ValueList:
    """n 期变化率（%）= v[i] / v[i−n] − 1；i < n、任一端为 None 或 ≤ 0 时为 None。"""
    out: ValueList = []
    for i in range(len(values)):
        if i < n or n <= 0:
            out.append(None)
            continue
        a, b = values[i - n], values[i]
        if a is None or b is None or a <= 0 or b <= 0:
            out.append(None)
        else:
            out.append(100.0 * b / a - 100.0)
    return out


def ratio_to_rolling_mean(values: Sequence[Optional[float]], n: int) -> ValueList:
    """v / 其 n 期滚动均值（含当期）；均值为 None 或 0、或 v 为 None 时为 None。"""
    means = rolling_mean(values, n)
    return [None if v is None or m is None or m == 0 else v / m for v, m in zip(values, means)]


def rolling_percentile(values: Sequence[Optional[float]], window: int = 500, min_obs: int = 250) -> ValueList:
    """滚动含等号分位：100 × #(w ≤ x) / N，w 取含当前值的最近 ``window`` 条记录中的非 None 值。

    N = 窗口内非 None 条数；当前值为 None 或 N < ``min_obs`` 时为 None。用 bisect 维护有序窗口，O(N log W)。
    """
    out: ValueList = []
    if window <= 0:
        return [None] * len(values)
    sorted_window: List[float] = []
    for i, x in enumerate(values):
        if x is not None:
            bisect.insort(sorted_window, x)
        j = i - window
        if j >= 0 and values[j] is not None:
            del sorted_window[bisect.bisect_left(sorted_window, values[j])]
        n_obs = len(sorted_window)
        if x is None or n_obs < min_obs or n_obs == 0:
            out.append(None)
        else:
            out.append(100.0 * bisect.bisect_right(sorted_window, x) / n_obs)
    return out


def mean_of_available(values: Sequence[Optional[float]], min_count: int) -> Tuple[Optional[float], int]:
    """非 None 值的均值与个数 → (mean | None, count)；个数 < ``min_count`` 时均值为 None。"""
    vals = [v for v in values if v is not None]
    if len(vals) < min_count or not vals:
        return None, len(vals)
    return sum(vals) / len(vals), len(vals)


def band_label(x: Optional[float], bands: Sequence[Dict[str, object]]) -> Optional[str]:
    """按区间表取标签：``bands = [{lo, hi, label}]`` 升序，区间 ``[lo, hi)`` 左闭右开、最后一个右端闭合。

    x 为 None 或落在所有区间之外时返回 None。
    """
    if x is None or not bands:
        return None
    last = len(bands) - 1
    for i, b in enumerate(bands):
        lo, hi = float(b["lo"]), float(b["hi"])  # type: ignore[arg-type]
        if lo <= x < hi or (i == last and x == hi):
            return str(b["label"])
    return None
