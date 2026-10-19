/*
 * 面板 decomp —— 指数收益来源拆解。
 *   A. 全区间：每个指数一行横向堆叠柱（股息 / 盈利 / 估值），菱形标出全收益合计；
 *   B. 分年：下拉选指数，按自然年堆叠柱 + 全收益折线；
 *   C. 明细表 + 公式。
 * 数据：data/decomp.json → data.indexes[]{code,name,startDate,endDate,years,totalReturn,dividend,earnings,valuation,peStart,peEnd,cagrTotal,perYear,byYear[],peSource}, data.formula
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var PARTS = [
    { key: 'dividend', name: '股息', color: 'dividend' },
    { key: 'earnings', name: '盈利', color: 'earnings' },
    { key: 'valuation', name: '估值', color: 'valuation' }
  ];

  function partLabel(threshold) {
    return {
      show: true, position: 'inside', fontSize: 11, color: '#fff',
      formatter: function (p) { return Math.abs(p.value) >= threshold ? C.fmtPp(p.value, 0) + '%' : ''; }
    };
  }

  // ------------------------------------------------------------------ A. 全区间
  function wholeOption(idx, ctx, narrow) {
    var palette = ctx.palette;
    var cats = idx.map(function (x) {
      return narrow ? x.name : x.name + '\n' + x.startDate.slice(0, 7) + ' → ' + x.endDate.slice(0, 7) + ' · ' + x.years.toFixed(1) + ' 年';
    });
    var maxAbs = Math.max.apply(null, idx.map(function (x) { return Math.abs(x.totalReturn) + Math.max(0, -Math.min(0, x.valuation, x.earnings, x.dividend)); }));
    var series = PARTS.map(function (p) {
      return {
        name: p.name, type: 'bar', stack: 'parts', barWidth: narrow ? 16 : 22,
        data: idx.map(function (x) { return x[p.key]; }),
        itemStyle: { color: palette[p.color] },
        label: partLabel(maxAbs * 0.06),
        emphasis: { focus: 'series' }
      };
    });
    // 合计菱形画在柱子正下方（第 i 行中心向下 0.3 个行高，用第二条数值型纵轴定位）：
    // 正负分项分别向两侧堆叠，合计点常落在柱体中间，放在同一行会压住柱内标签
    series.push({
      name: '全收益合计', type: 'scatter', symbol: 'diamond', symbolSize: 11, yAxisIndex: 1,
      data: idx.map(function (x, i) { return [x.totalReturn, i + 0.3]; }),
      itemStyle: { color: palette.fg },
      label: { show: true, position: 'right', fontSize: 11, color: palette.fg, formatter: function (p) { return '合计 ' + C.fmtPp(p.value[0], 0) + '%'; } },
      tooltip: { show: false },
      z: 10
    });
    return {
      animation: false,
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true, textStyle: { fontSize: 12 },
        formatter: function (params) {
          var i = params[0].dataIndex, x = idx[i];
          return '<b>' + x.name + '</b>　' + x.startDate + ' → ' + x.endDate + '（' + x.years + ' 年）<br>' +
            '全收益 ' + C.fmtSignedPct(x.totalReturn, 1) + '（年化 ' + ctx.fmt.pct(x.cagrTotal, 2) + '）<br>' +
            PARTS.map(function (p) {
              return '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + palette[p.color] + ';margin-right:6px"></span>' +
                p.name + ' ' + C.fmtSignedPct(x[p.key], 1) + '（' + C.fmtPp(x.perYear[p.key], 1) + ' pp/年）';
            }).join('<br>') + '<br>PE ' + ctx.fmt.num(x.peStart, 1) + ' → ' + ctx.fmt.num(x.peEnd, 1);
        }
      },
      legend: { top: 0, textStyle: { fontSize: 12 }, data: PARTS.map(function (p) { return p.name; }).concat(['全收益合计']) },
      grid: { left: narrow ? 70 : 170, right: narrow ? 70 : 96, top: 34, bottom: 28 },
      xAxis: {
        type: 'value', axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return v + '%'; } },
        // onZero 必须关掉：否则横轴线会贴到第二条数值纵轴的 0 处（第一行中间）
        splitLine: { lineStyle: { color: '#EEF0F3' } }, axisLine: { show: true, onZero: false, lineStyle: { color: '#9CA3AF' } }
      },
      yAxis: [
        {
          type: 'category', inverse: true, data: cats,
          axisTick: { show: false }, axisLine: { show: false },
          axisLabel: { color: palette.fg, fontSize: narrow ? 11 : 12, lineHeight: 15 }
        },
        // 与类目轴逐行对齐的数值轴（第 i 行中心 = i），只给合计菱形用，不显示
        { type: 'value', min: -0.5, max: idx.length - 0.5, inverse: true, show: false, axisPointer: { show: false } }
      ],
      series: series
    };
  }

  // ------------------------------------------------------------------ B. 分年
  function yearOption(x, ctx, narrow) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var by = x.byYear || [];
    var cats = by.map(function (b) { return String(b.year) + (b.partial ? (narrow ? '*' : '（部分）') : ''); });
    var series = PARTS.map(function (p) {
      return {
        name: p.name, type: 'bar', stack: 'parts', barMaxWidth: 28,
        data: by.map(function (b) { return b[p.key]; }),
        itemStyle: { color: palette[p.color] },
        emphasis: { focus: 'series' }
      };
    });
    series.push({
      name: '全收益', type: 'line', data: by.map(function (b) { return b.totalReturn; }),
      symbol: 'circle', symbolSize: 6, lineStyle: { width: 1.6, color: palette.fg }, itemStyle: { color: palette.fg }, z: 10
    });
    return {
      animation: false,
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true, textStyle: { fontSize: 12 },
        formatter: function (params) {
          var i = params[0].dataIndex, b = by[i];
          return '<b>' + x.name + ' ' + b.year + '</b>' + (b.partial ? '（部分年份）' : '') + '　' + b.from + ' → ' + b.to + '<br>' +
            '全收益 ' + C.fmtSignedPct(b.totalReturn, 1) + '<br>' +
            PARTS.map(function (p) {
              return '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + palette[p.color] + ';margin-right:6px"></span>' +
                p.name + ' ' + C.fmtSignedPct(b[p.key], 1);
            }).join('<br>') + '<br>PE ' + fmt.num(b.peFrom, 1) + ' → ' + fmt.num(b.peTo, 1);
        }
      },
      legend: { top: 0, textStyle: { fontSize: 12 } },
      grid: { left: 52, right: 16, top: 34, bottom: 30 },
      xAxis: { type: 'category', data: cats, axisLabel: { color: palette.muted, fontSize: 11, interval: narrow ? 'auto' : 0, rotate: narrow ? 45 : 0 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D1D5DB' } } },
      yAxis: {
        type: 'value', axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return v + '%'; } },
        splitLine: { lineStyle: { color: '#EEF0F3' } }
      },
      series: series
    };
  }

  // ------------------------------------------------------------------ C. 表
  function table(idx, ctx, container) {
    var fmt = ctx.fmt;
    function signed(x) { return { t: C.fmtSignedPct(x, 1), v: x, cls: x < 0 ? 'neg' : 'pos' }; }
    var rows = idx.map(function (x) {
      return [
        { t: x.name, v: x.name },
        { t: x.startDate.slice(0, 7) + ' → ' + x.endDate.slice(0, 7), v: x.startDate },
        { t: x.years.toFixed(1), v: x.years },
        signed(x.totalReturn), signed(x.dividend), signed(x.earnings), signed(x.valuation),
        { t: fmt.num(x.peStart, 1) + ' → ' + fmt.num(x.peEnd, 1), v: x.peEnd / x.peStart },
        { t: fmt.pct(x.cagrTotal, 2), v: x.cagrTotal },
        { t: C.fmtPp(x.perYear.dividend, 1) + ' / ' + C.fmtPp(x.perYear.earnings, 1) + ' / ' + C.fmtPp(x.perYear.valuation, 1), v: x.perYear.totalReturn },
        { t: x.peSource === 'legulegu' ? '乐咕' : '中证', v: x.peSource, title: x.peSourceNote }
      ];
    });
    C.makeTable(container,
      [{ text: '指数', numeric: false }, { text: '区间', numeric: false }, '年数', '全收益', '股息', '盈利', '估值', 'PE 期初 → 期末', '年化全收益',
       '每年 股息 / 盈利 / 估值（pp）', { text: 'PE 源', numeric: false }],
      rows, { sortable: true, caption: '「每年」= 全区间各分项 ÷ 年数，便于区间长度不同的指数之间比较。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var idx = d.indexes;
    var narrow = body.clientWidth < 560;

    body.appendChild(C.el('h3', null, '全区间拆解'));
    var elA = C.el('div', 'chart');
    elA.style.height = (idx.length * (narrow ? 44 : 58) + 70) + 'px';
    body.appendChild(elA);
    var chartA = echarts.init(elA, null, { renderer: 'canvas' });
    chartA.setOption(wholeOption(idx, ctx, narrow));

    body.appendChild(C.el('h3', null, '分年拆解'));
    var tools = C.el('div', 'panel-tools');
    tools.appendChild(C.el('span', 'tools-label', '指数'));
    var sel = C.el('select', 'inline-select');
    sel.id = 'decomp-index-select';
    sel.name = 'decomp-index';
    sel.setAttribute('aria-label', '选择指数');
    idx.forEach(function (x, i) {
      var o = C.el('option', null, x.name);
      o.value = String(i);
      sel.appendChild(o);
    });
    tools.appendChild(sel);
    body.appendChild(tools);
    var elB = C.el('div', 'chart chart-mid');
    body.appendChild(elB);
    var chartB = echarts.init(elB, null, { renderer: 'canvas' });
    chartB.setOption(yearOption(idx[0], ctx, narrow));
    sel.addEventListener('change', function () {
      chartB.setOption(yearOption(idx[+sel.value], ctx, narrow), { notMerge: true });
    });
    body.appendChild(C.el('p', 'chart-note', '柱为当年内的 股息 / 盈利 / 估值 贡献，折线为当年全收益；标「部分」的年份不满 12 个月。'));

    body.appendChild(C.el('h3', null, '明细'));
    table(idx, ctx, body);
    body.appendChild(C.el('p', 'formula', d.formula));
    return { charts: [chartA, chartB] };
  }

  window.AshareBoard.registerPanel({
    id: 'decomp',
    title: '指数收益来源拆解',
    subtitle: '全收益 = 股息 + 盈利 + 估值（残差法 · 月末对齐）',
    dataFile: 'decomp.json',
    bodyMinHeight: 1180,
    notes: [
      '长期看，估值的贡献在正负之间来回摆动，股息与盈利是把净值推高的两个稳定来源；读图时先看柱子里绿色与蓝色的厚度。',
      '同一套配色贯穿全区间图、分年图与表格：绿 = 股息，蓝 = 盈利，橙 = 估值。'
    ],
    render: render
  });
})();
