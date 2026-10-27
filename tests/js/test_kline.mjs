// web/js/lib/kline.js 的离线单测（node --test tests/js）：URL 语法、响应解析、窗口规划、重叠拼接（真实两页 + 合成基准漂移）、
// 盘中 K 线剔除、bars 转换、代码推断、以及注入 fetchJson / storage 的 loadKline 编排（不联网）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const K = require('../../web/js/lib/kline.js');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const fixture = (name) => JSON.parse(readFileSync(path.join(HERE, '..', 'fixtures', name), 'utf8'));
const P1 = fixture('tencent_kline_sh600519_hfq_p1.json');     // 641 行 2024-03-05 → 2026-10-27（含 10-27 盘中）
const P2 = fixture('tencent_kline_sh600519_hfq_p2.json');     // 640 行 2021-07-14 → 2024-03-05
const IDX = fixture('tencent_kline_sh000300_day_p1.json');    // 指数：键名 day
const EMPTY = fixture('tencent_kline_empty.json');            // sh999999：day: []

test('klineUrl 的 param 语法', () => {
  assert.equal(K.klineUrl('sh600519', 'hfq', '', ''), 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,640,hfq');
  assert.equal(K.klineUrl('sh000300', '', '2005-01-01', '2007-01-16'), 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000300,day,2005-01-01,2007-01-16,640,');
  assert.equal(K.klineUrl('sz000001', 'qfq'), 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz000001,day,,,640,qfq');
});

test('parseResponse：股票后复权页', () => {
  const r = K.parseResponse(P1, 'sh600519');
  assert.equal(r.key, 'hfqday');
  assert.equal(r.rows.length, 641);
  assert.equal(r.rows[0][0], '2024-03-05');
  assert.equal(r.rows[640][0], '2026-10-27');
  assert.equal(r.name, '贵州茅台');
  assert.equal(r.quote.time, '20261027134058');
  assert.equal(typeof r.quote.price, 'number');
  assert.equal(r.prec, '10481.111');
});

test('parseResponse：指数只有 day 键；空结果 rows 0；异常体抛 BAD_RESPONSE', () => {
  const idx = K.parseResponse(IDX, 'sh000300');
  assert.equal(idx.key, 'day');
  assert.equal(idx.rows.length, 641);
  assert.equal(idx.name, '沪深300');
  const empty = K.parseResponse(EMPTY, 'sh999999');
  assert.equal(empty.rows.length, 0);
  assert.equal(empty.key, 'day');
  assert.throws(() => K.parseResponse({ code: 1, data: {} }, 'sh600519'), (e) => e.code === 'BAD_RESPONSE');
  assert.throws(() => K.parseResponse(P1, 'sh000001'), (e) => e.code === 'BAD_RESPONSE');
});

test('effectiveAdj', () => {
  assert.equal(K.effectiveAdj('hfqday'), 'hfq');
  assert.equal(K.effectiveAdj('qfqday'), 'qfq');
  assert.equal(K.effectiveAdj('day'), 'none');
});

test('planWindows：两年窗口 + 15 天重叠，最新窗口止于首日 + 15 天', () => {
  const w = K.planWindows('2024-03-05');
  assert.equal(w.length, 10);
  assert.deepEqual(w.map((x) => x.start), ['2005-01-01', '2007-01-01', '2009-01-01', '2011-01-01', '2013-01-01', '2015-01-01', '2017-01-01', '2019-01-01', '2021-01-01', '2023-01-01']);
  assert.equal(w[0].end, '2007-01-16');
  assert.equal(w[8].end, '2023-01-16');
  assert.equal(w[9].end, '2024-03-20');
  assert.deepEqual(K.planWindows('2004-06-01'), []);
  assert.deepEqual(K.planWindows('2005-01-01'), []);
  assert.deepEqual(K.planWindows('2005-01-10'), [{ start: '2005-01-01', end: '2005-01-25' }]);
  assert.deepEqual(K.planWindows('2013-05-01', '2010-01-01').map((x) => x.start), ['2010-01-01', '2012-01-01']);
});

test('spliceWindows：真实两页重叠 1 日、比值 1.0、1280 个唯一日期、严格升序', () => {
  const sp = K.spliceWindows([P2.data.sh600519.hfqday, P1.data.sh600519.hfqday]);
  assert.equal(sp.items.length, 1280);
  assert.equal(sp.rebased.length, 0);
  assert.equal(sp.gaps, 0);
  assert.equal(sp.items[0].d, '2021-07-14');
  assert.equal(sp.items[1279].d, '2026-10-27');
  assert.equal(sp.items[1279].c, +P1.data.sh600519.hfqday[640][2]);
  for (let i = 1; i < sp.items.length; i++) assert.ok(sp.items[i].d > sp.items[i - 1].d);
  assert.equal(new Set(sp.items.map((x) => x.d)).size, 1280);
});

test('spliceWindows：合成基准漂移（旧页 × 1.5）被重叠比值校正回最新页基准', () => {
  const scaled = P2.data.sh600519.hfqday.map((r) => [r[0], r[1], String(+r[2] * 1.5), r[3], r[4], r[5]]);
  const ref = K.spliceWindows([P2.data.sh600519.hfqday, P1.data.sh600519.hfqday]);
  const sp = K.spliceWindows([scaled, P1.data.sh600519.hfqday]);
  assert.equal(sp.rebased.length, 1);
  assert.equal(sp.rebased[0].at, '2024-03-05');
  assert.ok(Math.abs(sp.rebased[0].ratio - 1 / 1.5) < 1e-12);
  assert.equal(sp.items.length, ref.items.length);
  for (let i = 0; i < ref.items.length; i++) {
    assert.equal(sp.items[i].d, ref.items[i].d);
    assert.ok(Math.abs(sp.items[i].c - ref.items[i].c) < 1e-9, `${ref.items[i].d}: ${sp.items[i].c} vs ${ref.items[i].c}`);
  }
});

test('spliceWindows：无重叠日直接相接并计 gaps；空窗口跳过；三窗口链式拼接', () => {
  const a = [['2020-01-02', '1', '10', '1', '1', '1'], ['2020-01-03', '1', '11', '1', '1', '1']];
  const b = [['2020-02-03', '1', '12', '1', '1', '1'], ['2020-02-04', '1', '13', '1', '1', '1']];
  const sp = K.spliceWindows([a, [], b]);
  assert.equal(sp.gaps, 1);
  assert.deepEqual(sp.items.map((x) => x.d), ['2020-01-02', '2020-01-03', '2020-02-03', '2020-02-04']);
  // 三窗口：c 与 b 重叠且基准不同（×2），b 与 a 重叠且基准相同 → a、b 都要乘到 c 的基准上
  const c = [['2020-02-04', '1', '26', '1', '1', '1'], ['2020-02-05', '1', '28', '1', '1', '1']];
  const b2 = [['2020-01-03', '1', '11', '1', '1', '1'], ['2020-02-03', '1', '12', '1', '1', '1'], ['2020-02-04', '1', '13', '1', '1', '1']];
  const sp3 = K.spliceWindows([a, b2, c]);
  assert.deepEqual(sp3.items.map((x) => x.d), ['2020-01-02', '2020-01-03', '2020-02-03', '2020-02-04', '2020-02-05']);
  assert.deepEqual(sp3.items.map((x) => x.c), [20, 22, 24, 26, 28]);
  // 每个旧窗口都与「已换到最新基准的累计结果」比：b2 相对 c 偏 2 倍，a 相对（已换基的）b2 也偏 2 倍 → 两条记录
  assert.deepEqual(sp3.rebased, [{ at: '2020-02-04', ratio: 2 }, { at: '2020-01-03', ratio: 2 }]);
  assert.throws(() => K.spliceWindows([[['2020-01-03', '1', '1', '1', '1', '1'], ['2020-01-03', '1', '1', '1', '1', '1']]]), (e) => e.code === 'BAD_RESPONSE');
});

test('dropIntraday：行情时间同日且 < 15:00 才剔除最后一根', () => {
  const items = [{ d: '2026-10-24', c: 1 }, { d: '2026-10-27', c: 2 }];
  assert.equal(K.dropIntraday(items, '20261027134058').length, 1);
  assert.equal(K.dropIntraday(items, '20261027150301').length, 2);
  assert.equal(K.dropIntraday(items, '20261028093000').length, 2);
  assert.equal(K.dropIntraday(items, null).length, 2);
  assert.equal(K.dropIntraday([], '20261027134058').length, 0);
});

test('toBars：t 为 UTC 天数，非正收盘跳过并计数，startCap 之前的行不要', () => {
  const r = K.toBars([{ d: '2004-12-31', c: 5 }, { d: '2024-03-05', c: 10586.913 }, { d: '2024-03-06', c: 0 }, { d: '2024-03-07', c: NaN }, { d: '2024-03-08', c: 1 }]);
  assert.equal(r.bars.length, 2);
  assert.equal(r.bars[0].t, 19787);
  assert.equal(r.bars[0].c, 10586.913);
  assert.equal(r.dropped, 2);
  assert.equal(K.toBars([{ d: '2004-12-31', c: 5 }], '2004-01-01').bars.length, 1);
});

test('inferSymbol：市场推断', () => {
  assert.deepEqual(K.inferSymbol('600519'), { symbol: 'sh600519', code: '600519', market: 'sh' });
  assert.deepEqual(K.inferSymbol('000001'), { symbol: 'sz000001', code: '000001', market: 'sz' });
  assert.deepEqual(K.inferSymbol('430047'), { symbol: 'bj430047', code: '430047', market: 'bj' });
  assert.deepEqual(K.inferSymbol('sh000001'), { symbol: 'sh000001', code: '000001', market: 'sh' });
  assert.deepEqual(K.inferSymbol('SZ399006'), { symbol: 'sz399006', code: '399006', market: 'sz' });
  assert.equal(K.inferSymbol('abc'), null);
  assert.equal(K.inferSymbol('60051'), null);
  assert.equal(K.inferSymbol(''), null);
});

test('cleanName / cacheKey / 缓存读写与配额淘汰', () => {
  assert.equal(K.cleanName(' 万  科Ａ '), '万科A');
  assert.equal(K.cacheKey('sh600519', 'hfq'), 'ashare.kline.sh600519.hfq');
  assert.equal(K.cacheKey('sh000300', ''), 'ashare.kline.sh000300.none');
  const store = memoryStorage();
  assert.equal(K.readCache(store, 'x'), null);
  assert.ok(K.writeCache(store, K.cacheKey('sh1', 'hfq'), { bars: [{ t: 1, c: 1 }], fetchedAt: '2026-01-01T00:00:00Z' }));
  assert.equal(K.readCache(store, K.cacheKey('sh1', 'hfq')).bars.length, 1);
  assert.deepEqual(K.readCache(store, K.cacheKey('sh1', 'hfq')), { bars: [{ t: 1, c: 1 }], fetchedAt: '2026-01-01T00:00:00Z' });
  assert.equal(K.readCache(store, 'ashare.kline.sh1.none'), null);
  // 配额：第一次 setItem 抛错 → 删掉自己的旧键再写
  const quota = memoryStorage({ failFirst: true });
  quota.setItemRaw('other.key', '1');
  quota.setItemRaw(K.cacheKey('sh0', 'hfq'), JSON.stringify({ bars: [{ t: 1, c: 1 }], fetchedAt: '2025-01-01' }));
  assert.ok(K.writeCache(quota, K.cacheKey('sh2', 'hfq'), { bars: [{ t: 2, c: 2 }], fetchedAt: '2026-01-01' }));
  assert.equal(quota.getItem('other.key'), '1');
  assert.equal(quota.getItem(K.cacheKey('sh0', 'hfq')), null);
  assert.ok(quota.getItem(K.cacheKey('sh2', 'hfq')));
});

test('mapLimit：并发不超过 limit，结果按序', async () => {
  let active = 0, peak = 0;
  const out = await K.mapLimit([5, 1, 3, 2, 4], 2, (ms) => new Promise((res) => { active++; peak = Math.max(peak, active); setTimeout(() => { active--; res(ms * 10); }, ms); }));
  assert.deepEqual(out, [50, 10, 30, 20, 40]);
  assert.equal(peak, 2);
  await assert.rejects(K.mapLimit([1, 2], 2, (x) => x === 2 ? Promise.reject(new Error('boom')) : Promise.resolve(x)), /boom/);
});

// ---- loadKline 编排：按 URL 提供 fixture 的注入 fetchJson -------------------------------------------------------------
function serverFor(sym, pagesByWindow, latest, opts = {}) {
  const log = [];
  let inFlight = 0, peak = 0;
  const fetchJson = (url) => {
    log.push(url);
    const m = /param=([^,]+),day,([^,]*),([^,]*),640,([^,&]*)/.exec(url);
    inFlight++; peak = Math.max(peak, inFlight);
    return new Promise((res, rej) => setTimeout(() => {
      inFlight--;
      if (opts.failUrl && url.includes(opts.failUrl) && !(opts.failOnce && log.filter((u) => u.includes(opts.failUrl)).length > 1)) { rej(Object.assign(new Error('HTTP 503'), { code: 'NETWORK' })); return; }
      const body = m[2] === '' && m[3] === '' ? latest : (pagesByWindow[m[2]] || { code: 0, data: { [sym]: { day: [], qt: {}, prec: '' } } });
      res(JSON.parse(JSON.stringify(body)));
    }, 2));
  };
  return { fetchJson, log, peakRef: () => peak };
}
function memoryStorage(opts = {}) {
  const m = new Map(); let failed = false;
  return {
    get length() { return m.size; }, key: (i) => Array.from(m.keys())[i] ?? null, getItem: (k) => m.has(k) ? m.get(k) : null,
    setItem(k, v) { if (opts.failFirst && !failed) { failed = true; throw new Error('QuotaExceededError'); } m.set(k, v); },
    setItemRaw(k, v) { m.set(k, v); }, removeItem: (k) => { m.delete(k); }
  };
}
// 把 P2（2021-07-14 → 2024-03-05）切成「两年窗口」页：2021-01-01 窗口 = 2021-07-14 … 2023-01-16，2023-01-01 窗口 = 2023-01-01 … 2024-03-20
function windowPage(rows, start, end) {
  return { code: 0, data: { sh600519: { hfqday: rows.filter((r) => r[0] >= start && r[0] <= end), qt: P1.data.sh600519.qt, prec: '' } } };
}

test('loadKline：最新页 + 窗口分批（≤ 2 在途）→ 空窗口即停 → 拼接 → 剔盘中 → bars；第二次命中会话缓存', async () => {
  const rows = P2.data.sh600519.hfqday;
  const pages = { '2023-01-01': windowPage(rows, '2023-01-01', '2024-03-20'), '2021-01-01': windowPage(rows, '2021-01-01', '2023-01-16') };
  const srv = serverFor('sh600519', pages, P1);
  const store = memoryStorage();
  const r = await K.loadKline('600519', 'hfq', { fetchJson: srv.fetchJson, storage: store, batchGapMs: 0, retryDelayMs: 0 });
  assert.equal(r.symbol, 'sh600519');
  assert.equal(r.name, '贵州茅台');
  assert.equal(r.adj, 'hfq');
  assert.equal(r.requested, 'hfq');
  assert.equal(r.fromCache, false);
  // 最新页 1 + 第一批 2 个窗口（2023 / 2021）+ 第二批 2 个（2019 / 2017，2019 为空 → 停）= 5 次请求，10 个窗口里只取了 4 个
  assert.equal(r.requests, 5);
  assert.equal(srv.log.length, 5);
  assert.equal(r.windows, 10);
  assert.equal(K.CONCURRENCY, 2);
  assert.ok(srv.peakRef() <= 2);
  assert.equal(srv.log[0], K.klineUrl('sh600519', 'hfq', '', ''));
  assert.equal(r.first, '2021-07-14');
  assert.equal(r.last, P1.data.sh600519.hfqday[639][0]);   // 10-27 13:40 的盘中 K 线被剔除，最后一根是前一交易日
  assert.equal(r.last, '2026-10-26');
  assert.equal(r.intradayDropped, 1);
  assert.equal(r.n, 1279);
  assert.equal(r.bars[0].t, 18822);             // 2021-07-14
  assert.equal(r.bars[r.n - 1].c, +P1.data.sh600519.hfqday[639][2]);
  assert.equal(r.rebased.length, 0);
  assert.equal(r.gaps, 0);
  assert.ok(r.cached);
  const again = await K.loadKline('sh600519', 'hfq', { fetchJson: () => Promise.reject(new Error('must not fetch')), storage: store });
  assert.equal(again.fromCache, true);
  assert.equal(again.requests, 0);
  assert.equal(again.n, 1279);
  assert.equal(again.last, '2026-10-26');
});

test('loadKline：空结果 → NO_DATA；窗口失败一次自动重试、两次失败 → NETWORK；指数走 day 键 → adj none', async () => {
  const empty = serverFor('sh999999', {}, EMPTY);
  await assert.rejects(K.loadKline('sh999999', 'hfq', { fetchJson: empty.fetchJson, storage: null }), (e) => e.code === 'NO_DATA' && /没有找到 sh999999/.test(e.message));
  assert.equal(empty.log.length, 1);
  await assert.rejects(K.loadKline('abc', 'hfq', { fetchJson: empty.fetchJson, storage: null }), (e) => e.code === 'NO_DATA');

  const rows = P2.data.sh600519.hfqday;
  const pages = { '2023-01-01': windowPage(rows, '2023-01-01', '2024-03-20'), '2021-01-01': windowPage(rows, '2021-01-01', '2023-01-16') };
  const once = serverFor('sh600519', pages, P1, { failUrl: '2023-01-01,2024-03-20', failOnce: true });
  const r = await K.loadKline('sh600519', 'hfq', { fetchJson: once.fetchJson, storage: null, batchGapMs: 0, retryDelayMs: 0 });
  assert.equal(r.requests, 6);                   // 失败的那一窗多算 1 次
  assert.equal(r.n, 1279);
  const twice = serverFor('sh600519', pages, P1, { failUrl: '2023-01-01,2024-03-20' });
  await assert.rejects(K.loadKline('sh600519', 'hfq', { fetchJson: twice.fetchJson, storage: null, batchGapMs: 0, retryDelayMs: 0 }), (e) => e.code === 'NETWORK');
  assert.equal(twice.log.filter((u) => u.includes('2023-01-01,2024-03-20')).length, 2);   // 只重试一次

  const idx = serverFor('sh000300', {}, IDX);
  const ri = await K.loadKline('sh000300', '', { fetchJson: idx.fetchJson, storage: null, startCap: '2024-03-05' });
  assert.equal(ri.adj, 'none');
  assert.equal(ri.name, '沪深300');
  assert.equal(ri.requests, 1);                  // startCap == 首日 → 不需要窗口
  assert.equal(ri.symbol, 'sh000300');
});
