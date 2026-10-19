/*
 * 面板 erp —— 股债性价比。
 *   一个 ECharts 实例、上下两个网格：上为 ERP（盈利收益率 − 10Y 国债）折线 + 均值±1σ 阴影 + ±2σ 虚线 + 当前点；
 *   下为 盈利收益率 ÷ 国债收益率 的比值折线 + 均值线。两个网格共享横轴指针与提示框。
 * 数据：data/erp.json → data.dates[], pe[], ey[], y10[], erp[], ratio[], summary{latest, erpPct, ratioPct, mean, std, bands, ratioMean}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;

  function readout(d, ctx) {
    var s = d.summary, fmt = ctx.fmt;
    var box = C.el('div', 'readout');
    function item(label, value) {
      var sp = C.el('span');
      sp.appendChild(document.createTextNode(label + ' '));
      sp.appendChild(C.el('strong', null, value));
      box.appendChild(sp);
    }
    item('ERP', fmt.pct(s.latest.erp, 2));
    item('历史分位', Math.round(s.erpPct) + '%');
    item('10Y', fmt.pct(s.latest.y10, 2));
    item('PE', fmt.num(s.latest.pe, 1));
    item('比值', fmt.num(s.latest.ratio, 2) + '（分位 ' + Math.round(s.ratioPct) + '%）');
    item('截至', s.latest.date);
    return box;
  }

  // 纵轴范围：把 ±2σ 虚线也包进来（否则 +2σ 超出数据极值时会被裁掉），并取整到刻度步长，刻度才整齐
  function axisRange(values) {
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var step = (hi - lo) > 8 ? 2 : 1;
    return { min: Math.floor(lo / step) * step, max: Math.ceil(hi / step) * step, interval: step };
  }

  // layout = {narrow, width}：窄屏收紧左右边距，并按绘图区宽度抽稀年份刻度
  function chartOption(d, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var s = d.summary;
    var dates = d.dates.concat([s.latest.date]);
    var erp = d.erp.concat([s.latest.erp]);
    var ratio = d.ratio.concat([s.latest.ratio]);
    var last = dates.length - 1;
    var colorErp = palette['000906'];
    var bands = C.sigmaBands(s.mean, s.std, palette.muted, 2);
    var erpRange = axisRange(erp.concat([s.bands.p2, s.bands.m2]));
    var left = narrow ? 44 : 60, right = narrow ? 16 : 70;
    var plotW = Math.max(80, (layout.width || 1000) - left - right);
    var axisLabel = { color: palette.muted, fontSize: 11, interval: 0, showMaxLabel: false,
                      formatter: C.yearLabelFormatter(dates, C.yearLabelStep(dates, plotW)) };

    function valueAt(i) {
      if (i === last) return { pe: s.latest.pe, ey: s.latest.ey, y10: s.latest.y10, erp: s.latest.erp, ratio: s.latest.ratio, latest: true };
      return { pe: d.pe[i], ey: d.ey[i], y10: d.y10[i], erp: d.erp[i], ratio: d.ratio[i], latest: false };
    }

    return {
      animation: false,
      tooltip: {
        trigger: 'axis', confine: true, textStyle: { fontSize: 12 },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var i = params[0].dataIndex;
          var v = valueAt(i);
          return '<b>' + dates[i] + (v.latest ? '（最新，非月末）' : '') + '</b><br>' +
            'ERP ' + fmt.pct(v.erp, 2) + '　比值 ' + fmt.num(v.ratio, 2) + '<br>' +
            '盈利收益率 ' + fmt.pct(v.ey, 2) + ' = 100 ÷ PE ' + fmt.num(v.pe, 2) + '<br>' +
            '10 年国债 ' + fmt.pct(v.y10, 2);
        }
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#9CA3AF' } },
      grid: [
        { left: left, right: right, top: 28, height: '48%' },
        { left: left, right: right, top: '70%', height: '22%' }
      ],
      xAxis: [
        { type: 'category', gridIndex: 0, data: dates, boundaryGap: false, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D1D5DB' } } },
        { type: 'category', gridIndex: 1, data: dates, boundaryGap: false, axisLabel: axisLabel, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D1D5DB' } } }
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, name: 'ERP（百分点）', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          min: erpRange.min, max: erpRange.max, interval: erpRange.interval,
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } },
        { type: 'value', gridIndex: 1, name: '比值', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' }, scale: true,
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } }
      ],
      series: [
        {
          name: 'ERP', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: erp,
          showSymbol: false, lineStyle: { width: 1.6, color: colorErp }, itemStyle: { color: colorErp },
          markArea: bands.markArea, markLine: bands.markLine
        },
        {
          name: '当前', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, data: [[last, s.latest.erp]],
          symbolSize: 10, itemStyle: { color: palette.up, borderColor: '#fff', borderWidth: 1.5 },
          label: { show: true, position: narrow ? 'top' : 'right', fontSize: 11, color: palette.up,
                   formatter: function () { return '当前 ' + fmt.pct(s.latest.erp, 2); } },
          tooltip: { show: false }
        },
        {
          name: '比值', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: ratio,
          showSymbol: false, lineStyle: { width: 1.4, color: palette.fg }, itemStyle: { color: palette.fg },
          markLine: {
            silent: true, symbol: 'none', animation: false,
            label: { position: 'insideEndTop', fontSize: 10, color: palette.muted, formatter: function (p) { return p.name; } },
            data: [{ yAxis: s.ratioMean, name: '均值 ' + fmt.num(s.ratioMean, 2), lineStyle: { color: palette.muted, width: 1 } },
                   { yAxis: 1, name: '1 倍', lineStyle: { color: '#9CA3AF', type: 'dotted', width: 1 } }]
          }
        }
      ]
    };
  }

  function recentTable(d, ctx, container) {
    var fmt = ctx.fmt;
    var n = d.dates.length;
    var rows = [];
    for (var i = n - 1; i >= Math.max(0, n - 12); i--) {
      rows.push([
        { t: d.dates[i], v: d.dates[i] }, { t: fmt.num(d.pe[i], 2), v: d.pe[i] }, { t: fmt.pct(d.ey[i], 2), v: d.ey[i] },
        { t: fmt.pct(d.y10[i], 2), v: d.y10[i] }, { t: fmt.pct(d.erp[i], 2), v: d.erp[i] }, { t: fmt.num(d.ratio[i], 2), v: d.ratio[i] }
      ]);
    }
    C.makeTable(container, [{ text: '月末', numeric: false }, 'PE', '盈利收益率', '10 年国债', 'ERP', '比值'], rows,
      { caption: '最近 12 个已完结月份的月末值。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');

    body.appendChild(readout(d, ctx));
    var chartEl = C.el('div', 'chart chart-tall');
    body.appendChild(chartEl);
    function layout() { return { narrow: body.clientWidth < 520, width: chartEl.clientWidth || body.clientWidth }; }
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    chart.setOption(chartOption(d, ctx, layout()));
    // 宽度变化后边距与年份刻度步长要重算（app.js 只负责 chart.resize()）
    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) { lastWidth = chartEl.clientWidth; chart.setOption(chartOption(d, ctx, layout()), { notMerge: true }); }
      }, 260);
    });
    var s = d.summary;
    body.appendChild(C.el('p', 'chart-note',
      '阴影为均值 ± 1σ（' + ctx.fmt.num(s.bands.m1, 2) + ' – ' + ctx.fmt.num(s.bands.p1, 2) + '），虚线为 ± 2σ；样本 ' +
      s.sampleStart.slice(0, 7) + ' 至 ' + s.sampleEnd.slice(0, 7) + ' 共 ' + s.nMonths + ' 个月末，股票端为' + (d.equity ? d.equity.name : '') + '。'));
    body.appendChild(C.el('h3', null, '最近 12 个月末'));
    recentTable(d, ctx, body);
    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'erp',
    title: '股债性价比',
    subtitle: '盈利收益率（100 ÷ PE）与 10 年国债收益率之差及其比值 · 月末',
    dataFile: 'erp.json',
    bodyMinHeight: 980,
    notes: [
      'ERP 越高，表示相对于无风险的国债，股票给出的盈利回报越多；落在均值以上一个标准差之外，是历史上少见的区域。',
      '这是一个相对指标：PE 不动、利率下降也会让 ERP 走高，所以要同时看上图的 ERP 与下图的比值，并留意利率所处的水平。'
    ],
    render: render
  });
})();
