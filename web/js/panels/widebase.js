/*
 * 面板 widebase —— 宽基指数全收益净值。
 *   对数坐标折线：每条线在当前时间窗起点归一为 100；按钮切换 全部/10年/5年/3年/1年 时重新归一；
 *   线末标注该窗口内的年化；下方表格给各自起点的累计倍数与年化。
 * 数据：data/widebase.json → data.dates[]（共用交易日轴）、data.series[]{code,trCode,name,kind,start,end,values[]}、data.summary[]
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var RANGES = [{ label: '全部', years: 0 }, { label: '10年', years: 10 }, { label: '5年', years: 5 }, { label: '3年', years: 3 }, { label: '1年', years: 1 }];

  function lastNonNull(values) {
    for (var i = values.length - 1; i >= 0; i--) if (values[i] !== null && values[i] !== undefined) return i;
    return -1;
  }

  function firstNonNullFrom(values, from) {
    for (var i = Math.max(0, from); i < values.length; i++) if (values[i] !== null && values[i] !== undefined) return i;
    return -1;
  }

  // 当前窗口下每条线的归一序列、起点下标、年化
  function windowed(d, rangeIdx) {
    var dates = d.dates;
    var years = RANGES[rangeIdx].years;
    var startIdx = years ? C.firstIndexOnOrAfter(dates, C.windowStartDate(dates[dates.length - 1], years)) : 0;
    if (startIdx < 0) startIdx = 0;
    var winDates = dates.slice(startIdx);
    var series = d.series.map(function (s) {
      var vals = s.values.slice(startIdx);
      var base = firstNonNullFrom(vals, 0);
      var norm = C.normalizeFrom(vals, base);
      var last = lastNonNull(norm);
      var cg = (base >= 0 && last > base) ? C.cagr(100, norm[last], C.yearsBetween(winDates[base], winDates[last])) : null;
      return { meta: s, values: norm, baseIdx: base, lastIdx: last, cagr: cg, baseDate: base >= 0 ? winDates[base] : null };
    });
    return { dates: winDates, series: series };
  }

  // layout = {narrow, width}：窄屏缩小右侧标注区，并按绘图区宽度抽稀年份刻度
  function chartOption(win, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var grid = { left: 56, right: layout.narrow ? 92 : 118, top: 16, bottom: 30 };
    var plotW = Math.max(80, (layout.width || 1000) - grid.left - grid.right);
    var opt = C.baseLineOption({ dates: win.dates, legend: false, grid: grid, yearStep: C.yearLabelStep(win.dates, plotW) });
    opt.yAxis = C.logAxis({ name: '归一净值（对数）' });
    // 对数轴默认把上下界取到 10 的整数幂，曲线只占画面一半；改为按数据范围各留 12% 余量
    opt.yAxis.min = function (v) { return v.min / 1.12; };
    opt.yAxis.max = function (v) { return v.max * 1.12; };
    // 上下界本身也会成为刻度（57 / 2,797 这类零碎数字），不标它们，只留 100 / 1,000 等整刻度
    opt.yAxis.axisLabel.showMinLabel = false;
    opt.yAxis.axisLabel.showMaxLabel = false;
    opt.tooltip.formatter = function (params) {
      if (!params || !params.length) return '';
      var idx = params[0].dataIndex;
      var lines = ['<b>' + win.dates[idx] + '</b>'];
      params.forEach(function (p) {
        var s = win.series[p.seriesIndex];
        var v = s.values[idx];
        if (v === null || v === undefined) return;
        var yrs = C.yearsBetween(s.baseDate, win.dates[idx]);
        var cg = C.cagr(100, v, yrs);
        lines.push(p.marker + s.meta.name + (s.meta.kind === 'price' ? '（价格）' : '') + '　' + fmt.num(v, 1) +
          ' · 起点以来 ' + C.fmtMult(v / 100) + (cg === null ? '' : ' · 年化 ' + fmt.pct(cg, 1)));
      });
      return lines.join('<br>');
    };
    opt.series = win.series.map(function (s) {
      var color = palette[s.meta.code] || palette.muted;
      return {
        name: s.meta.name,
        type: 'line',
        data: s.values,
        showSymbol: false,
        sampling: 'lttb',
        connectNulls: false,
        lineStyle: { width: s.meta.kind === 'price' ? 1.2 : 1.6, type: s.meta.kind === 'price' ? 'dashed' : 'solid', color: color },
        itemStyle: { color: color },
        emphasis: { focus: 'series', lineStyle: { width: 2.6 } },
        endLabel: {
          show: true, fontSize: layout.narrow ? 10 : 11, color: color, distance: 6,
          formatter: function () { return s.meta.name + (s.cagr === null ? '' : ' ' + fmt.pct(s.cagr, 1)); }
        },
        labelLayout: { moveOverlap: 'shiftY' }
      };
    });
    return opt;
  }

  function legendRow(d, ctx, chart) {
    var legend = C.el('div', 'legend');
    var hidden = {};
    d.series.forEach(function (s) {
      var item = C.el('span', 'legend-item');
      item.style.cursor = 'pointer';
      item.appendChild(C.colorCell(ctx.palette[s.code] || ctx.palette.muted));
      item.appendChild(document.createTextNode(s.name + (s.kind === 'price' ? '（价格）' : '')));
      item.title = '点击显示 / 隐藏';
      item.addEventListener('click', function () {
        hidden[s.name] = !hidden[s.name];
        item.style.opacity = hidden[s.name] ? 0.35 : 1;
        chart.dispatchAction({ type: hidden[s.name] ? 'legendUnSelect' : 'legendSelect', name: s.name });
      });
      legend.appendChild(item);
    });
    return legend;
  }

  function summaryTable(d, ctx, container) {
    var fmt = ctx.fmt;
    function pct(x) { return { t: fmt.pct(x, 1), v: x, cls: x === null ? 'dim' : (x < 0 ? 'neg' : '') }; }
    var rows = d.summary.map(function (s) {
      return [
        { t: s.name, v: s.name },
        { t: s.kind === 'tr' ? '全收益' : '价格', v: s.kind },
        { t: s.start, v: s.start },
        { t: C.fmtMult(s.multiple, 2), v: s.multiple },
        pct(s.cagrAll), pct(s.cagr10y), pct(s.cagr5y), pct(s.cagr3y),
        { t: fmt.pct(s.maxDrawdown, 1), v: s.maxDrawdown, cls: 'neg' }
      ];
    });
    C.makeTable(container,
      [{ text: '指数', numeric: false }, { text: '口径', numeric: false }, { text: '起点', numeric: false },
       '累计倍数', '年化·全程', '年化·10年', '年化·5年', '年化·3年', '最大回撤'],
      rows, { sortable: true, caption: '累计倍数与全程年化按各自起点计算；10 / 5 / 3 年年化从最后日期同月同日往前推，历史不够长的留空。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');

    var chartEl = C.el('div', 'chart chart-tall');
    var current = 0;
    var chart = null;
    function layout() { return { narrow: body.clientWidth < 520, width: chartEl.clientWidth || body.clientWidth }; }
    function apply() { chart.setOption(chartOption(windowed(d, current), ctx, layout()), { replaceMerge: ['series'] }); }

    C.rangeButtons(body, RANGES.map(function (r) { return r.label; }), 0, function (i) {
      current = i;
      apply();
    });
    body.appendChild(chartEl);
    chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    chart.setOption(chartOption(windowed(d, current), ctx, layout()));
    body.insertBefore(legendRow(d, ctx, chart), chartEl);

    // 宽度变化后边距与年份刻度步长要重算（app.js 只负责 chart.resize()）
    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) { lastWidth = chartEl.clientWidth; apply(); }
      }, 260);
    });

    body.appendChild(C.el('p', 'chart-note', '虚线为价格指数（无全收益版本）。切换时间窗后所有线重新以窗口首日为 100；线末数字是该窗口内的年化收益。'));
    body.appendChild(C.el('h3', null, '各指数长期表现'));
    summaryTable(d, ctx, body);
    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'widebase',
    title: '宽基指数全收益净值',
    subtitle: '各条线在当前时间窗起点归一为 100 · 对数坐标 · 线末标注窗口内年化',
    dataFile: 'widebase.json',
    bodyMinHeight: 960,
    notes: [
      '对数坐标下，相同的斜率代表相同的年化收益；一段时间内翻倍，不论发生在 2007 年还是 2020 年，在图上的高度都一样。',
      '全收益指数把分红当作在除息日买回指数，长期和价格指数的差距就是股息的贡献；红利低波这类高股息策略的差距最明显。',
      '默认显示全部历史，起点各不相同；用「10年 / 5年 / 3年 / 1年」切到相同起点后再比较各指数。'
    ],
    render: render
  });
})();
