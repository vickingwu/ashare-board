/*
 * 面板 turnover —— 全市场成交金额与换手率。
 *   一个 ECharts 实例、上下两个网格、横轴指针联动：上为日成交额柱 + 20 日均线；下为换手率折线 + 所选时间窗的均值 ±1σ 阴影 + 当前点。
 *   时间窗按钮（1年 / 3年 / 5年 / 10年 / 全部）重切所有数组并重算均值、σ 与分位；读数条随之更新。
 *   换手率 = 当日成交额 ÷ 当日流通市值（申万口径），流通市值缺失的日子留空、不插值。
 * 数据：data/turnover.json → data.dates[], amount[], mv[], turnover[], ma20[], swTurnover[], mvLabel, scope, gaps[]{from,to,n,nPresent},
 *   summary{latest{date,amount,ma20,mv,turnover,swTurnover}, pctAll, nDays, nTurnover, start}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [
    { label: '1年', years: 1 }, { label: '3年', years: 3 }, { label: '5年', years: 5 }, { label: '10年', years: 10 }, { label: '全部', years: null }
  ];
  var DEFAULT_RANGE = 2;

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  // 亿元 → 「x.xx 万亿」或「x,xxx 亿」
  function fmtAmount(x, fmt) {
    if (!isNum(x)) return '–';
    return x >= 10000 ? fmt.num(x / 10000, 2) + ' 万亿' : fmt.num(x, 0) + ' 亿';
  }

  function sliceWindow(d, startIdx) {
    var W = {};
    ['dates', 'amount', 'mv', 'turnover', 'ma20', 'swTurnover'].forEach(function (k) { W[k] = d[k].slice(startIdx); });
    W.stats = C.meanStd(W.turnover);
    return W;
  }

  // 短窗口（≤ 3 年）按月标刻度：刻度落在该月的第一个交易日，文本 YYYY-MM，每 step 个月标一个
  function monthLabelFormatter(dates, step) {
    var m0 = dates.length ? +dates[0].slice(0, 4) * 12 + +dates[0].slice(5, 7) : 0;
    return function (value, idx) {
      var prev = dates[idx - 1];
      if (prev && prev.slice(0, 7) === value.slice(0, 7)) return '';
      var m = +value.slice(0, 4) * 12 + +value.slice(5, 7);
      return ((m - m0) % step === 0) ? value.slice(0, 7) : '';
    };
  }

  function chartOption(d, W, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var latest = d.summary.latest;
    var dates = W.dates, last = dates.length - 1;
    var left = narrow ? 50 : 64, right = narrow ? 14 : 24;
    var plotW = Math.max(80, (layout.width || 1000) - left - right);
    var spanYears = C.yearsBetween(dates[0], dates[last]) || 0;
    var labelFormatter;
    if (spanYears <= 3.2) {
      var nMonths = Math.max(1, Math.round(spanYears * 12));
      labelFormatter = monthLabelFormatter(dates, Math.max(1, Math.ceil(nMonths / Math.max(1, Math.floor(plotW / 56)))));
    } else {
      labelFormatter = C.yearLabelFormatter(dates, C.yearLabelStep(dates, plotW));
    }
    var axisLabel = { color: palette.muted, fontSize: 11, interval: 0, showMaxLabel: false, formatter: labelFormatter };
    var mean = W.stats.mean, std = W.stats.std;
    var bands = isNum(mean) ? C.sigmaBands(mean, std, palette.muted, 2) : null;
    var turnoverSeries = {
      name: '换手率', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: W.turnover,
      showSymbol: false, connectNulls: false, lineStyle: { width: 1.3, color: palette.turnover }, itemStyle: { color: palette.turnover }
    };
    if (bands) {
      turnoverSeries.markArea = bands.markArea;
      // 均值标签放在左端：右端已经有「当前」点的标签
      turnoverSeries.markLine = {
        silent: true, symbol: 'none', animation: false,
        label: Object.assign({}, bands.markLine.label, { position: 'insideStartTop' }),
        data: bands.markLine.data.slice(0, 1)
      };
    }

    return {
      animation: false,
      tooltip: {
        trigger: 'axis', confine: true, textStyle: { fontSize: 12 },
        formatter: function (params) {
          if (!params || !params.length) return '';
          var i = params[0].dataIndex;
          var t = W.turnover[i];
          var tTxt = isNum(t) ? fmt.pct(t, 2) + (isNum(W.swTurnover[i]) ? '（申万口径换手率 ' + fmt.pct(W.swTurnover[i], 2) + '）' : '') : '—（流通市值缺失）';
          return '<b>' + dates[i] + '</b><br>' +
            '成交额 ' + fmt.num(W.amount[i], 0) + ' 亿' + (isNum(W.ma20[i]) ? '（20 日均 ' + fmt.num(W.ma20[i], 0) + ' 亿）' : '') + '<br>' +
            '换手率 ' + tTxt + '<br>' +
            '流通市值 ' + (isNum(W.mv[i]) ? fmtAmount(W.mv[i], fmt) : '—');
        }
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#9CA3AF' } },
      grid: [
        { left: left, right: right, top: 28, height: '40%' },
        { left: left, right: right, top: '58%', height: '34%' }
      ],
      xAxis: [
        { type: 'category', gridIndex: 0, data: dates, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D1D5DB' } } },
        { type: 'category', gridIndex: 1, data: dates, boundaryGap: false, axisLabel: axisLabel, axisTick: { show: false }, axisLine: { lineStyle: { color: '#D1D5DB' } } }
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, name: '成交额（亿元）', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return v >= 10000 ? fmt.num(v / 10000, (v / 10000) % 1 ? 1 : 0) + ' 万亿' : fmt.num(v, 0); } },
          splitLine: { lineStyle: { color: '#EEF0F3' } } },
        { type: 'value', gridIndex: 1, name: '换手率（%）', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' }, scale: true,
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } }
      ],
      series: [
        {
          name: '成交额', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: W.amount,
          large: true, largeThreshold: 600, barCategoryGap: '20%', itemStyle: { color: palette.amount }
        },
        {
          name: '20 日均', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: W.ma20,
          showSymbol: false, connectNulls: false, lineStyle: { width: 1.4, color: palette.amountMa }, itemStyle: { color: palette.amountMa }
        },
        turnoverSeries,
        {
          name: '当前', type: 'scatter', xAxisIndex: 1, yAxisIndex: 1, data: [[last, latest.turnover]],
          symbolSize: 9, itemStyle: { color: palette.up, borderColor: palette.neutral, borderWidth: 1.5 },
          label: { show: true, position: narrow ? 'top' : 'left', fontSize: 11, color: palette.up,
                   formatter: function () { return '当前 ' + fmt.pct(latest.turnover, 2); } },
          tooltip: { show: false }
        }
      ]
    };
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    var fmt = ctx.fmt;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var latest = d.summary.latest;
    var lastDate = d.dates[d.dates.length - 1];
    var pctAll = C.percentileRank(d.turnover, latest.turnover);
    var state = { range: DEFAULT_RANGE, W: null };

    C.rangeButtons(body, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, pick);

    var box = C.el('div', 'readout');
    function item(label, value) {
      var sp = C.el('span');
      sp.appendChild(document.createTextNode(label + ' '));
      var strong = C.el('strong', null, value);
      sp.appendChild(strong);
      box.appendChild(sp);
      return strong;
    }
    item('最新成交额', fmtAmount(latest.amount, fmt));
    item('20 日均', fmtAmount(latest.ma20, fmt));
    item('换手率', fmt.pct(latest.turnover, 2));
    var pctWinEl = item('窗口内分位', '–');
    item('全历史分位', isNum(pctAll) ? Math.round(pctAll) + '%' : '–');
    item('截至', latest.date);
    body.appendChild(box);

    var chartEl = C.el('div', 'chart chart-turnover');
    body.appendChild(chartEl);
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    var note = C.el('p', 'chart-note');
    body.appendChild(note);

    function layout() { return { narrow: body.clientWidth < NARROW_PX, width: chartEl.clientWidth || body.clientWidth }; }

    function windowStart(i) {
      var years = RANGES[i].years;
      if (years === null) return 0;
      var idx = C.firstIndexOnOrAfter(d.dates, C.windowStartDate(lastDate, years));
      return idx < 0 ? 0 : idx;
    }

    function gapText() {
      return (d.gaps || []).map(function (g) {
        return g.from + ' 至 ' + g.to + ' 流通市值缺失（' + g.n + ' 个交易日），换手率留空';
      }).join('；');
    }

    function pick(i) {
      state.range = i;
      var W = sliceWindow(d, windowStart(i));
      state.W = W;
      var pctWin = C.percentileRank(W.turnover, latest.turnover);
      pctWinEl.textContent = isNum(pctWin) ? Math.round(pctWin) + '%' : '–';
      chart.setOption(chartOption(d, W, ctx, layout()), { notMerge: true });
      var m = W.stats.mean, s = W.stats.std;
      var nT = W.turnover.filter(isNum).length;
      note.textContent = '时间窗 ' + W.dates[0] + ' 至 ' + W.dates[W.dates.length - 1] + ' 共 ' + fmt.num(W.dates.length) + ' 个交易日（有换手率 ' + fmt.num(nT) + ' 日）：' +
        (isNum(m) ? '换手率均值 ' + fmt.pct(m, 2) + '，阴影为 ±1σ（' + fmt.pct(m - s, 2) + ' – ' + fmt.pct(m + s, 2) + '）' : '窗口内无换手率') +
        '。' + (d.gaps && d.gaps.length ? gapText() + '。' : '') + ' 流通市值口径：' + d.mvLabel + '；范围：' + d.scope + '。';
    }

    pick(DEFAULT_RANGE);

    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) {
          lastWidth = chartEl.clientWidth;
          chart.setOption(chartOption(d, state.W, ctx, layout()), { notMerge: true });
        }
      }, 260);
    });

    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'turnover',
    title: '全市场成交金额与换手率',
    subtitle: '沪深 A 股日成交额与 20 日均线 · 换手率 = 成交额 ÷ 流通市值 · 窗口均值 ±1σ',
    dataFile: 'turnover.json',
    bodyMinHeight: 900,
    notes: [
      '成交额看绝对热度，换手率把成交额除以流通市值，剔除了市场变大带来的自然增长，更适合跨年份比较。',
      '阴影是所选时间窗内换手率的均值 ±1 个标准差；换窗口时均值、σ 和「窗口内分位」都会重算，「全历史分位」固定按全部样本。'
    ],
    render: render
  });
})();
