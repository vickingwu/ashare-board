/*
 * 面板 indheat —— 行业交易热度。
 *   单色热力图：行 = 申万一级行业（或点开某个一级后的二级行业），列 = 最近 K 个交易日。
 *   两个指标都在浏览器里从收盘价算：
 *     RPS_N(i,t) = 100 × #{j≠i: N 日涨幅_j < N 日涨幅_i} / (n_t − 1)，同级全部指数参与排名（二级时也是全部 130 个，不只是同一父级的）；
 *     MA20 站上率(t) = 最近 250 个交易日里收盘 > 20 日均线 的天数占比。
 *   工具条：指标 / 周期 N / 显示列数；点击一级格子下钻到二级，「返回一级」回到 31 行；同一个 ECharts 实例重新 setOption，导出拿到的就是当前层级。
 * 数据：data/indheat.json → data.dates[], level1[]{code,name,closes[],amtShare[]}, level2[]{code,name,parent,closes[]}, dropped[], periods[], columns[]
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 700;
  var MA_N = 20, SHARE_WINDOW = 250;
  var ROW_PX = 22, MIN_H = 300;

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  // 每个 (level, N) 只算一次：全历史的 N 日涨幅矩阵与横截面 RPS 矩阵
  function makeCalc(d) {
    var universes = { 1: d.level1, 2: d.level2 };
    var retCache = {}, rpsCache = {}, maCache = {};
    function returns(level, n) {
      var key = level + ':' + n;
      if (!retCache[key]) retCache[key] = universes[level].map(function (s) { return C.pctChangeN(s.closes, n); });
      return retCache[key];
    }
    function rps(level, n) {
      var key = level + ':' + n;
      if (!rpsCache[key]) {
        var rets = returns(level, n), T = d.dates.length;
        var out = rets.map(function () { return new Array(T); });
        for (var t = 0; t < T; t++) {
          var col = rets.map(function (r) { return r[t]; });
          var rp = C.rankPct(col);
          for (var i = 0; i < rp.length; i++) out[i][t] = rp[i];
        }
        rpsCache[key] = out;
      }
      return rpsCache[key];
    }
    function maShare(series) {
      if (!maCache[series.code]) {
        var ma = C.rollingMean(series.closes, MA_N);
        var flags = series.closes.map(function (c, i) { return (isNum(c) && isNum(ma[i])) ? c > ma[i] : null; });
        maCache[series.code] = { flags: flags, share: C.rollingShare(flags, SHARE_WINDOW) };
      }
      return maCache[series.code];
    }
    return { universes: universes, returns: returns, rps: rps, maShare: maShare };
  }

  // 当前状态下的行（含每行的值序列与附加信息），按最新一列降序，空值在后
  function computeRows(d, calc, state) {
    var universe = calc.universes[state.level];
    var picked = [];
    universe.forEach(function (s, idx) {
      if (state.level === 1 || s.parent === state.parent) picked.push({ series: s, uidx: idx });
    });
    var T = d.dates.length, from = T - state.cols;
    var rpsM = state.metric === 'rps' ? calc.rps(state.level, state.period) : null;
    var retM = state.metric === 'rps' ? calc.returns(state.level, state.period) : null;
    var rows = picked.map(function (p) {
      var vals, extra;
      if (rpsM) {
        vals = rpsM[p.uidx].slice(from);
        extra = retM[p.uidx].slice(from);
      } else {
        var m = calc.maShare(p.series);
        vals = m.share.slice(from);
        extra = m.flags.slice(from);
      }
      return { code: p.series.code, name: p.series.name, series: p.series, vals: vals, extra: extra, latest: vals[vals.length - 1] };
    });
    rows.sort(function (a, b) {
      var na = !isNum(a.latest), nb = !isNum(b.latest);
      if (na && nb) return a.name.localeCompare(b.name, 'zh');
      if (na) return 1;
      if (nb) return -1;
      if (b.latest !== a.latest) return b.latest - a.latest;
      return a.name.localeCompare(b.name, 'zh');
    });
    return rows;
  }

  function chartOption(d, state, rows, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var cols = state.cols, T = d.dates.length, from = T - cols;
    var xCats = d.dates.slice(from);
    var zoom = narrow && cols > 20;
    var opt = C.heatmapBase({ xCats: xCats, yCats: rows.map(function (r) { return r.name; }), narrow: narrow });
    opt.grid.bottom = zoom ? 88 : 60;          // 底部：日期标签 + 色阶（窄屏多列时再加一条滑条）

    var plotW = Math.max(80, (layout.width || 1000) - opt.grid.left - opt.grid.right);
    var visibleCols = zoom ? 20 : cols;
    var step = Math.max(1, Math.ceil(visibleCols * 44 / plotW));
    opt.xAxis.axisLabel.interval = function (idx) { return (cols - 1 - idx) % step === 0; };
    opt.xAxis.axisLabel.formatter = function (v) { return v.slice(5); };

    var data = [];
    rows.forEach(function (r, yi) {
      r.vals.forEach(function (v, xi) {
        if (isNum(v)) data.push([xi, yi, Math.round(v * 10) / 10, r.extra[xi]]);
      });
    });
    var isRps = state.metric === 'rps';
    var showLabel = cols <= 20 && !narrow;

    opt.tooltip.formatter = function (p) {
      var v = p.value, r = rows[v[1]], date = xCats[v[0]];
      var head = '<b>' + r.name + '</b> · ' + date + '<br>';
      if (isRps) {
        var s = 'RPS ' + Math.round(v[2]) + '（' + state.period + ' 日涨幅 ' + C.fmtSignedPct(v[3], 1) + '）';
        if (state.level === 1 && r.series.amtShare) {
          var share = r.series.amtShare[from + v[0]];
          if (isNum(share)) s += ' · 成交额占比 ' + fmt.pct(share, 1);
        }
        return head + s;
      }
      return head + 'MA20 站上率 ' + Math.round(v[2]) + '%（当日收盘' + (v[3] === true ? '高于' : v[3] === false ? '不高于' : '—') + ' 20 日均线）';
    };

    opt.series = [{
      name: isRps ? 'RPS' : 'MA20 站上率', type: 'heatmap', data: data,
      itemStyle: { borderColor: palette.neutral, borderWidth: cols > 120 ? 0 : 1 },
      emphasis: { itemStyle: { borderColor: palette.fg, borderWidth: 1.5 } },
      cursor: state.level === 1 ? 'pointer' : 'default',
      label: C.heatCellLabel(showLabel, function (p) { return String(Math.round(p.value[2])); })
    }];
    opt.visualMap = {
      type: 'continuous', dimension: 2, min: 0, max: 100, calculable: false,
      inRange: { color: [palette.heatLow, palette.heatHigh] }, orient: 'horizontal', left: 'center', bottom: 0,
      text: ['100', '0'], textStyle: { color: palette.muted, fontSize: 11 }, itemWidth: 10, itemHeight: narrow ? 120 : 160
    };
    if (zoom) {
      opt.dataZoom = [
        { type: 'inside', xAxisIndex: 0, startValue: cols - 20, endValue: cols - 1, filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, startValue: cols - 20, endValue: cols - 1, filterMode: 'none',
          height: 16, bottom: 30, brushSelect: false, showDetail: false }
      ];
    }
    return opt;
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var periods = d.periods || [3, 5, 10, 20, 60, 120, 250];
    var columns = d.columns || [20, 60, 120, 250];
    var narrow = body.clientWidth < NARROW_PX;
    var state = { metric: 'rps', period: 60, cols: narrow ? 20 : 60, level: 1, parent: null, parentName: null };
    var calc = makeCalc(d);
    var l1Names = {};
    d.level1.forEach(function (s) { l1Names[s.code] = s.name; });

    var tools = C.el('div', 'panel-tools');
    C.rangeButtons(tools, ['RPS', 'MA20 站上率'], 0, function (i) {
      state.metric = i === 0 ? 'rps' : 'ma20';
      gPeriod.hidden = state.metric !== 'rps';
      rebuild();
    }, { label: '指标' });
    var gPeriod = C.rangeButtons(tools, periods.map(String), Math.max(0, periods.indexOf(state.period)), function (i) {
      state.period = periods[i];
      rebuild();
    }, { label: '周期 N 日' });
    C.rangeButtons(tools, columns.map(String), Math.max(0, columns.indexOf(state.cols)), function (i) {
      state.cols = columns[i];
      rebuild();
    }, { label: '显示列数' });
    body.appendChild(tools);

    var crumbHost = C.el('div');
    body.appendChild(crumbHost);
    body.appendChild(C.el('p', 'heat-legend', '颜色越深越热：RPS 0 = 同级最弱，100 = 同级最强；MA20 站上率 = 近 250 日里收盘站在自身 20 日均线之上的比例。一级行业的格子可点击，展开它的二级行业。'));

    var chartEl = C.el('div', 'chart chart-heat-ind');
    body.appendChild(chartEl);
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    var note = C.el('p', 'chart-note');
    body.appendChild(note);

    function layout() { return { narrow: body.clientWidth < NARROW_PX, width: chartEl.clientWidth || body.clientWidth }; }

    function renderCrumbs() {
      crumbHost.innerHTML = '';
      if (state.level === 1) {
        C.crumbs(crumbHost, ['一级行业（' + d.level1.length + '）']);
      } else {
        C.crumbs(crumbHost, ['一级行业', state.parentName + '（二级）'], function () {
          state.level = 1; state.parent = null; state.parentName = null;
          rebuild();
        });
      }
    }

    function rebuild() {
      var rows = computeRows(d, calc, state);
      state.rows = rows;
      renderCrumbs();
      chartEl.style.height = Math.max(MIN_H, rows.length * ROW_PX + 90) + 'px';
      chart.resize();
      chart.setOption(chartOption(d, state, rows, ctx, layout()), { notMerge: true });
      var last = d.dates[d.dates.length - 1];
      var metricTxt = state.metric === 'rps' ? 'RPS（' + state.period + ' 日涨幅在同级 ' + calc.universes[state.level].length + ' 个指数中的排名分位）' : 'MA20 站上率（近 250 日）';
      var scope = state.level === 1 ? '31 个一级行业' : state.parentName + ' 下 ' + rows.length + ' 个二级行业';
      note.textContent = metricTxt + ' · ' + scope + ' · 最近 ' + state.cols + ' 个交易日，截至 ' + last + '；行按最新一列降序。' +
        (d.dropped && d.dropped.length ? ' 二级里 ' + d.dropped.map(function (x) { return x.name; }).join('、') + ' 因源数据缺口过多未纳入。' : '');
    }

    chart.on('click', function (p) {
      if (p.componentType !== 'series' || state.level !== 1 || !p.value) return;
      var row = state.rows[p.value[1]];
      if (!row) return;
      state.level = 2;
      state.parent = row.code;
      state.parentName = row.name;
      rebuild();
    });

    rebuild();

    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) {
          lastWidth = chartEl.clientWidth;
          chart.setOption(chartOption(d, state, state.rows, ctx, layout()), { notMerge: true });
        }
      }, 260);
    });

    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'indheat',
    title: '行业交易热度',
    subtitle: '申万一级 / 二级行业 · RPS 相对强度 与 MA20 站上率 · 单色越深越热',
    dataFile: 'indheat.json',
    bodyMinHeight: 980,
    notes: [
      'RPS 是横截面排名：某行业 RPS 90 表示它的 N 日涨幅超过了同级 90% 的行业，和涨幅绝对值大小无关；一片深色说明资金在少数行业里集中。',
      'MA20 站上率衡量趋势的持续性：一个行业最近一年里有多少交易日收在 20 日均线之上；两项指标都在浏览器里由收盘价即时计算。'
    ],
    render: render
  });
})();
