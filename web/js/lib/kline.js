/*
 * A股看板 —— 浏览器端取腾讯行情日线（window.AshareKline；Node 里 require 得到同一对象，便于离线单测）。
 *
 * 接口：GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<sym>,day,<start>,<end>,<count>,<fq>
 *   fq = hfq 后复权 | qfq 前复权 | '' 不复权；响应带 Access-Control-Allow-Origin: *，所以直接 fetch（无 JSONP、无代理）；
 *   行 = [日期, 开, 收, 高, 低, 量(, 分红送转说明)]，键名按复权方式是 hfqday / qfqday / day（指数永远是 day）；
 *   data.<sym>.qt.<sym> 是行情数组：[1] 名称、[3] 最新价、[30] 行情时间 YYYYMMDDHHmmss。
 * 取数规则：count 上限 640 行，所以 1 次「最新页」（含当日盘中 K 线）+ 按两年日历窗口从 2005-01-01（或上市日）往前补齐，
 *   窗口之间重叠 15 天，每批 2 路并行、批间停 250 ms、整窗为空即停（上市前）；窗口按重叠日的收盘比值中位数拼接
 *   （后复权基准以最新页为准），盘中（行情时间当天且 < 15:00）的最后一根不计入；结果按 symbol + 复权 缓存在 sessionStorage，
 *   只在本次会话内有效。腾讯的 WAF 对短时间内的突发请求返回 501（无 CORS 头，浏览器里表现为 CORS 错误），所以失败只重试一次、
 *   停 1.5 秒再试，仍失败就把错误交给面板提示用户稍后再查。
 * 纯函数（klineUrl / parseResponse / planWindows / spliceWindows / dropIntraday / toBars / inferSymbol …）全部可离线测；
 *   loadKline(symbol, adj, opts) 是唯一发请求的入口，opts.fetchJson / storage 可注入。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(root);
  else root.AshareKline = factory(root);
})(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var ENDPOINT = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get';
  var PAGE = 640;                 // 接口的单次上限（count=2000 仍只回 640 行）
  var START_CAP = '2005-01-01';   // 历史起点上限（与其它面板的 2005 起一致）
  var WINDOW_YEARS = 2;           // 固定日历窗口长度（两年 ≈ 490 个交易日 < 640）
  var OVERLAP_DAYS = 15;          // 相邻窗口重叠的日历天数
  var CONCURRENCY = 2;            // 同时在途的窗口请求数（3 路并行连续多次查询后触发过腾讯 WAF 的 501 限流，2026-10-27）
  var BATCH_GAP_MS = 250;         // 两批窗口请求之间的停顿
  var RETRY_DELAY_MS = 1500;      // 单个窗口失败后重试前的停顿
  var TRANSPORT = 'fetch';        // 'fetch'（直接跨域 fetch）| 'script'（?_var= 脚本注入的备用通道，只在线上 CORS 异常时切换）
  var CACHE_PREFIX = 'ashare.kline.';
  var DAY = 86400000;

  // ---------------------------------------------------------------- 小工具
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function ymdOf(ms) { var d = new Date(ms); return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()); }
  function msOf(ymd) { return Date.UTC(+ymd.slice(0, 4), +ymd.slice(5, 7) - 1, +ymd.slice(8, 10)); }
  function addDays(ymd, n) { return ymdOf(msOf(ymd) + n * DAY); }
  function cleanName(s) {
    return String(s || '').replace(/\s+/g, '').replace(/[\uFF01-\uFF5E]/g, function (ch) { return String.fromCharCode(ch.charCodeAt(0) - 0xFEE0); });
  }
  function tagged(message, code, extra) {
    var e = new Error(message);
    e.code = code;
    if (extra) Object.keys(extra).forEach(function (k) { e[k] = extra[k]; });
    return e;
  }
  function median(arr) {
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var n = a.length;
    return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2;
  }

  // ---------------------------------------------------------------- 请求 URL 与响应解析
  function klineUrl(symbol, adj, start, end) {
    return ENDPOINT + '?param=' + [symbol, 'day', start || '', end || '', PAGE, adj || ''].join(',');
  }

  // 'hfqday' → 'hfq'，'qfqday' → 'qfq'，'day' → 'none'
  function effectiveAdj(key) { return key === 'hfqday' ? 'hfq' : key === 'qfqday' ? 'qfq' : 'none'; }

  // → {rows, key, name, quote: {price, time}, prec}；code ≠ 0 或没有该代码的数据块 → 抛 BAD_RESPONSE
  function parseResponse(json, symbol) {
    if (!json || json.code !== 0 || !json.data || !json.data[symbol]) {
      throw tagged('腾讯行情返回异常（code ' + (json && json.code !== undefined ? json.code : '?') + '）', 'BAD_RESPONSE');
    }
    var d = json.data[symbol];
    var key = ['hfqday', 'qfqday', 'day'].filter(function (k) { return Array.isArray(d[k]); })[0] || null;
    var rows = key ? d[key] : [];
    var qt = d.qt && d.qt[symbol];
    var name = qt && qt[1] ? cleanName(qt[1]) : null;
    var price = qt && qt[3] !== undefined && qt[3] !== '' ? +qt[3] : null;
    var time = qt && qt[30] ? String(qt[30]) : null;
    return { rows: rows, key: key, name: name, quote: { price: isNaN(price) ? null : price, time: time }, prec: d.prec !== undefined ? d.prec : null };
  }

  // ---------------------------------------------------------------- 窗口规划与拼接
  // 从 startCap 起每 WINDOW_YEARS 年一个窗口，直到最新页的首日 firstDate 之前；窗口 end = 下一窗口起点 + OVERLAP_DAYS，
  // 最新的窗口 end = firstDate + OVERLAP_DAYS。firstDate ≤ startCap 时不需要窗口 → []
  function planWindows(firstDate, startCap) {
    startCap = startCap || START_CAP;
    var out = [];
    if (!(firstDate > startCap)) return out;
    var y0 = +startCap.slice(0, 4);
    for (var k = 0; ; k++) {
      var start = (y0 + k * WINDOW_YEARS) + '-01-01';
      if (!(start < firstDate)) break;
      var nextStart = (y0 + (k + 1) * WINDOW_YEARS) + '-01-01';
      out.push({ start: start, end: addDays(nextStart < firstDate ? nextStart : firstDate, OVERLAP_DAYS) });
    }
    return out;
  }

  // windows = 行数组的数组（每个升序，整体 旧 → 新，允许空数组）→ {items: [{d, c}], rebased: [{at, ratio}], gaps}
  // 从最新窗口往前拼：重叠日的 收盘(新) / 收盘(旧) 取中位数，偏离 1 超过 1e-6 就把旧窗口整体乘上去（后复权基准以最新页为准），
  // 旧窗口里日期 ≥ 新窗口首日的行丢弃；没有重叠日（跨窗口停牌）直接相接并计入 gaps。结果必须严格升序。
  function spliceWindows(windows) {
    var rebased = [], gaps = 0;
    var acc = null;
    for (var w = windows.length - 1; w >= 0; w--) {
      var rows = windows[w] || [];
      if (!rows.length) continue;
      var items = rows.map(function (r) { return { d: String(r[0]), c: +r[2] }; });
      if (acc === null) { acc = items; continue; }
      var firstNew = acc[0].d;
      var newer = {};
      for (var i = 0; i < acc.length && acc[i].d <= items[items.length - 1].d; i++) newer[acc[i].d] = acc[i].c;
      var ratios = [];
      items.forEach(function (it) { if (newer[it.d] !== undefined && it.c > 0 && newer[it.d] > 0) ratios.push(newer[it.d] / it.c); });
      var ratio = 1;
      if (ratios.length) {
        ratio = median(ratios);
        if (Math.abs(ratio - 1) > 1e-6) rebased.push({ at: firstNew, ratio: ratio });
        else ratio = 1;
      } else gaps++;
      var older = [];
      items.forEach(function (it) { if (it.d < firstNew) older.push(ratio === 1 ? it : { d: it.d, c: it.c * ratio }); });
      acc = older.concat(acc);
    }
    acc = acc || [];
    for (var j = 1; j < acc.length; j++) {
      if (!(acc[j].d > acc[j - 1].d)) throw tagged('拼接后的日线不是严格升序：' + acc[j - 1].d + ' → ' + acc[j].d, 'BAD_RESPONSE');
    }
    return { items: acc, rebased: rebased, gaps: gaps };
  }

  // 行情时间 quoteTime（YYYYMMDDHHmmss）与最后一根同一天且早于 15:00 → 那一根是盘中值，去掉
  function dropIntraday(items, quoteTime) {
    if (!items.length || !quoteTime || !/^\d{14}$/.test(String(quoteTime))) return items;
    var q = String(quoteTime);
    var lastDate = items[items.length - 1].d.replace(/-/g, '');
    if (q.slice(0, 8) === lastDate && q.slice(8) < '150000') return items.slice(0, -1);
    return items;
  }

  // [{d, c}] → {bars: [{t, c}], dropped}；t = 自 1970-01-01 起的天数（与引擎的 parseYMD 相同）；非正 / NaN 收盘与 startCap 之前的行跳过
  function toBars(items, startCap) {
    startCap = startCap || START_CAP;
    var bars = [], dropped = 0;
    items.forEach(function (it) {
      if (it.d < startCap) return;
      if (!(it.c > 0)) { dropped++; return; }
      bars.push({ t: msOf(it.d) / DAY, c: it.c });
    });
    return { bars: bars, dropped: dropped };
  }

  // 'sh600519' / '600519' / 'SZ000001' → {symbol, code, market}；6 位数字无前缀时按首位推断市场：5/6/9 沪、4/8 北、其余 深
  function inferSymbol(input) {
    var m = /^\s*(sh|sz|bj)?(\d{6})\s*$/i.exec(String(input || ''));
    if (!m) return null;
    var code = m[2];
    var market = m[1] ? m[1].toLowerCase() : (/^[569]/.test(code) ? 'sh' : /^[48]/.test(code) ? 'bj' : 'sz');
    return { symbol: market + code, code: code, market: market };
  }

  // ---------------------------------------------------------------- 会话缓存
  function cacheKey(symbol, adj) { return CACHE_PREFIX + symbol + '.' + (adj || 'none'); }

  function readCache(storage, key) {
    if (!storage) return null;
    try {
      var raw = storage.getItem(key);
      if (!raw) return null;
      var obj = JSON.parse(raw);
      return obj && Array.isArray(obj.bars) && obj.bars.length ? obj : null;
    } catch (e) { return null; }
  }

  // 写入失败（配额）时按 fetchedAt 从旧到新删掉自己的键再试一次；仍失败就放弃（缓存不是正确性的一部分）
  function writeCache(storage, key, obj) {
    if (!storage) return false;
    var text = JSON.stringify(obj);
    try { storage.setItem(key, text); return true; } catch (e) { /* 配额或禁用 */ }
    try {
      var ours = [];
      for (var i = 0; i < storage.length; i++) {
        var k = storage.key(i);
        if (k && k.indexOf(CACHE_PREFIX) === 0) {
          var at = null;
          try { at = (JSON.parse(storage.getItem(k)) || {}).fetchedAt || null; } catch (e2) { at = null; }
          ours.push({ k: k, at: at || '' });
        }
      }
      ours.sort(function (a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : 0; });
      ours.forEach(function (o) { storage.removeItem(o.k); });
      storage.setItem(key, text);
      return true;
    } catch (e3) { return false; }
  }

  // ---------------------------------------------------------------- 传输
  function fetchJsonFetch(url) {
    return fetch(url, { mode: 'cors', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw tagged('HTTP ' + r.status, 'NETWORK');
      return r.text();
    }, function (e) {
      throw tagged('网络错误：' + (e && e.message ? e.message : e), 'NETWORK');
    }).then(function (text) {
      try { return JSON.parse(text); } catch (e) { throw tagged('响应不是 JSON', 'BAD_RESPONSE'); }
    });
  }

  // 备用通道：?_var=<varName> 让接口返回 `varName={...}` 的 JS，注入 <script> 后读 window[varName]（20 秒超时）
  function fetchJsonViaScript(url, varName) {
    return new Promise(function (resolve, reject) {
      var name = varName || ('ashareKline' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8));
      var s = document.createElement('script');
      var done = false;
      var timer = setTimeout(function () { finish(); reject(tagged('请求超时', 'NETWORK')); }, 20000);
      function finish() {
        done = true;
        clearTimeout(timer);
        if (s.parentNode) s.parentNode.removeChild(s);
      }
      s.onload = function () {
        if (done) return;
        var data = root[name];
        try { delete root[name]; } catch (e) { root[name] = undefined; }
        finish();
        if (data === undefined) reject(tagged('脚本未赋值 ' + name, 'BAD_RESPONSE')); else resolve(data);
      };
      s.onerror = function () { if (done) return; finish(); reject(tagged('脚本加载失败', 'NETWORK')); };
      s.src = url + (url.indexOf('?') >= 0 ? '&' : '?') + '_var=' + encodeURIComponent(name);
      document.head.appendChild(s);
    });
  }

  // 有限并发：按 items 顺序启动，最多 limit 个在途；返回与 items 等长的结果数组（任一失败即整体 reject）
  function mapLimit(items, limit, fn) {
    return new Promise(function (resolve, reject) {
      var results = new Array(items.length), next = 0, active = 0, failed = false;
      function launch() {
        if (failed) return;
        if (next >= items.length && active === 0) { resolve(results); return; }
        while (active < limit && next < items.length) {
          (function (i) {
            active++;
            Promise.resolve().then(function () { return fn(items[i], i); }).then(function (v) {
              results[i] = v; active--; launch();
            }, function (e) { if (!failed) { failed = true; reject(e); } });
          })(next++);
        }
      }
      launch();
    });
  }

  // ---------------------------------------------------------------- 编排
  // → Promise<{symbol, code, market, name, adj(实际), requested, bars, first, last, n, dropped, rebased, gaps, requests, fromCache, quote, fetchedAt, cached}>
  // 错误带 .code：NO_DATA（接口没有该代码的日线）| NETWORK（请求失败，已重试一次）| BAD_RESPONSE（返回体异常）
  function loadKline(symbol, adj, opts) {
    opts = opts || {};
    var sym = inferSymbol(symbol);
    if (!sym) return Promise.reject(tagged('没有找到 ' + symbol + ' 的日线数据（代码是否正确？）', 'NO_DATA'));
    adj = adj === 'qfq' ? 'qfq' : adj === 'hfq' ? 'hfq' : '';
    var startCap = opts.startCap || START_CAP;
    var concurrency = opts.concurrency || CONCURRENCY;
    var storage = opts.storage !== undefined ? opts.storage : (typeof sessionStorage !== 'undefined' ? sessionStorage : null);
    var transport = opts.transport || TRANSPORT;
    var fetchJson = opts.fetchJson || (transport === 'script' ? function (u) { return fetchJsonViaScript(u); } : fetchJsonFetch);
    var key = cacheKey(sym.symbol, adj);
    var cached = readCache(storage, key);
    if (cached) {
      var hit = {};
      Object.keys(cached).forEach(function (k) { hit[k] = cached[k]; });
      hit.fromCache = true;
      hit.requests = 0;
      return Promise.resolve(hit);
    }
    var requests = 0;
    var retryDelay = opts.retryDelayMs !== undefined ? opts.retryDelayMs : RETRY_DELAY_MS;
    // 一次请求 + 失败后停顿一下再重试一次（被限流时立刻重试只会更糟）；返回解析后的 {rows, ...}
    function getPage(start, end) {
      requests++;
      return fetchJson(klineUrl(sym.symbol, adj, start, end)).catch(function (e) {
        if (e && e.code === 'BAD_RESPONSE') throw e;
        return new Promise(function (r) { setTimeout(r, retryDelay); }).then(function () {
          requests++;
          return fetchJson(klineUrl(sym.symbol, adj, start, end));
        });
      }).then(function (json) { return parseResponse(json, sym.symbol); });
    }
    return getPage('', '').then(function (latest) {
      if (!latest.rows.length) throw tagged('没有找到 ' + sym.symbol + ' 的日线数据（代码是否正确？）', 'NO_DATA');
      var windows = planWindows(String(latest.rows[0][0]), startCap);
      var pages = new Array(windows.length);
      // 从最新窗口往旧的分批取；一批里出现整窗为空就不再往前（已到上市前）
      var order = [];
      for (var i = windows.length - 1; i >= 0; i--) order.push(i);
      var batches = [];
      for (var b = 0; b < order.length; b += concurrency) batches.push(order.slice(b, b + concurrency));
      var gap = opts.batchGapMs !== undefined ? opts.batchGapMs : BATCH_GAP_MS;
      var chain = Promise.resolve(false);
      batches.forEach(function (batch, bi) {
        chain = chain.then(function (stop) {
          if (stop) return true;
          var pause = bi > 0 && gap > 0 ? new Promise(function (r) { setTimeout(r, gap); }) : Promise.resolve();
          return pause.then(function () {
            return mapLimit(batch, concurrency, function (wi) {
              return getPage(windows[wi].start, windows[wi].end).then(function (p) { pages[wi] = p.rows; });
            });
          }).then(function () {
            return batch.some(function (wi) { return !pages[wi].length; });
          });
        });
      });
      return chain.then(function () {
        var all = pages.map(function (p) { return p || []; });
        all.push(latest.rows);
        var sp = spliceWindows(all);
        var items = dropIntraday(sp.items, latest.quote.time);
        var tb = toBars(items, startCap);
        if (!tb.bars.length) throw tagged('没有找到 ' + sym.symbol + ' 的日线数据（代码是否正确？）', 'NO_DATA');
        var res = {
          symbol: sym.symbol, code: sym.code, market: sym.market, name: latest.name,
          adj: effectiveAdj(latest.key), requested: adj, bars: tb.bars,
          first: items.length ? items[0].d : null, last: items.length ? items[items.length - 1].d : null,
          n: tb.bars.length, dropped: tb.dropped, intradayDropped: sp.items.length - items.length,
          rebased: sp.rebased, gaps: sp.gaps, requests: requests, windows: windows.length, fromCache: false,
          quote: latest.quote, fetchedAt: new Date().toISOString()
        };
        res.cached = writeCache(storage, key, res);
        return res;
      });
    });
  }

  return {
    ENDPOINT: ENDPOINT, PAGE: PAGE, START_CAP: START_CAP, WINDOW_YEARS: WINDOW_YEARS, OVERLAP_DAYS: OVERLAP_DAYS,
    CONCURRENCY: CONCURRENCY, BATCH_GAP_MS: BATCH_GAP_MS, RETRY_DELAY_MS: RETRY_DELAY_MS, TRANSPORT: TRANSPORT, CACHE_PREFIX: CACHE_PREFIX, DAY: DAY,
    klineUrl: klineUrl, parseResponse: parseResponse, effectiveAdj: effectiveAdj, planWindows: planWindows,
    spliceWindows: spliceWindows, dropIntraday: dropIntraday, toBars: toBars, inferSymbol: inferSymbol, cleanName: cleanName,
    cacheKey: cacheKey, readCache: readCache, writeCache: writeCache,
    fetchJsonFetch: fetchJsonFetch, fetchJsonViaScript: fetchJsonViaScript, mapLimit: mapLimit, median: median, addDays: addDays,
    loadKline: loadKline
  };
});
