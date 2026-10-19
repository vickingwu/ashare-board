/*
 * 面板 valuation —— 主要宽基指数估值分位。
 *   每个指数一行：绿→黄→红的区间条表示自身月末 PE 的 最低→最高，白圈是当前 PE 的位置；
 *   条下标注 PE、全历史 / 近10年 / 近5年 分位；下方是可排序的明细表。
 * 数据：data/valuation.json → data.indexes[]{code,name,source,peNow,peDate,sampleStart,nMonths,pctAll,pct10y,pct5y,min,max,median,shortSample,monthly}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var ROW_H = 58;
  var NARROW_PX = 520;

  function pctText(x) { return x === null || x === undefined ? '–' : Math.round(x) + '%'; }

  function labelFor(r, narrow, fmt) {
    var parts = narrow
      ? ['PE ' + fmt.num(r.peNow, 1), pctText(r.pctAll) + ' / ' + pctText(r.pct10y) + ' / ' + pctText(r.pct5y)]
      : ['PE ' + fmt.num(r.peNow, 1), '全历史 ' + pctText(r.pctAll), '近10年 ' + pctText(r.pct10y), '近5年 ' + pctText(r.pct5y)];
    var s = parts.join(' · ');
    if (r.shortSample) s += '（样本短）';
    return s;
  }

  function chartOption(rows, ctx, narrow) {
    var palette = ctx.palette, fmt = ctx.fmt, echarts = ctx.echarts;
    var gradient = new echarts.graphic.LinearGradient(0, 0, 1, 0, [
      { offset: 0, color: C.gradientGreenRed(0) },
      { offset: 0.5, color: C.gradientGreenRed(0.5) },
      { offset: 1, color: C.gradientGreenRed(1) }
    ]);

    function renderItem(params, api) {
      var i = params.dataIndex;
      var r = rows[i];
      var p0 = api.coord([0, i]), p1 = api.coord([100, i]);
      var bandH = api.size([0, 1])[1];
      var barH = Math.min(14, Math.max(8, bandH * 0.26));
      var cy = p0[1] - bandH * 0.16;
      var x0 = p0[0], x1 = p1[0], w = x1 - x0;
      var span = r.max - r.min;
      var pos = span > 0 ? Math.max(0, Math.min(1, (r.peNow - r.min) / span)) : 0.5;
      var med = span > 0 ? (r.median - r.min) / span : 0.5;
      var cx = x0 + w * pos;
      var fontSize = narrow ? 10.5 : 11.5;
      return {
        type: 'group',
        children: [
          { type: 'rect', shape: { x: x0, y: cy - barH / 2, width: w, height: barH, r: barH / 2 }, style: { fill: gradient } },
          { type: 'rect', shape: { x: x0 + w * med - 1, y: cy - barH / 2 - 3, width: 2, height: barH + 6 }, style: { fill: 'rgba(31,35,40,0.55)' } },
          { type: 'circle', shape: { cx: cx, cy: cy, r: barH * 0.5 + 3 }, style: { fill: '#fff', stroke: palette.fg, lineWidth: 2, shadowBlur: 3, shadowColor: 'rgba(0,0,0,0.25)' } },
          { type: 'text', style: { x: x0 - 6, y: cy, text: fmt.num(r.min, 1), textAlign: 'right', textVerticalAlign: 'middle', fontSize: 10.5, fill: palette.muted } },
          { type: 'text', style: { x: x1 + 6, y: cy, text: fmt.num(r.max, 1), textAlign: 'left', textVerticalAlign: 'middle', fontSize: 10.5, fill: palette.muted } },
          { type: 'text', style: { x: x0, y: cy + barH / 2 + 5, text: labelFor(r, narrow, fmt), textAlign: 'left', textVerticalAlign: 'top', fontSize: fontSize, fill: palette.fg } }
        ]
      };
    }

    return {
      animation: false,
      tooltip: {
        trigger: 'item', confine: true, textStyle: { fontSize: 12 },
        formatter: function (p) {
          var r = rows[p.dataIndex];
          return '<b>' + r.name + '</b>　PE ' + fmt.num(r.peNow, 2) + '（' + r.peDate + '）<br>' +
            '全历史分位 ' + pctText(r.pctAll) + ' · 近10年 ' + pctText(r.pct10y) + ' · 近5年 ' + pctText(r.pct5y) + '<br>' +
            '月末样本 ' + r.nMonths + ' 个，自 ' + r.sampleStart + '<br>' +
            '最低 ' + fmt.num(r.min, 2) + ' · 中位 ' + fmt.num(r.median, 2) + ' · 最高 ' + fmt.num(r.max, 2) + '<br>' +
            '<span style="color:' + palette.muted + '">' + r.sourceNote + '</span>';
        }
      },
      grid: { left: narrow ? 64 : 96, right: narrow ? 34 : 44, top: 6, bottom: 6 },
      xAxis: { type: 'value', min: -14, max: 114, show: false },
      yAxis: {
        type: 'category', inverse: true, data: rows.map(function (r) { return r.name; }),
        axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { color: palette.fg, fontSize: narrow ? 11.5 : 13, fontWeight: 500, margin: 10 }
      },
      series: [{
        type: 'custom',
        name: '估值区间',
        renderItem: renderItem,
        encode: { x: 0, y: 1 },
        data: rows.map(function (r, i) { return [r.pctAll, i]; })
      }]
    };
  }

  function table(rows, ctx, container) {
    var fmt = ctx.fmt;
    function pctCell(x) {
      if (x === null || x === undefined) return { t: '–', v: null, cls: 'dim' };
      return { t: fmt.pct(x, 1), v: x, style: 'background:' + C.withAlpha(C.gradientGreenRed(x / 100), 0.22) };
    }
    var data = rows.map(function (r) {
      return [
        { t: r.name + (r.shortSample ? '（样本短）' : ''), v: r.name },
        { t: fmt.num(r.peNow, 2), v: r.peNow },
        pctCell(r.pctAll), pctCell(r.pct10y), pctCell(r.pct5y),
        { t: fmt.num(r.min, 2), v: r.min }, { t: fmt.num(r.median, 2), v: r.median }, { t: fmt.num(r.max, 2), v: r.max },
        { t: r.sampleStart, v: r.sampleStart }, { t: String(r.nMonths), v: r.nMonths },
        { t: r.source === 'legulegu' ? '乐咕' : '中证', v: r.source, title: r.sourceNote }
      ];
    });
    C.makeTable(container,
      [{ text: '指数', numeric: false }, '当前 PE', '全历史分位', '近10年', '近5年', '最低', '中位', '最高',
       { text: '样本起点', numeric: false }, '月数', { text: 'PE 源', numeric: false }],
      data, { sortable: true, defaultSort: { col: 2, desc: true }, caption: '点击表头排序。分位含等号（历史月末 PE ≤ 当前值的占比）；近10年 / 近5年分别要求 120 / 60 个月末样本。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var rows = d.indexes.slice();

    var legend = C.el('div', 'val-legend');
    legend.appendChild(document.createTextNode('最低'));
    legend.appendChild(C.el('span', 'grad'));
    legend.appendChild(document.createTextNode('最高 · 白圈 = 当前 PE · 竖线 = 中位数 · 条两端数字 = 历史最低 / 最高 PE'));
    body.appendChild(legend);

    var chartEl = C.el('div', 'chart chart-auto');
    chartEl.style.height = (rows.length * ROW_H + 16) + 'px';
    body.appendChild(chartEl);
    var narrow = body.clientWidth < NARROW_PX;
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    chart.setOption(chartOption(rows, ctx, narrow));

    var timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var now = body.clientWidth < NARROW_PX;
        if (now !== narrow) {
          narrow = now;
          chart.setOption(chartOption(rows, ctx, narrow), { notMerge: true });
        }
      }, 220);
    });

    body.appendChild(C.el('h3', null, '明细'));
    table(rows, ctx, body);
    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'valuation',
    title: '主要宽基指数估值分位',
    subtitle: '滚动市盈率（整体法）当前值在自身月末历史中的位置 · 绿低红高',
    dataFile: 'valuation.json',
    bodyMinHeight: 1040,
    notes: [
      '区间条按每个指数自己的历史最低到最高拉满，所以不同行之间的颜色位置不能互相比较，只表示「相对自己的历史贵不贵」。',
      '分位数字是含等号的占比：45% 表示历史上 45% 的月末 PE 不高于现在。'
    ],
    render: render
  });
})();
