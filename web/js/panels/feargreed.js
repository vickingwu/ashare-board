/*
 * 面板 feargreed —— 恐贪指数。
 *   头部：综合分大数字 + 区间标签（底色取 palette.fgBands）+ 日期；
 *   一个 ECharts 实例、上下两个网格、时间轴、横轴指针联动：上为综合分折线 + 五段区间底色 + 25/45/55/75 虚线 + 中证全指（右侧对数轴）；
 *   下为六个子指标分位分的细折线（图例可单独开关）；图下方是最新一日各子指标的原始值 / 分位得分 / 定义表。
 *   时间窗按钮只改横轴的 min/max（以及叠加线所在对数轴的范围），不切数组。
 * 数据：data/feargreed.json → data.dates[], composite[], subs{key:{name, unit, raw[], score[], definition}}, overlay{csiAll[]},
 *   bands[{lo, hi, label}], latest{date, composite, band, subs[{key, name, unit, raw, score, definition}]}, window, minObs, minSubs, start, nDays
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [
    { label: '全部', years: null }, { label: '5年', years: 5 }, { label: '3年', years: 3 }, { label: '1年', years: 1 }, { label: '6月', months: 6 }
  ];
  var DEFAULT_RANGE = 2;
  var SUB_ORDER = ['vol', 'turnover', 'margin', 'breadth', 'rsi', 'strength'];
  var SUB_COLOR = { vol: 'breadth50', turnover: 'turnover', margin: 'amountMa', breadth: 'breadth250', rsi: 'valuation', strength: 'dividend' };
  var NAME_COMPOSITE = '恐贪综合分', NAME_CSI = '中证全指';

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  function tsToDate(ts) {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return null;
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function lastIndexOnOrBefore(dates, dateStr) {
    var i = C.firstIndexOnOrAfter(dates, dateStr);
    if (i < 0) return dates.length - 1;
    return dates[i] === dateStr ? i : i - 1;
  }

  // 原始值按单位格式化：% → 百分数；倍 → x.xx 倍；无单位 → 1 位小数
  function fmtRaw(raw, unit, fmt) {
    if (!isNum(raw)) return '–';
    if (unit === '%') return fmt.pct(raw, 1);
    if (unit === '倍') return fmt.num(raw, 2) + ' 倍';
    return fmt.num(raw, 1) + (unit ? ' ' + unit : '');
  }

  // 综合分落在哪一段：半开区间 [lo, hi)，最后一段闭区间；没有分 → -1
  function bandIndex(bands, x) {
    if (!isNum(x)) return -1;
    for (var i = 0; i < bands.length; i++) {
      var last = i === bands.length - 1;
      if (x >= bands[i].lo && (x < bands[i].hi || (last && x <= bands[i].hi))) return i;
    }
    return -1;
  }

  function overlayRange(dates, values, startDate) {
    var i0 = startDate ? Math.max(0, C.firstIndexOnOrAfter(dates, startDate)) : 0;
    var lo = Infinity, hi = -Infinity;
    for (var i = i0; i < values.length; i++) {
      var v = values[i];
      if (isNum(v) && v > 0) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    return lo < hi ? { min: lo / 1.03, max: hi * 1.03 } : null;
  }

  function subKeys(d) {
    var keys = SUB_ORDER.filter(function (k) { return d.subs[k]; });
    Object.keys(d.subs).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    return keys;
  }

  function head(d, ctx) {
    var palette = ctx.palette, fmt = ctx.fmt, lt = d.latest;
    var box = C.el('div', 'fg-head');
    var big = C.el('span', 'fg-big', isNum(lt.composite) ? String(Math.round(lt.composite)) : '–');
    big.setAttribute('aria-label', '恐贪综合分 ' + fmt.num(lt.composite, 2));
    box.appendChild(big);
    var bi = -1;
    d.bands.forEach(function (b, i) { if (b.label === lt.band) bi = i; });
    if (bi < 0) bi = bandIndex(d.bands, lt.composite);
    var band = C.el('span', 'fg-band', lt.band || (bi >= 0 ? d.bands[bi].label : '–'));
    band.style.background = bi >= 0 ? palette.fgBands[bi] : palette.muted;
    box.appendChild(band);
    var subs = lt.subs || [];
    var nScored = subs.filter(function (s) { return isNum(s.score); }).length;
    box.appendChild(C.el('span', 'fg-date', '截至 ' + lt.date + ' · ' +
      (nScored === subs.length ? subs.length + ' 个子指标' : nScored + '/' + subs.length + ' 个子指标有分')));
    return box;
  }

  function chartOption(d, ctx, layout, state) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var left = narrow ? 44 : 56, right = narrow ? 52 : 72;
    var legendFont = narrow ? 11 : 12;
    // 窄屏：图例会折成两行，下图整体下移留出空间；坐标轴名称与居中的图例重叠，隐藏（图例与读数已说明含义）
    var axisName = function (s) { return narrow ? '' : s; };
    var legend2Top = narrow ? '52%' : '54%', grid2Top = narrow ? '62%' : '60%', grid2Height = narrow ? '30%' : '32%';
    var keys = subKeys(d);
    var csi = d.overlay.csiAll;
    var csiRange = overlayRange(d.dates, csi, state.start);
    var cuts = d.bands.slice(0, -1).map(function (b) { return { yAxis: b.hi }; });

    function tooltip(params) {
      if (!params || !params.length) return '';
      var p = null;
      for (var k = 0; k < params.length; k++) if (params[k].axisIndex === 0) { p = params[k]; break; }
      var date = tsToDate((p || params[0]).axisValue);
      if (!date) return '';
      var i = lastIndexOnOrBefore(d.dates, date);
      if (i < 0) return '';
      var comp = d.composite[i], bi = bandIndex(d.bands, comp);
      var html = '<b>' + d.dates[i] + '</b><br>恐贪 ' + fmt.num(comp, 1) + (bi >= 0 ? ' · ' + d.bands[bi].label : '');
      keys.forEach(function (k) {
        var s = d.subs[k];
        html += '<br>' + s.name + ' ' + fmt.num(s.score[i], 1) + '（' + fmtRaw(s.raw[i], s.unit, fmt) + '）';
      });
      html += '<br>' + NAME_CSI + ' ' + fmt.num(csi[i], 2);
      return html;
    }

    var subSeries = keys.map(function (k) {
      var s = d.subs[k], color = palette[SUB_COLOR[k]] || palette.muted;
      return {
        id: 'sub-' + k, name: s.name, type: 'line', xAxisIndex: 1, yAxisIndex: 2,
        data: C.toTimeRows(d.dates, s.score), showSymbol: false, connectNulls: false, sampling: 'lttb',
        lineStyle: { width: 1, color: color }, itemStyle: { color: color }
      };
    });

    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' }, formatter: tooltip },
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#9CA3AF' } },
      legend: [
        { top: 0, textStyle: { fontSize: legendFont }, itemWidth: 18, itemHeight: 3, selected: state.selected || {},
          data: [NAME_COMPOSITE, NAME_CSI] },
        { top: legend2Top, textStyle: { fontSize: legendFont }, itemWidth: 18, itemHeight: 3, selected: state.selected || {},
          data: subSeries.map(function (s) { return s.name; }) }
      ],
      grid: [
        { left: left, right: right, top: 36, height: '44%' },
        { left: left, right: right, top: grid2Top, height: grid2Height }
      ],
      xAxis: [
        C.timeAxis({ gridIndex: 0, min: state.start, max: null, labels: false }),
        C.timeAxis({ gridIndex: 1, min: state.start, max: null })
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, min: 0, max: 100, interval: 25, name: axisName('恐贪 0–100'),
          nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { show: false } },
        Object.assign(C.logAxis({ name: axisName(NAME_CSI) }), {
          gridIndex: 0, position: 'right', splitLine: { show: false }, minorSplitLine: { show: false },
          min: csiRange ? csiRange.min : null, max: csiRange ? csiRange.max : null
        }),
        { type: 'value', gridIndex: 1, min: 0, max: 100, interval: 25, name: axisName('子指标分位分'),
          nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } }
      ],
      series: [
        {
          id: 'composite', name: NAME_COMPOSITE, type: 'line', xAxisIndex: 0, yAxisIndex: 0,
          data: C.toTimeRows(d.dates, d.composite), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1.6, color: palette.fgLine }, itemStyle: { color: palette.fgLine },
          markArea: C.bandAreas(d.bands, palette.fgBands, 0.14),
          markLine: {
            silent: true, symbol: 'none', animation: false, label: { show: false },
            lineStyle: { color: '#9CA3AF', type: 'dashed', width: 1 },
            data: cuts
          }
        },
        {
          id: 'csi', name: NAME_CSI, type: 'line', xAxisIndex: 0, yAxisIndex: 1,
          data: C.toTimeRows(d.dates, csi), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1, color: palette['000985'], opacity: 0.8 }, itemStyle: { color: palette['000985'] }
        }
      ].concat(subSeries)
    };
  }

  function subTable(d, ctx, container) {
    var fmt = ctx.fmt;
    var rows = (d.latest.subs || []).map(function (s) {
      return [
        { t: s.name, v: s.name },
        { t: fmtRaw(s.raw, s.unit, fmt), v: s.raw, cls: 'num' },
        { t: fmt.num(s.score, 1), v: s.score, cls: 'num' },
        { t: s.definition || '', v: s.definition || '', cls: 'def' }
      ];
    });
    C.makeTable(container, [{ text: '子指标', numeric: false }, '原始值', '分位得分', { text: '定义', numeric: false }], rows,
      { className: 'fg-table', caption: '分位得分 = 当日原始值在最近 ' + fmt.num(d.window) + ' 个交易日里的分位（0–100，波动率取 100 − 分位）；留空表示当日该子指标无分。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    var fmt = ctx.fmt;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var lastDate = d.dates[d.dates.length - 1];
    var state = { range: DEFAULT_RANGE, start: null, selected: null };

    C.rangeButtons(body, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, pick);
    body.appendChild(head(d, ctx));

    var chartEl = C.el('div', 'chart chart-fg');
    body.appendChild(chartEl);
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    var note = C.el('p', 'chart-note');
    body.appendChild(note);
    body.appendChild(C.el('h3', null, '子指标（' + d.latest.date + '）'));
    subTable(d, ctx, body);

    function layout() { return { narrow: body.clientWidth < NARROW_PX }; }

    function legendSelected() {
      var opt = chart.getOption();
      var out = {};
      ((opt && opt.legend) || []).forEach(function (lg) { Object.assign(out, lg.selected || {}); });
      return out;
    }

    function applyRange(i) {
      var r = RANGES[i];
      state.range = i;
      if (r.years) state.start = C.windowStartDate(lastDate, r.years);
      else if (r.months) state.start = C.monthsBack(lastDate, r.months);
      else state.start = null;
    }

    function noteText() {
      var i0 = state.start ? Math.max(0, C.firstIndexOnOrAfter(d.dates, state.start)) : 0;
      return '时间窗 ' + d.dates[i0] + ' 至 ' + lastDate + '（' + RANGES[state.range].label + '）· 序列自 ' + d.dates[0] +
        ' 起（首个有 ≥ ' + fmt.num(d.minSubs) + ' 个子指标分数的交易日）· 分位窗口 ' + fmt.num(d.window) + ' 个交易日 · 底色自下而上：' +
        d.bands.map(function (b) { return b.label + ' ' + b.lo + '–' + b.hi; }).join('、') + '。';
    }

    function pick(i) {
      applyRange(i);
      var csiRange = overlayRange(d.dates, d.overlay.csiAll, state.start);
      chart.setOption({
        xAxis: [{ min: state.start, max: null }, { min: state.start, max: null }],
        yAxis: [{}, { min: csiRange ? csiRange.min : null, max: csiRange ? csiRange.max : null }, {}]
      });
      note.textContent = noteText();
    }

    applyRange(DEFAULT_RANGE);
    chart.setOption(chartOption(d, ctx, layout(), state));
    note.textContent = noteText();

    var lastWidth = chartEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (chartEl.clientWidth !== lastWidth) {
          lastWidth = chartEl.clientWidth;
          state.selected = legendSelected();
          chart.setOption(chartOption(d, ctx, layout(), state), { notMerge: true });
        }
      }, 260);
    });

    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'feargreed',
    title: '恐贪指数',
    subtitle: '六个子指标各自滚动 500 日分位的等权平均 · 0 恐慌 — 100 贪婪 · 叠加中证全指',
    dataFile: 'feargreed.json',
    bodyMinHeight: 1000,
    notes: [
      '综合分越低表示市场情绪越偏恐慌，越高越偏贪婪；五段底色对应 0–25 极度恐慌、25–45 恐慌、45–55 中性、55–75 贪婪、75–100 极度贪婪。',
      '六个子指标（波动率、相对换手、两融、市场宽度、RSI、涨跌强度）各自在最近 500 个交易日里算分位（0–100），波动率取反向，等权平均得到综合分；当日有分的子指标不足 4 个时综合分留空。',
      '下图是六个子指标的分位分，可在图例里单独开关；表格列出最新一日每个子指标的原始值、分位得分与定义。'
    ],
    render: render
  });
})();
