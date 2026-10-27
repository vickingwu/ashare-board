// web/js/lib/bullbear.js 的离线单测（node --test tests/js）：手算的 10 根 K 线（净值 / 回撤 / 最大回撤 / ZigZag 阶段）、
// 多周期涨跌幅的基准日与年化规则、分年涨跌幅、时间窗切片边界、日期工具。与原引擎的逐值比对在 m5_work/parity_m5.js。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const B = require('../../web/js/lib/bullbear.js');
const near = (a, b, eps = 1e-12) => assert.ok(Math.abs(a - b) <= eps, `${a} vs ${b}`);

// 连续日历日的 bars（basis D 下与交易日无关）
function dailyBars(startYMD, closes) {
  const t0 = B.parseYMD(startYMD);
  return closes.map((c, i) => ({ t: t0 + i, c }));
}
// 每个日历日一根、收盘单调递增的三年序列
function everyDay(startYMD, endYMD, step = 0.1) {
  const out = []; let t = B.parseYMD(startYMD); const te = B.parseYMD(endYMD); let i = 0;
  while (t <= te) { out.push({ t, c: 100 + i * step }); t++; i++; }
  return out;
}

test('日期工具：parseYMD / fmtDate / addMonths / ymd', () => {
  assert.equal(B.parseYMD('2024-03-05'), 19787);
  assert.equal(B.fmtDate(19787), '2024-03-05');
  assert.equal(B.fmtDate(B.addMonths(B.parseYMD('2024-03-31'), -1)), '2024-02-29');
  assert.equal(B.fmtDate(B.addMonths(B.parseYMD('2024-01-31'), 1)), '2024-02-29');
  assert.equal(B.fmtDate(B.addMonths(B.parseYMD('2026-10-27'), -120)), '2016-10-27');
  assert.equal(B.ymd(2024, 9, 23), B.parseYMD('2024-09-23'));
  assert.equal(B.autoBasis(365.25 * 6), 'M');
  assert.equal(B.autoBasis(365.25 * 6 - 1), 'W');
  assert.equal(B.autoBasis(365.25 * 2 - 1), 'D');
  assert.equal(B.cleanName(' 万  科Ａ '), '万科A');
});

test('idxOnOrBefore / idxOnOrAfter', () => {
  const bars = dailyBars('2024-01-01', [1, 1, 1, 1, 1]).filter((_, i) => i !== 2);   // t: 0,1,3,4 相对
  const t0 = B.parseYMD('2024-01-01');
  assert.equal(B.idxOnOrBefore(bars, t0 + 2), 1);
  assert.equal(B.idxOnOrAfter(bars, t0 + 2), 2);
  assert.equal(B.idxOnOrBefore(bars, t0 - 1), -1);
  assert.equal(B.idxOnOrAfter(bars, t0 + 99), bars.length);
});

test('zigzagPivots：手算的转折下标', () => {
  assert.deepEqual(B.zigzagPivots([10, 12, 9, 13, 15, 11, 12, 16, 10, 14], 0.2), [1, 2, 4, 5, 7, 8]);
  assert.deepEqual(B.zigzagPivots([10, 11, 10.5, 11.2], 0.2), []);     // 从未走出 ≥ 20%
  assert.deepEqual(B.zigzagPivots([10], 0.2), []);
  // 跌破本轮牛市起点也结束牛市：10 → 13（确认牛）→ 11.5（回落 11.5%，但未破起点 10）→ 9.9（破起点）
  assert.deepEqual(B.zigzagPivots([10, 13, 11.5, 9.9], 0.2), [1]);
});

test('analyze：10 根手算（净值、回撤、3 段回撤与最大回撤、7 个阶段、首末截断标记）', () => {
  const bars = dailyBars('2024-01-02', [10, 12, 9, 13, 15, 11, 12, 16, 10, 14]);
  const A = B.analyze(bars, 0.2, 'D');
  assert.equal(A.n, 10);
  assert.equal(A.basis, 'D');
  assert.deepEqual(A.nav, [1, 1.2, 0.9, 1.3, 1.5, 1.1, 1.2, 1.6, 1.0, 1.4]);
  near(A.total, 0.4);
  assert.ok(Number.isNaN(A.cagr));                     // 区间 9 天 < 20 天
  assert.equal(A.days, 9);
  const dd = [0, 0, 9 / 12 - 1, 0, 0, 11 / 15 - 1, 12 / 15 - 1, 0, 10 / 16 - 1, 14 / 16 - 1];
  A.dd.forEach((v, i) => near(v, dd[i]));
  assert.equal(A.episodes.length, 3);
  assert.deepEqual(A.episodes.map((e) => [e.peak, e.trough, e.end]), [[1, 2, 3], [4, 5, 7], [7, 8, null]]);
  near(A.maxDD.depth, -0.375);
  assert.equal(A.maxDD.peak, 7);
  assert.equal(A.maxDD.trough, 8);
  assert.equal(A.maxDD.end, null);
  near(A.curDD, -0.125);
  assert.equal(A.curPeakIdx, 7);
  assert.deepEqual(A.phases.map((p) => [p.s, p.e, p.bull]), [[0, 1, true], [1, 2, false], [2, 4, true], [4, 5, false], [5, 7, true], [7, 8, false], [8, 9, true]]);
  assert.equal(A.phases[0].first, true);
  assert.equal(A.phases[6].ongoing, true);
  assert.equal(A.phases.filter((p) => p.ongoing).length, 1);
  near(A.phases[2].ret, 15 / 9 - 1);
  assert.equal(A.phases[2].days, 2);
  assert.equal(A.phases[2].ann, null);                 // 不满一年
  assert.equal(A.phases[2].extI, 4);
  near(A.phases[5].fromExt, 0);                        // 熊市末根就是低点
  assert.ok(A.vol > 0 && Number.isFinite(A.vol));
  // 阈值放大到 40%：阶段数只会减少
  const A4 = B.analyze(bars, 0.4, 'D');
  assert.ok(A4.phases.length <= A.phases.length);
  assert.ok(A4.phases.length >= 1);
});

test('analyze：进行中的阶段未走出阈值时并入上一阶段；全程单边只有 1 段', () => {
  // 10 → 15（牛）→ 11（跌 26.7% 确认熊）→ 12（反弹 9%，不足 20%）：末段 11 → 12 并入熊市
  const bars = dailyBars('2024-01-02', [10, 15, 11, 12]);
  const A = B.analyze(bars, 0.2, 'D');
  assert.deepEqual(A.phases.map((p) => [p.s, p.e, p.bull, p.ongoing]), [[0, 1, true, false], [1, 3, false, true]]);
  const mono = B.analyze(dailyBars('2024-01-02', [1, 2, 3, 4, 5, 6]), 0.2, 'D');
  assert.equal(mono.phases.length, 1);
  assert.equal(mono.phases[0].bull, true);
  assert.equal(mono.phases[0].first, false);
  assert.equal(mono.maxDD, null);
  assert.equal(mono.episodes.length, 0);
  // basis auto：区间 ≥ 6 年用 M
  const long = everyDay('2018-01-01', '2026-01-05');
  assert.equal(B.analyze(long, 0.2, 'auto').basis, 'M');
  assert.equal(B.analyze(everyDay('2023-01-03', '2026-01-05'), 0.2, 'auto').basis, 'W');
  assert.equal(B.analyze(everyDay('2025-06-01', '2026-01-05'), 0.2, 'auto').basis, 'D');
});

test('sampleIndices：W / M 取每周 / 每月最后一根且末根必含', () => {
  const bars = everyDay('2024-01-01', '2024-03-15');   // 2024-01-01 是周一
  const w = B.sampleIndices(bars, 'W');
  assert.equal(B.fmtDate(bars[w[0]].t), '2024-01-07');  // 周日为一周最后一天（t − 4 对 7 取整 → 周一起）
  assert.equal(w[w.length - 1], bars.length - 1);
  const m = B.sampleIndices(bars, 'M');
  assert.deepEqual(m.map((i) => B.fmtDate(bars[i].t)), ['2024-01-31', '2024-02-29', '2024-03-15']);
  assert.equal(B.sampleIndices(bars, 'D').length, bars.length);
});

test('horizons：10 项、基准日 = 期限之前（含）最近一根、年初至今以上年 12-31 为基、满一年才有年化、历史不足为 null', () => {
  const bars = everyDay('2023-01-03', '2026-01-05');
  const H = B.horizons(bars);
  assert.deepEqual(H.map((h) => h.k), ['1周', '1月', '3月', '6月', '年初至今', '1年', '3年', '5年', '10年', '全程']);
  const by = Object.fromEntries(H.map((h) => [h.k, h]));
  assert.equal(by['1周'].baseDate, '2025-12-29');
  assert.equal(by['1月'].baseDate, '2025-12-05');
  assert.equal(by['3月'].baseDate, '2025-10-05');
  assert.equal(by['6月'].baseDate, '2025-07-05');
  assert.equal(by['年初至今'].baseDate, '2025-12-31');
  assert.equal(by['1年'].baseDate, '2025-01-05');
  assert.equal(by['3年'].baseDate, '2023-01-05');
  assert.equal(by['全程'].baseDate, '2023-01-03');
  assert.equal(by['5年'].v, null);
  assert.equal(by['5年'].note, '历史不足');
  assert.equal(by['10年'].v, null);
  assert.equal(by['6月'].ann, null);                   // 184 天
  assert.equal(by['1年'].days, 365);
  assert.ok(by['1年'].ann !== null);
  const last = bars[bars.length - 1], base1y = bars[B.idxOnOrBefore(bars, B.parseYMD('2025-01-05'))];
  near(by['1年'].v, last.c / base1y.c - 1);
  near(by['1年'].ann, Math.pow(1 + by['1年'].v, 365.25 / 365) - 1);
  near(by['全程'].v, last.c / bars[0].c - 1);
  assert.ok(by['全程'].ann !== null && by['3年'].ann !== null);
  // 当年才上市：年初至今的说明是「本年上市」
  const young = everyDay('2026-01-02', '2026-01-05');
  const Hy = Object.fromEntries(B.horizons(young).map((h) => [h.k, h]));
  assert.equal(Hy['年初至今'].v, null);
  assert.equal(Hy['年初至今'].note, '本年上市');
  assert.equal(Hy['1周'].note, '历史不足');
  assert.equal(Hy['全程'].v, young[3].c / young[0].c - 1);
  assert.deepEqual(B.horizons([]), []);
});

test('yearlyReturns：首年自首根起（fromListing、partial）、中间年完整、末年 partial', () => {
  const bars = everyDay('2023-01-03', '2026-01-05');
  const Y = B.yearlyReturns(bars);
  assert.deepEqual(Y.map((y) => y.y), [2023, 2024, 2025, 2026]);
  assert.deepEqual(Y.map((y) => [y.partial, y.fromListing]), [[true, true], [false, false], [false, false], [true, false]]);
  const idx = (ymd) => bars.findIndex((b) => b.t === B.parseYMD(ymd));
  near(Y[0].v, bars[idx('2023-12-31')].c / bars[0].c - 1);
  near(Y[1].v, bars[idx('2024-12-31')].c / bars[idx('2023-12-31')].c - 1);
  near(Y[3].v, bars[bars.length - 1].c / bars[idx('2025-12-31')].c - 1);
});

test('sliceByRange：all 原样；N 年按月回推的第一根；至少保留 2 根', () => {
  const bars = everyDay('2023-01-03', '2026-01-05');
  assert.equal(B.sliceByRange(bars, 'all'), bars);
  const y1 = B.sliceByRange(bars, '1y');
  assert.equal(B.fmtDate(y1[0].t), '2025-01-05');
  assert.equal(y1.length, 366);
  const y10 = B.sliceByRange(bars, '10y');
  assert.equal(y10.length, bars.length);                // 历史不足 10 年 → 从头开始
  const two = bars.slice(-2);
  assert.equal(B.sliceByRange(two, '1y').length, 2);
  const recent = everyDay('2025-12-30', '2026-01-05');
  assert.equal(B.sliceByRange(recent, '1y').length, recent.length);
  // 窗口内不足 2 根时退到倒数第二根
  const sparse = [{ t: B.parseYMD('2010-01-04'), c: 1 }, { t: B.parseYMD('2011-01-04'), c: 2 }, { t: B.parseYMD('2026-01-05'), c: 3 }];
  assert.equal(B.sliceByRange(sparse, '1y').length, 2);
});

test('phaseRows / summary 把下标换成日期并汇总读数', () => {
  const bars = dailyBars('2024-01-02', [10, 12, 9, 13, 15, 11, 12, 16, 10, 14]);
  const A = B.analyze(bars, 0.2, 'D');
  const rows = B.phaseRows(A);
  assert.equal(rows.length, 7);
  assert.equal(rows[0].from, '2024-01-02');
  assert.equal(rows[0].to, '2024-01-03');
  assert.equal(rows[0].first, true);
  assert.equal(rows[6].ongoing, true);
  assert.equal(rows[6].to, '2024-01-11');
  const S = B.summary(A);
  assert.equal(S.first, '2024-01-02');
  assert.equal(S.last, '2024-01-11');
  assert.equal(S.lastClose, 14);
  near(S.multiple, 1.4);
  assert.equal(S.nPhases, 7);
  assert.equal(S.nBull, 4);
  assert.equal(S.nBear, 3);
  assert.equal(S.maxDD.peak, '2024-01-09');
  assert.equal(S.maxDD.trough, '2024-01-10');
  assert.equal(S.maxDD.recovery, null);
  near(S.maxDD.depth, -0.375);
  assert.equal(S.curPeak, '2024-01-09');
  near(S.curDD, -0.125);
});
