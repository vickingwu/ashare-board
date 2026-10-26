/*
 * 面板 growth —— 不同事物的增长。
 *   月度类目轴（monthEnds）上的对数坐标折线：M2、名义 GDP（四季度滚动，季末点连线）、A 股流通市值、中证全指全收益、红利低波全收益、
 *   70 城 / 一线二手住宅（虚线）、黄金人民币价。「全部」窗口每条线以自己第一个有值的月份为 100；「10年 / 5年」以共同起点为 100；
 *   线末标注窗口内倍数与年化；下方表格给各序列自起点以来的倍数与年化。
 * 数据：data/growth.json → data.months[], monthEnds[], start, end, baseMonth, series[]{key,name,unit,kind,cadence,source,start,end,values[]},
 *   summary[]{key,name,start,end,first,last,multiple,years,cagrAll,cagr10y,note}, dropped[]{key,name,reason}, houseDropped[]
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [{ label: '全部', years: 0 }, { label: '10年', years: 10 }, { label: '5年', years: 5 }];
  var DEFAULT_RANGE = 0;
  var COLOR_KEY = { csiAllTR: '000985', divLowVolTR: 'H20269' };   // 其余序列直接用 palette[key]
  var CADENCE = { monthly: '月度', quarterly: '季度', monthEnd: '月末值', monthlyAvg: '月均' };

  function colorOf(s, palette) { return palette[COLOR_KEY[s.key] || s.key] || palette.muted; }

  // 原始值按单位格式化：亿元取整，其余 2 位
  function fmtRaw(v, unit, fmt) {
    if (v === null || v === undefined) return '–';
    return fmt.num(v, unit === '亿元' ? 0 : 2) + ' ' + unit;
  }

  // 当前窗口：起点下标、窗口月末轴、逐条归一
  function windowed(d, rangeIdx) {
    var dates = d.monthEnds;
    var years = RANGES[rangeIdx].years;
    var startIdx = years ? C.firstIndexOnOrAfter(dates, C.windowStartDate(dates[dates.length - 1], years)) : 0;
    if (startIdx < 0) startIdx = 0;
    return { startIdx: startIdx, dates: dates.slice(startIdx), months: d.months.slice(startIdx), series: C.normalizeWindow(dates, d.series, startIdx) };
  }

  // layout = {narrow, width}
  function chartOption(win, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var grid = { left: 56, right: narrow ? 150 : 236, top: 16, bottom: 30 };
    var plotW = Math.max(80, (layout.width || 1000) - grid.left - grid.right);
    var opt = C.baseLineOption({ dates: win.dates, legend: false, grid: grid, yearStep: C.yearLabelStep(win.dates, plotW) });
    opt.yAxis = C.logAxis({ name: '归一（对数）' });
    opt.yAxis.min = function (v) { return v.min / 1.12; };
    opt.yAxis.max = function (v) { return v.max * 1.12; };
    opt.yAxis.axisLabel.showMinLabel = false;
    opt.yAxis.axisLabel.showMaxLabel = false;
    opt.tooltip.formatter = function (params) {
      if (!params || !params.length) return '';
      var idx = params[0].dataIndex;
      var lines = ['<b>' + win.months[idx] + '</b>'];
      params.forEach(function (p) {
        var s = win.series[p.seriesIndex];
        var v = s.values[idx];
        if (v === null || v === undefined) return;
        var raw = s.meta.values[win.startIdx + idx];
        lines.push(p.marker + s.meta.name + '　' + fmtRaw(raw, s.meta.unit, fmt) + ' · 起点以来 ' + C.fmtMult(v / 100, 2));
      });
      return lines.join('<br>');
    };
    opt.series = win.series.map(function (s) {
      var m = s.meta;
      var color = colorOf(m, palette);
      var isGdp = m.key === 'gdp';
      var last = s.lastIdx >= 0 ? s.values[s.lastIdx] : null;
      return {
        name: m.name,
        type: 'line',
        data: s.values,
        showSymbol: isGdp,
        symbolSize: 4,
        connectNulls: isGdp,
        lineStyle: { width: 1.6, type: m.kind === 'index' ? 'dashed' : 'solid', color: color },
        itemStyle: { color: color },
        emphasis: { focus: 'series', lineStyle: { width: 2.6 } },
        endLabel: {
          show: true, fontSize: narrow ? 10 : 11, color: color, distance: 6, width: grid.right - 14, overflow: 'truncate',
          formatter: function () {
            var txt = m.name + ' ' + C.fmtMult(last === null ? null : last / 100, 1);
            if (!narrow && s.cagr !== null) txt += ' · 年化 ' + fmt.pct(s.cagr, 1);
            return txt;
          }
        },
        labelLayout: { moveOverlap: 'shiftY' }
      };
    });
    return opt;
  }

  function summaryTable(d, ctx, container) {
    var fmt = ctx.fmt;
    var meta = {};
    d.series.forEach(function (s) { meta[s.key] = s; });
    function pct(x) { return { t: fmt.pct(x, 1), v: x, cls: x === null ? 'dim' : (x < 0 ? 'neg' : '') }; }
    var rows = d.summary.map(function (s) {
      var m = meta[s.key] || {};
      var unit = m.unit || '';
      return [
        { t: s.name, v: s.name, title: s.note || '' },
        { t: unit + ' · ' + (CADENCE[m.cadence] || m.cadence || ''), v: m.cadence || '' },
        { t: s.start, v: s.start },
        { t: fmtRaw(s.last, unit, fmt) + ' · ' + s.end, v: s.last },
        { t: C.fmtMult(s.multiple, 2), v: s.multiple },
        pct(s.cagrAll), pct(s.cagr10y)
      ];
    });
    var dropped = (d.dropped || []).map(function (x) { return x.name + '（' + x.reason + '）'; });
    C.makeTable(container,
      [{ text: '序列', numeric: false }, { text: '口径·频率', numeric: false }, { text: '起点', numeric: false },
       '最新（值·月份）', '累计倍数', '年化·全程', '年化·10年'],
      rows, { sortable: true, caption: '累计倍数与全程年化按各自起点到最新月份计算；10 年年化从最新月份往前推 10 年。' +
        (dropped.length ? ' 未纳入：' + dropped.join('；') + '。' : '') });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');

    var chartEl = C.el('div', 'chart chart-growth');
    var current = DEFAULT_RANGE;
    var chart = null;
    function layout() { return { narrow: body.clientWidth < NARROW_PX, width: chartEl.clientWidth || body.clientWidth }; }
    function apply() { chart.setOption(chartOption(windowed(d, current), ctx, layout()), { replaceMerge: ['series'] }); }

    C.rangeButtons(body, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, function (i) {
      current = i;
      apply();
    });
    body.appendChild(chartEl);
    chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    chart.setOption(chartOption(windowed(d, current), ctx, layout()));
    body.insertBefore(C.legendToggle(null, d.series.map(function (s) {
      return { name: s.name, color: colorOf(s, ctx.palette) };
    }), chart), chartEl);

    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) { lastWidth = chartEl.clientWidth; apply(); }
      }, 260);
    });

    body.appendChild(C.el('p', 'chart-note', '全部窗口下每条线以自身首个有数据的月份为 100；10 年 / 5 年窗口以共同起点为 100。GDP 为季度点（连线），房价为虚线。'));
    body.appendChild(C.el('h3', null, '各序列长期增长'));
    summaryTable(d, ctx, body);
    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'growth',
    title: '不同事物的增长',
    subtitle: '月度 · 对数坐标 · 每条线在窗口起点归一为 100 · 线末标注倍数与年化',
    dataFile: 'growth.json',
    bodyMinHeight: 1000,
    notes: [
      '对数坐标下相同的斜率代表相同的年化增速，线末的「x 倍 · 年化 y%」是该条线在当前窗口内的累计倍数与年化。',
      '各序列起点不同（M2 2008-01、70 城房价 2011-01、GDP 2006-12，其余 2006-01），「全部」窗口下各自从自己的起点归一；要比较同一时期，用「10年 / 5年」切到共同起点。',
      '名义 GDP 为当季值的四季度滚动求和，只在季末月有点、相邻点连线；金价为当月日收盘均价；指数与流通市值取月末值；房价为 70 城 / 一线 4 城二手住宅价格指数的等权平均（2010-12 = 100）。'
    ],
    render: render
  });
})();
