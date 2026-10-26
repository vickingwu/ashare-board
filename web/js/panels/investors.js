/*
 * 面板 investors —— 投资者结构。
 *   八张指标卡片（沪市两融余额 / 融资余额 / 两融占流通市值比 / 沪市 A 股流通市值与换手率 / 全 A 流通市值、成交额、20 日均换手）；
 *   一张时间轴折线：沪市融资融券余额与融资余额（左轴，亿元）叠加中证全指（右侧对数轴）；时间窗按钮只改横轴 min 与两条纵轴的范围。
 * 数据：data/investors.json → data.cards[]{key,label,value,unit,date,scope,note}, chart{dates[],total[],rzye[],csiAll[]},
 *   latest{date,total,rzye,ratioPct}, dropped[]{key,name,reason}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [{ label: '全部', years: null }, { label: '5年', years: 5 }, { label: '3年', years: 3 }, { label: '1年', years: 1 }];
  var DEFAULT_RANGE = 1;
  var NAMES = { total: '融资融券余额', rzye: '融资余额', csi: '中证全指' };

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  // 窗口内的取值范围；pad 为上下各留的比例；没有有效值时 null
  function rangeIn(values, i0, pad) {
    var lo = Infinity, hi = -Infinity;
    for (var i = i0; i < values.length; i++) {
      var v = values[i];
      if (isNum(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    return lo < hi ? { min: lo / (1 + pad), max: hi * (1 + pad) } : null;
  }

  // 线性轴的整齐边界：按跨度的量级取步长，下界向下、上界向上取整到步长
  function niceBounds(r) {
    if (!r) return { min: null, max: null };
    var span = r.max - r.min;
    if (!(span > 0)) return { min: null, max: null };
    var step = Math.pow(10, Math.floor(Math.log(span) / Math.LN10));
    if (span / step < 2) step /= 5; else if (span / step < 5) step /= 2;
    return { min: Math.max(0, Math.floor(r.min / step) * step), max: Math.ceil(r.max / step) * step };
  }

  function cards(d, ctx) {
    var fmt = ctx.fmt;
    var grid = C.el('div', 'cards');
    d.cards.forEach(function (c) {
      var card = C.el('div', 'card');
      card.setAttribute('data-key', c.key);
      if (c.note) card.title = c.note;
      card.appendChild(C.el('div', 'card-label', c.label));
      var val = C.el('div', 'card-value', isNum(c.value) ? fmt.num(c.value, c.unit === '%' ? 2 : 0) : '–');
      if (isNum(c.value) && c.unit) val.appendChild(C.el('span', 'card-unit', c.unit));
      card.appendChild(val);
      card.appendChild(C.el('div', 'card-meta', (c.date || '–') + ' · ' + (c.scope || '')));
      grid.appendChild(card);
    });
    return grid;
  }

  // state = {start}
  function chartOption(d, ctx, layout, state) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var ch = d.chart, dates = ch.dates;
    var i0 = state.start ? Math.max(0, C.firstIndexOnOrAfter(dates, state.start)) : 0;
    var left = niceBounds(rangeIn(ch.total, i0, 0.04));
    var csiRange = rangeIn(ch.csiAll, i0, 0.03);
    var axisName = function (s) { return narrow ? '' : s; };

    function tooltip(params) {
      if (!params || !params.length) return '';
      var date = params[0].data[0];
      var i = C.firstIndexOnOrAfter(dates, date);
      if (i < 0 || dates[i] !== date) return '';
      return '<b>' + date + '</b><br>' + NAMES.total + ' ' + fmt.yi(ch.total[i], 2) + '<br>' + NAMES.rzye + ' ' + fmt.yi(ch.rzye[i], 2) +
        '<br>' + NAMES.csi + ' ' + fmt.num(ch.csiAll[i], 2);
    }

    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' }, formatter: tooltip },
      legend: { show: false },
      grid: { left: narrow ? 56 : 72, right: narrow ? 54 : 72, top: 24, bottom: 32 },
      xAxis: C.timeAxis({ min: state.start, max: null }),
      yAxis: [
        { type: 'value', name: axisName('亿元'), nameTextStyle: { color: palette.muted, fontSize: 11, align: 'right' },
          min: left.min, max: left.max, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return fmt.num(v, 0); } },
          splitLine: { lineStyle: { color: '#E5E7EB' } } },
        Object.assign(C.logAxis({ name: axisName(NAMES.csi) }), {
          position: 'right', splitLine: { show: false }, minorSplitLine: { show: false },
          min: csiRange ? csiRange.min : null, max: csiRange ? csiRange.max : null
        })
      ],
      series: [
        { id: 'total', name: NAMES.total, type: 'line', yAxisIndex: 0, data: C.toTimeRows(dates, ch.total), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1.6, color: palette.marginTotal }, itemStyle: { color: palette.marginTotal } },
        { id: 'rzye', name: NAMES.rzye, type: 'line', yAxisIndex: 0, data: C.toTimeRows(dates, ch.rzye), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1.2, color: palette.marginFin }, itemStyle: { color: palette.marginFin } },
        { id: 'csi', name: NAMES.csi, type: 'line', yAxisIndex: 1, data: C.toTimeRows(dates, ch.csiAll), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1, type: 'dashed', color: palette['000985'], opacity: 0.85 }, itemStyle: { color: palette['000985'] } }
      ]
    };
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var dates = d.chart.dates;
    var lastDate = dates[dates.length - 1];
    var state = { range: DEFAULT_RANGE, start: null };

    body.appendChild(cards(d, ctx));
    body.appendChild(C.el('h3', null, '沪市两融余额走势'));
    var tools = C.el('div', 'panel-tools');
    body.appendChild(tools);
    C.rangeButtons(tools, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, pick);
    var legendHost = C.el('div');
    body.appendChild(legendHost);
    var chartEl = C.el('div', 'chart chart-margin');
    body.appendChild(chartEl);
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    var note = C.el('p', 'chart-note');
    body.appendChild(note);

    function layout() { return { narrow: body.clientWidth < NARROW_PX }; }

    function applyRange(i) {
      state.range = i;
      var years = RANGES[i].years;
      state.start = years === null ? null : C.windowStartDate(lastDate, years);
    }

    function noteText() {
      var i0 = state.start ? Math.max(0, C.firstIndexOnOrAfter(dates, state.start)) : 0;
      var lt = d.latest || {};
      return '时间窗 ' + dates[i0] + ' 至 ' + lastDate + '（' + RANGES[state.range].label + '）· 序列自 ' + dates[0] + ' 起 · 最新 ' +
        NAMES.total + ' ' + ctx.fmt.yi(lt.total, 2) + '、' + NAMES.rzye + ' ' + ctx.fmt.yi(lt.rzye, 2) +
        (isNum(lt.ratioPct) ? '，占沪市 A 股流通市值 ' + ctx.fmt.pct(lt.ratioPct, 2) : '') + '；中证全指为右侧对数轴。';
    }

    function pick(i) {
      applyRange(i);
      var o = chartOption(d, ctx, layout(), state);
      chart.setOption({ xAxis: { min: state.start, max: null }, yAxis: [{ min: o.yAxis[0].min, max: o.yAxis[0].max }, { min: o.yAxis[1].min, max: o.yAxis[1].max }] });
      note.textContent = noteText();
    }

    applyRange(DEFAULT_RANGE);
    chart.setOption(chartOption(d, ctx, layout(), state));
    note.textContent = noteText();
    C.legendToggle(legendHost, [
      { name: NAMES.total, color: ctx.palette.marginTotal },
      { name: NAMES.rzye, color: ctx.palette.marginFin },
      { name: NAMES.csi, color: ctx.palette['000985'] }
    ], chart);

    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) {
          lastWidth = chartEl.clientWidth;
          chart.setOption(chartOption(d, ctx, layout(), state), { replaceMerge: ['series'] });
        }
      }, 260);
    });

    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'investors',
    title: '投资者结构',
    subtitle: '沪市两融余额与占流通市值比例 · 成交与换手 · 两融余额日度走势叠加中证全指',
    dataFile: 'investors.json',
    bodyMinHeight: 700,
    notes: [
      '卡片分两组口径：「沪市」两组数字来自上海证券交易所（融资融券汇总、每日概况），「全 A」来自申万Ａ指（沪深两市全部 A 股）；比值类卡片的分子分母取同一口径。',
      '两融数据为上交所 T+1 上午发布，卡片日期即两融的最新交易日；每日概况与申万数据在当日收盘后更新，取不到当日概况时比值卡片留空并注明。',
      '图中融资融券余额 = 融资余额 + 融券余额（沪市），中证全指为右侧对数轴的叠加线；深市两融、新增投资者、公募规模与北向持股未纳入，原因见「看板说明」后续条目。'
    ],
    render: render
  });
})();
