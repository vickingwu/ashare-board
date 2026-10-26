/*
 * 面板 fundroll —— 基金指数滚动年化。
 *   块 A：中证偏股型基金指数（930950）1 / 3 / 5 / 7 年滚动年化（时间轴折线，正负分色、0 线、负区填充），读数条给最新一日的窗口年化；
 *   块 B：偏股基金 / 非纯债债基（近似偏债）/ 沪深300全收益 / 中证A500 在所选起点归一为 100 的对数坐标净值；股票 / 混合 / 债券基金指数默认隐藏。
 * 数据：data/fundroll.json → data.dates[], windows{"1","3","5","7"}, rolling{"1":[],…}, series[]{key,name,code,kind,extra,values[]},
 *   startOptions[], latest{date,close,r1,r3,r5,r7}, stats{"1":{min,max,latest,pctPositive,n},…}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var WINDOWS = [{ label: '1年', k: '1' }, { label: '3年', k: '3' }, { label: '5年', k: '5' }, { label: '7年', k: '7' }];
  var DEFAULT_WINDOW = 1;
  var COLOR_KEY = { equityFund: 'fundEquity', bondPlusFund: 'fundBondPlus', csi300TR: '000300', a500: '000510', stockFund: 'fundStock', mixedFund: 'fundMixed', bondFund: 'fundBond' };

  function colorOf(s, palette) { return palette[COLOR_KEY[s.key]] || palette.muted; }
  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  // 序列的取值范围（空值跳过）；没有值时 [−1, 1]
  function valueRange(values) {
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < values.length; i++) if (isNum(values[i])) { if (values[i] < lo) lo = values[i]; if (values[i] > hi) hi = values[i]; }
    return lo <= hi ? [Math.min(lo, -1), Math.max(hi, 1)] : [-1, 1];
  }

  // ---------------------------------------------------------------- 块 A：滚动年化
  function rollingOption(d, k, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var dates = d.dates, values = d.rolling[k];
    // 正负分色的分段：ECharts 6.1 的折线渐变要求每段都有上下界（只给 lt / gte 的开区间会在渲染时抛错），所以用数据范围封住两端
    var range = valueRange(values);
    return {
      animation: false,
      tooltip: {
        trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var row = params[0].data;
          return '<b>' + row[0] + '</b><br>近 ' + k + ' 年年化 ' + fmt.pct(row[1], 2);
        }
      },
      grid: { left: narrow ? 48 : 60, right: narrow ? 16 : 24, top: 20, bottom: 32 },
      xAxis: C.timeAxis({}),
      yAxis: {
        type: 'value', name: narrow ? '' : '滚动年化 %', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return v + '%'; } },
        splitLine: { lineStyle: { color: '#E5E7EB' } }
      },
      visualMap: {
        show: false, seriesIndex: 0, dimension: 1,
        pieces: [{ min: range[0] - 1, lt: 0, color: palette.down }, { gte: 0, max: range[1] + 1, color: palette.up }]
      },
      series: [{
        id: 'rolling', name: '近 ' + k + ' 年滚动年化', type: 'line',
        data: C.toTimeRows(dates, values), showSymbol: false, connectNulls: false, sampling: 'lttb',
        lineStyle: { width: 1.4 }, areaStyle: { origin: 0, opacity: 0.18 },
        markLine: {
          silent: true, symbol: 'none', animation: false,
          label: { position: 'insideEndTop', fontSize: 10, color: palette.muted, formatter: '0' },
          lineStyle: { color: palette.fg, width: 1.4, type: 'solid' },
          data: [{ yAxis: 0 }]
        }
      }]
    };
  }

  function readoutText(d, k, ctx, strong) {
    var fmt = ctx.fmt;
    strong.textContent = '近 ' + k + ' 年年化 ' + fmt.pct(d.latest['r' + k], 1);
    return ' · 截至 ' + d.latest.date;
  }

  function statsText(d, k, ctx) {
    var fmt = ctx.fmt, st = d.stats[k] || {};
    if (!isNum(st.n) || st.n === 0) return '该窗口尚无完整数据。';
    return '近 ' + k + ' 年滚动年化（' + d.windows[k] + ' 个交易日）：历史最低 ' + fmt.pct(st.min, 1) + ' · 最高 ' + fmt.pct(st.max, 1) +
      ' · 为正的比例 ' + fmt.pct(st.pctPositive, 1) + '（' + fmt.num(st.n) + ' 个交易日）。';
  }

  // ---------------------------------------------------------------- 块 B：净值对比
  function navOption(winDates, series, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var grid = { left: 56, right: narrow ? 128 : 196, top: 16, bottom: 30 };
    var plotW = Math.max(80, (layout.width || 1000) - grid.left - grid.right);
    var opt = C.baseLineOption({ dates: winDates, legend: false, grid: grid, yearStep: C.yearLabelStep(winDates, plotW) });
    opt.yAxis = C.logAxis({ name: '归一净值（对数）' });
    opt.yAxis.min = function (v) { return v.min / 1.12; };
    opt.yAxis.max = function (v) { return v.max * 1.12; };
    opt.yAxis.axisLabel.showMinLabel = false;
    opt.yAxis.axisLabel.showMaxLabel = false;
    opt.tooltip.formatter = function (params) {
      if (!params || !params.length) return '';
      var idx = params[0].dataIndex;
      var lines = ['<b>' + winDates[idx] + '</b>'];
      params.forEach(function (p) {
        var s = series[p.seriesIndex];
        var v = s.values[idx];
        if (v === null || v === undefined) return;
        var cg = C.cagr(100, v, C.yearsBetween(s.baseDate, winDates[idx]));
        lines.push(p.marker + s.meta.name + '　' + fmt.num(v, 1) + ' · 起点以来 ' + C.fmtMult(v / 100, 2) + (cg === null ? '' : ' · 年化 ' + fmt.pct(cg, 1)));
      });
      return lines.join('<br>');
    };
    opt.series = series.map(function (s) {
      var m = s.meta;
      var color = colorOf(m, palette);
      return {
        name: m.name,
        type: 'line',
        data: s.values,
        showSymbol: false,
        sampling: 'lttb',
        connectNulls: false,
        lineStyle: { width: m.extra ? 1.2 : 1.6, type: m.kind === 'price' ? 'dashed' : 'solid', color: color },
        itemStyle: { color: color },
        emphasis: { focus: 'series', lineStyle: { width: 2.6 } },
        endLabel: {
          show: true, fontSize: narrow ? 10 : 11, color: color, distance: 6, width: grid.right - 14, overflow: 'truncate',
          formatter: function () { return m.name + (s.cagr === null ? '' : ' ' + fmt.pct(s.cagr, 1)); }
        },
        labelLayout: { moveOverlap: 'shiftY' }
      };
    });
    return opt;
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    function narrow() { return body.clientWidth < NARROW_PX; }

    // ---- 块 A
    var k = WINDOWS[DEFAULT_WINDOW].k;
    var toolsA = C.el('div', 'panel-tools');
    body.appendChild(toolsA);
    var readout = C.el('div', 'readout');
    var strong = C.el('strong');
    readout.appendChild(strong);
    var tail = document.createTextNode(readoutText(d, k, ctx, strong));
    readout.appendChild(tail);
    body.appendChild(readout);
    var rollEl = C.el('div', 'chart chart-fundroll');
    body.appendChild(rollEl);
    var statsNote = C.el('p', 'chart-note', statsText(d, k, ctx));
    body.appendChild(statsNote);
    var rollChart = echarts.init(rollEl, null, { renderer: 'canvas' });
    rollChart.setOption(rollingOption(d, k, ctx, { narrow: narrow() }));

    C.rangeButtons(toolsA, WINDOWS.map(function (w) { return w.label; }), DEFAULT_WINDOW, function (i) {
      k = WINDOWS[i].k;
      rollChart.setOption(rollingOption(d, k, ctx, { narrow: narrow() }), { replaceMerge: ['series'] });
      tail.textContent = readoutText(d, k, ctx, strong);
      statsNote.textContent = statsText(d, k, ctx);
    }, { label: '滚动窗口' });

    // ---- 块 B
    body.appendChild(C.el('h3', null, '基金指数净值对比'));
    var toolsB = C.el('div', 'panel-tools');
    body.appendChild(toolsB);
    var start = d.startOptions[0];
    C.selectControl(toolsB, {
      label: '起点', name: 'fundroll-start', value: start,
      options: d.startOptions.map(function (s) { return { value: s, text: s.slice(0, 7) + ' 起' }; }),
      onChange: function (v) { start = v; applyNav(); }
    });
    var legendHost = C.el('div');
    body.appendChild(legendHost);
    var navEl = C.el('div', 'chart chart-fundnav');
    body.appendChild(navEl);
    body.appendChild(C.el('p', 'chart-note', '所选起点之后的第一个交易日归一为 100，对数坐标；虚线为价格指数（不含股息）；线末数字是该起点以来的年化。'));
    var navChart = echarts.init(navEl, null, { renderer: 'canvas' });

    function applyNav(first) {
      var i0 = C.firstIndexOnOrAfter(d.dates, start);
      if (i0 < 0) i0 = 0;
      var winDates = d.dates.slice(i0);
      navChart.setOption(navOption(winDates, C.normalizeWindow(d.dates, d.series, i0), ctx, { narrow: narrow(), width: navEl.clientWidth || body.clientWidth }),
        first ? undefined : { replaceMerge: ['series'] });
    }
    applyNav(true);
    C.legendToggle(legendHost, d.series.map(function (s) {
      return { name: s.name, color: colorOf(s, ctx.palette), hidden: !!s.extra };
    }), navChart);

    var lastWidth = navEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (navEl.clientWidth !== lastWidth) {
          lastWidth = navEl.clientWidth;
          rollChart.setOption(rollingOption(d, k, ctx, { narrow: narrow() }), { replaceMerge: ['series'] });
          applyNav();
        }
      }, 260);
    });

    return { charts: [rollChart, navChart] };
  }

  window.AshareBoard.registerPanel({
    id: 'fundroll',
    title: '基金指数滚动年化',
    subtitle: '中证偏股型基金指数 1 / 3 / 5 / 7 年滚动年化 · 偏股 vs 固收+ vs 宽基全收益 净值对比',
    dataFile: 'fundroll.json',
    bodyMinHeight: 1040,
    notes: [
      '上图每个点表示「从该日往前数一个窗口买入偏股基金指数、持有到该日」的年化收益：红色为正、绿色为负，0 线以下的区域填色；读数条是最新一日的值。',
      '窗口按交易日计：252 日 ≈ 1 年、756 ≈ 3 年、1260 ≈ 5 年、1764 ≈ 7 年，年化 = (窗口末值 ÷ 窗口初值)^(252 ÷ 窗口日数) − 1；窗口未满的日期为空。',
      '下图把所选起点之后的第一个交易日归一为 100，比较偏股基金、非纯债债基（近似偏债）、沪深300全收益与中证A500（价格指数）；股票 / 混合 / 债券基金指数默认隐藏，可在图例里打开。'
    ],
    render: render
  });
})();
