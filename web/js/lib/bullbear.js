/*
 * A股看板 —— 个股 / 指数走势的牛熊与回撤引擎（window.AshareBullBear；Node 里 require 得到同一对象）。
 * 纯计算，不碰 DOM、不发请求。输入是升序日线 bars = [{t: 自 1970-01-01 起的天数, c: 收盘}]。
 *
 *   analyze(bars, T, basis)  → {n, c, t, nav, dd, days, total, cagr, episodes[], maxDD, phases[], basis, vol, curDD, curPeakIdx}
 *     nav   以首根为 1 的净值；cagr 区间年化（区间 < 20 天为 NaN）；
 *     dd    距前高回撤序列（≤ 0），episodes 每段回撤 {peak, trough, depth, end(修复日下标 | null)}，maxDD 其中最深的一段；
 *     phases ZigZag 牛熊阶段 {s, e, bull, ongoing, first, ret, days, ann(不满一年 null), lo, hi, extI, fromExt}；
 *     vol   日对数收益的年化标准差（√242），curDD 当前距前高。
 *   ZigZag 规则：自阶段极值反向运行 ≥ T 即确认转折；跌破本轮牛市起点（上一个谷）也结束牛市；basis 'auto' 按区间长度选
 *   月末 / 周末 / 日收盘找转折（≥ 6 年 M、≥ 2 年 W、否则 D），再在日线上精确定位峰谷；进行中的阶段必须已自起点走出 ≥ T，
 *   否则并入上一阶段；首末两段按窗口截断（first 标记起点被截断）。
 *   horizons(bars) 多周期涨跌幅（1周 … 全程，10 项）；yearlyReturns(bars) 分年涨跌幅；sliceByRange(bars, 'all' | '10y' …)。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AshareBullBear = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DAY = 86400000;

  // ---------------------------------------------------------------- 日期（UTC 天数 t）
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function parseYMD(s) { return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10)) / DAY; }
  function tToDate(t) { return new Date(t * DAY); }
  function fmtDate(t) { var d = tToDate(t); return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()); }
  function ymd(y, m, d) { return Date.UTC(y, m - 1, d) / DAY; }
  // t 往后 k 个月（k 可为负），同日；该月没有这一天时退到月末
  function addMonths(t, k) {
    var d = tToDate(t);
    var y = d.getUTCFullYear(), m = d.getUTCMonth() + k, day = d.getUTCDate();
    y += Math.floor(m / 12);
    m = ((m % 12) + 12) % 12;
    var last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return Date.UTC(y, m, Math.min(day, last)) / DAY;
  }

  // 去空格、全角转半角（"万  科Ａ" → "万科A"）
  function cleanName(s) {
    return String(s || '').replace(/\s+/g, '').replace(/[\uFF01-\uFF5E]/g, function (ch) { return String.fromCharCode(ch.charCodeAt(0) - 0xFEE0); });
  }

  // ---------------------------------------------------------------- 二分：以 t 为界的最近一根
  function idxOnOrBefore(bars, t) {
    var lo = 0, hi = bars.length - 1, ans = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (bars[mid].t <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }
  function idxOnOrAfter(bars, t) {
    var lo = 0, hi = bars.length - 1, ans = bars.length;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (bars[mid].t >= t) { ans = mid; hi = mid - 1; } else lo = mid + 1;
    }
    return ans;
  }

  // ---------------------------------------------------------------- ZigZag
  // 在收盘序列 c 上找转折点下标（峰、谷交替）。第一段：从起点同时跟踪最高 / 最低，先出现 ≥ T 的反向运行者决定初始趋势；
  // 之后：牛市中自阶段高点回落 ≥ T，或跌破本轮起点（上一个谷），确认转牛为熊；熊市对称。
  function zigzagPivots(c, T) {
    var n = c.length, pivots = [];
    if (n < 2) return pivots;
    var trend = 0, extI = 0, baseI = 0, maxI = 0, minI = 0, k = 1;
    for (; k < n; k++) {
      if (c[k] > c[maxI]) maxI = k;
      if (c[k] < c[minI]) minI = k;
      if (c[k] <= c[maxI] * (1 - T)) { trend = -1; if (maxI > 0) pivots.push(maxI); baseI = maxI; extI = k; break; }
      if (c[k] >= c[minI] * (1 + T)) { trend = 1; if (minI > 0) pivots.push(minI); baseI = minI; extI = k; break; }
    }
    if (trend === 0) return pivots;
    for (k = k + 1; k < n; k++) {
      if (trend === 1) {
        if (c[k] >= c[extI]) extI = k;
        else if (c[k] <= c[extI] * (1 - T) || c[k] < c[baseI]) { pivots.push(extI); baseI = extI; trend = -1; extI = k; }
      } else {
        if (c[k] <= c[extI]) extI = k;
        else if (c[k] >= c[extI] * (1 + T) || c[k] > c[baseI]) { pivots.push(extI); baseI = extI; trend = 1; extI = k; }
      }
    }
    return pivots;
  }

  // 采样下标：D 每日；W 每周最后一个交易日；M 每月最后一个交易日（末根必含）
  function sampleIndices(bars, basis) {
    var n = bars.length, out = [], i;
    if (basis === 'D') { for (i = 0; i < n; i++) out.push(i); return out; }
    var key = basis === 'W'
      ? function (t) { return Math.floor((t - 4) / 7); }
      : function (t) { var d = tToDate(t); return d.getUTCFullYear() * 12 + d.getUTCMonth(); };
    for (i = 0; i < n; i++) if (i === n - 1 || key(bars[i].t) !== key(bars[i + 1].t)) out.push(i);
    return out;
  }

  // [lo, hi] 内的极值下标（isPeak 取最大，否则最小）；区间为空返回 fallback；相等时取最早的
  function argExt(c, lo, hi, isPeak, fallback) {
    if (hi < lo) return fallback;
    var best = lo;
    for (var i = lo; i <= hi; i++) if (isPeak ? c[i] > c[best] : c[i] < c[best]) best = i;
    return best;
  }

  // 先在采样序列上识别转折，再在日线上精确定位峰谷（相邻转折之间的日收盘极值）
  function segmentPhases(bars, c, T, basis) {
    var n = c.length;
    if (n < 2) return [];
    var idx = sampleIndices(bars, basis);
    var sc = idx.map(function (i) { return c[i]; });
    var sp = zigzagPivots(sc, T);
    var refined = [], types = [];
    if (sp.length) {
      var nextRef = sp.length > 1 ? sc[sp[1]] : sc[sc.length - 1];
      var isPeak = sc[sp[0]] > nextRef;
      var prev = -1;
      for (var k = 0; k < sp.length; k++) {
        var hi = k + 1 < sp.length ? idx[sp[k + 1]] - 1 : n - 1;
        var best = argExt(c, prev + 1, hi, isPeak, idx[sp[k]]);
        refined.push(best); types.push(isPeak); prev = best; isPeak = !isPeak;
      }
      // 进行中的阶段必须自其日线起点起已走出一次 ≥ T 的行情，否则并入上一阶段（并重新定位上一阶段的极值）
      while (refined.length) {
        var last = refined.length - 1, R = refined[last], peakStart = types[last];
        var ext = c[R];
        for (var i = R + 1; i < n; i++) ext = peakStart ? Math.min(ext, c[i]) : Math.max(ext, c[i]);
        if (peakStart ? ext <= c[R] * (1 - T) : ext >= c[R] * (1 + T)) break;
        refined.pop(); types.pop();
        if (refined.length) {
          var j = refined.length - 1;
          refined[j] = argExt(c, (j > 0 ? refined[j - 1] : -1) + 1, n - 1, types[j], refined[j]);
        }
      }
    }
    var bounds = [0].concat(refined, [n - 1]);
    var phases = [];
    for (var b = 0; b + 1 < bounds.length; b++) {
      var s = bounds[b], e = bounds[b + 1];
      if (e <= s) continue;
      phases.push({ s: s, e: e, bull: c[e] >= c[s], ongoing: b + 2 === bounds.length, first: b === 0 && refined.length > 0 });
    }
    return phases;
  }

  function autoBasis(days) { return days >= 365.25 * 6 ? 'M' : days >= 365.25 * 2 ? 'W' : 'D'; }

  // ---------------------------------------------------------------- 主计算
  function analyze(bars, T, basisOpt) {
    var n = bars.length, i;
    var c = bars.map(function (b) { return b.c; }), t = bars.map(function (b) { return b.t; });
    var nav = c.map(function (x) { return x / c[0]; });
    var days = t[n - 1] - t[0];
    var total = nav[n - 1] - 1;
    var cagr = days >= 20 ? Math.pow(nav[n - 1], 365.25 / days) - 1 : NaN;

    // 回撤
    var dd = new Array(n), peakIdx = new Array(n);
    var runMax = -Infinity, runMaxI = 0;
    for (i = 0; i < n; i++) {
      if (c[i] >= runMax) { runMax = c[i]; runMaxI = i; }
      dd[i] = c[i] / runMax - 1;
      peakIdx[i] = runMaxI;
    }
    var episodes = [], cur = null;
    for (i = 0; i < n; i++) {
      if (dd[i] < 0) {
        if (!cur) cur = { peak: peakIdx[i], trough: i, depth: dd[i], end: null };
        else if (dd[i] < cur.depth) { cur.depth = dd[i]; cur.trough = i; }
      } else if (cur) { cur.end = i; episodes.push(cur); cur = null; }
    }
    if (cur) episodes.push(cur);
    var maxDD = null;
    episodes.forEach(function (ep) { if (!maxDD || ep.depth < maxDD.depth) maxDD = ep; });

    // 阶段
    var basis = basisOpt === 'auto' || !basisOpt ? autoBasis(days) : basisOpt;
    var phases = segmentPhases(bars, c, T, basis).map(function (p) {
      var ret = c[p.e] / c[p.s] - 1, d = t[p.e] - t[p.s];
      var lo = Infinity, hi = -Infinity, loI = p.s, hiI = p.s;
      for (var k = p.s; k <= p.e; k++) {
        var y = c[k] / c[p.s] - 1;
        if (y < lo) { lo = y; loI = k; }
        if (y > hi) { hi = y; hiI = k; }
      }
      var extI = p.bull ? hiI : loI;
      p.ret = ret;
      p.days = d;
      p.ann = d >= 365 ? Math.pow(1 + ret, 365.25 / d) - 1 : null;
      p.lo = lo;
      p.hi = hi;
      p.extI = extI;
      p.fromExt = c[p.e] / c[extI] - 1;
      return p;
    });

    // 波动率：日对数收益的总体标准差 × √242
    var sum = 0, sum2 = 0, m = 0;
    for (i = 1; i < n; i++) { var r = Math.log(c[i] / c[i - 1]); sum += r; sum2 += r * r; m++; }
    var vol = m > 5 ? Math.sqrt(Math.max(0, sum2 / m - (sum / m) * (sum / m))) * Math.sqrt(242) : NaN;

    return { n: n, c: c, t: t, nav: nav, dd: dd, days: days, total: total, cagr: cagr, episodes: episodes, maxDD: maxDD,
      phases: phases, basis: basis, vol: vol, curDD: dd[n - 1], curPeakIdx: peakIdx[n - 1] };
  }

  // 分年涨跌幅：每年最后一根 ÷ 上一年最后一根 − 1；首年没有上一年收盘时以当年首根为基（fromListing），
  // 最后一年未结束或首年为 partial
  function yearlyReturns(all) {
    var out = [];
    var prevClose = null;
    for (var i = 0; i < all.length; i++) {
      var y = tToDate(all[i].t).getUTCFullYear();
      var isLast = i === all.length - 1 || tToDate(all[i + 1].t).getUTCFullYear() !== y;
      if (isLast) {
        var basePrice = prevClose === null ? all[idxOnOrAfter(all, ymd(y, 1, 1))].c : prevClose;
        out.push({ y: y, v: all[i].c / basePrice - 1, partial: prevClose === null || (i === all.length - 1), fromListing: prevClose === null });
        prevClose = all[i].c;
      }
    }
    return out;
  }

  // 时间窗：'all' 全部；'<N>y' 末根往前 N 年（按月回推）起的第一根；至少保留 2 根
  function sliceByRange(bars, range) {
    if (range === 'all') return bars;
    var yrs = parseInt(range, 10);
    var lastT = bars[bars.length - 1].t;
    var i = idxOnOrAfter(bars, addMonths(lastT, -12 * yrs));
    return bars.slice(Math.max(0, Math.min(i, bars.length - 2)));
  }

  // ---------------------------------------------------------------- 多周期涨跌幅（本看板自己的 10 项）
  // 基准 = 该期限之前（含）最近一根；年初至今以上一年 12-31 之前的最近一根为基；满一年给年化
  var HORIZONS = [
    { k: '1周', back: function (last) { return last.t - 7; } },
    { k: '1月', back: function (last) { return addMonths(last.t, -1); } },
    { k: '3月', back: function (last) { return addMonths(last.t, -3); } },
    { k: '6月', back: function (last) { return addMonths(last.t, -6); } },
    { k: '年初至今', back: function (last) { return ymd(tToDate(last.t).getUTCFullYear() - 1, 12, 31); }, missing: '本年上市' },
    { k: '1年', back: function (last) { return addMonths(last.t, -12); } },
    { k: '3年', back: function (last) { return addMonths(last.t, -36); } },
    { k: '5年', back: function (last) { return addMonths(last.t, -60); } },
    { k: '10年', back: function (last) { return addMonths(last.t, -120); } },
    { k: '全程', back: null }
  ];

  function horizons(bars) {
    var n = bars.length;
    if (!n) return [];
    var last = bars[n - 1];
    return HORIZONS.map(function (h) {
      var base;
      if (h.back === null) base = bars[0];
      else { var i = idxOnOrBefore(bars, h.back(last)); base = i >= 0 ? bars[i] : null; }
      if (!base) return { k: h.k, v: null, baseDate: null, days: null, ann: null, note: h.missing || '历史不足' };
      var v = last.c / base.c - 1, days = last.t - base.t;
      return { k: h.k, v: v, baseDate: fmtDate(base.t), days: days, ann: days >= 365 ? Math.pow(1 + v, 365.25 / days) - 1 : null, note: null };
    });
  }

  // 阶段表的行
  function phaseRows(A) {
    return A.phases.map(function (p) {
      return { from: fmtDate(A.t[p.s]), to: fmtDate(A.t[p.e]), bull: p.bull, ret: p.ret, days: p.days, ann: p.ann, ongoing: p.ongoing, first: p.first };
    });
  }

  // 头部读数
  function summary(A) {
    var n = A.n, md = A.maxDD;
    var bulls = A.phases.filter(function (p) { return p.bull; }).length;
    return {
      first: fmtDate(A.t[0]), last: fmtDate(A.t[n - 1]), lastClose: A.c[n - 1], multiple: A.nav[n - 1], total: A.total, cagr: A.cagr, days: A.days,
      maxDD: md ? { depth: md.depth, peak: fmtDate(A.t[md.peak]), trough: fmtDate(A.t[md.trough]), recovery: md.end === null ? null : fmtDate(A.t[md.end]) } : null,
      curDD: A.curDD, curPeak: fmtDate(A.t[A.curPeakIdx]), vol: A.vol, nPhases: A.phases.length, nBull: bulls, nBear: A.phases.length - bulls, basis: A.basis
    };
  }

  return {
    DAY: DAY,
    parseYMD: parseYMD, tToDate: tToDate, fmtDate: fmtDate, ymd: ymd, addMonths: addMonths, cleanName: cleanName,
    idxOnOrBefore: idxOnOrBefore, idxOnOrAfter: idxOnOrAfter,
    zigzagPivots: zigzagPivots, sampleIndices: sampleIndices, argExt: argExt, segmentPhases: segmentPhases, autoBasis: autoBasis,
    analyze: analyze, yearlyReturns: yearlyReturns, sliceByRange: sliceByRange,
    horizons: horizons, phaseRows: phaseRows, summary: summary
  };
});
