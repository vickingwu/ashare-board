/*
 * 面板 trend —— 市场趋势温度。
 *   一个 ECharts 实例、上下两个网格、时间轴（日期不等距：日度与月度序列同轴）、横轴指针联动：
 *   上为 沪深 A 股站上 250 日均线占比折线 + 50% 虚线 + <10% / >90% 底色 + 站上 50 日线折线（默认隐藏）+ 中证全指（右侧对数轴）；
 *   下为 申万一级行业 MACD DIF>0 占比的月度柱 + MACD 柱>0 占比的对照柱（默认隐藏）+ 日度折线（时间窗 ≤ 3 年时显示）。
 *   时间窗按钮只改横轴的 min/max（以及叠加线所在对数轴的范围），不切数组；读数条取 latest。
 * 数据：data/trend.json → data.daily{dates, pct250, pct50, n, above250}, monthly{dates, pctIndustryMacdPos, pctHist, nIndustries, partialLast},
 *   industryDaily{dates, pct, pctHist}, overlay{dates, csiAll}, latest{date, pct250, pct50, n, above250, industryPct, industryPctHist, industryN, industryDate}, universe{…}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [
    { label: '全部', years: null }, { label: '10年', years: 10 }, { label: '5年', years: 5 }, { label: '3年', years: 3 }, { label: '1年', years: 1 }
  ];
  var DEFAULT_RANGE = 1;
  var DAILY_MAX_YEARS = 3;       // 行业日度折线只在 ≤ 3 年的时间窗里显示
  var NAMES = { pct250: '站上年线', pct50: '站上 50 日线', csi: '中证全指', mPos: '行业 MACD>0（月度）', mHist: 'MACD 柱>0（月度）', dPos: '行业 MACD>0（日度）' };

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }

  // 时间轴指针的时间戳 → YYYY-MM-DD（ECharts 把不带时区的日期串按本地时间解析，这里按本地时间还原）
  function tsToDate(ts) {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return null;
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // 升序 dates 里最后一个 ≤ dateStr 的下标；没有则 -1
  function lastIndexOnOrBefore(dates, dateStr) {
    var i = C.firstIndexOnOrAfter(dates, dateStr);
    if (i < 0) return dates.length - 1;
    return dates[i] === dateStr ? i : i - 1;
  }

  // 窗口内叠加线的取值范围（对数轴上下各留 3%），窗口没有有效值时返回 null
  function overlayRange(dates, values, startDate) {
    var i0 = startDate ? Math.max(0, C.firstIndexOnOrAfter(dates, startDate)) : 0;
    var lo = Infinity, hi = -Infinity;
    for (var i = i0; i < values.length; i++) {
      var v = values[i];
      if (isNum(v) && v > 0) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    return lo < hi ? { min: lo / 1.03, max: hi * 1.03 } : null;
  }

  function readout(d, ctx) {
    var fmt = ctx.fmt, lt = d.latest;
    var box = C.el('div', 'readout');
    function item(label, value) {
      var sp = C.el('span');
      sp.appendChild(document.createTextNode(label + ' '));
      sp.appendChild(C.el('strong', null, value));
      box.appendChild(sp);
    }
    var posN = (isNum(lt.industryPct) && isNum(lt.industryN)) ? Math.round(lt.industryPct / 100 * lt.industryN) : null;
    item('站上年线', fmt.pct(lt.pct250, 1) + '（' + fmt.num(lt.n) + ' 只）');
    item('站上 50 日线', fmt.pct(lt.pct50, 1));
    item('行业 MACD>0', fmt.pct(lt.industryPct, 1) + (posN === null ? '' : '（' + posN + '/' + lt.industryN + '）'));
    item('截至', lt.date);
    if (lt.industryDate && lt.industryDate !== lt.date) item('行业截至', lt.industryDate);
    return box;
  }

  // layout = {narrow}；state = {start(窗口起点或 null), showDaily, selected(图例状态)}
  function chartOption(d, ctx, layout, state) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var daily = d.daily, monthly = d.monthly, ind = d.industryDaily, csi = d.overlay.csiAll;
    var left = narrow ? 46 : 64, right = narrow ? 54 : 72;
    var legendFont = narrow ? 11 : 12;
    // 窄屏：图例会折成两行，下图整体下移留出空间；坐标轴名称与居中的图例重叠，隐藏（图例与读数已说明含义）
    var axisName = function (s) { return narrow ? '' : s; };
    var legend2Top = narrow ? '54%' : '56%', grid2Top = narrow ? '64%' : '62%', grid2Height = narrow ? '28%' : '30%';
    var selected = Object.assign({}, state.selected || {});
    if (selected[NAMES.pct50] === undefined) selected[NAMES.pct50] = false;
    if (selected[NAMES.mHist] === undefined) selected[NAMES.mHist] = false;
    var csiRange = overlayRange(daily.dates, csi, state.start);

    function tooltip(params) {
      if (!params || !params.length) return '';
      var p = null;
      for (var k = 0; k < params.length; k++) if (params[k].axisIndex === 0) { p = params[k]; break; }
      var date = tsToDate((p || params[0]).axisValue);
      if (!date) return '';
      var i = lastIndexOnOrBefore(daily.dates, date);
      if (i < 0) return '';
      var day = daily.dates[i];
      var m = lastIndexOnOrBefore(monthly.dates, day);
      var di = C.firstIndexOnOrAfter(ind.dates, day);
      var hasDaily = di >= 0 && ind.dates[di] === day;
      var html = '<b>' + day + '</b><br>' +
        NAMES.pct250 + ' ' + fmt.pct(daily.pct250[i], 1) + '（' + fmt.num(daily.above250[i]) + ' / ' + fmt.num(daily.n[i]) + ' 只）<br>' +
        NAMES.pct50 + ' ' + fmt.pct(daily.pct50[i], 1) + '<br>' +
        NAMES.csi + ' ' + fmt.num(csi[i], 2);
      if (m >= 0) {
        html += '<br>行业 MACD>0 ' + fmt.pct(monthly.pctIndustryMacdPos[m], 1) + '（柱>0 ' + fmt.pct(monthly.pctHist[m], 1) +
          '，' + monthly.nIndustries[m] + ' 个行业，月度 ' + monthly.dates[m] + '）';
      }
      if (hasDaily) html += '<br>当日行业 MACD>0 ' + fmt.pct(ind.pct[di], 1) + '（柱>0 ' + fmt.pct(ind.pctHist[di], 1) + '）';
      return html;
    }

    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' }, formatter: tooltip },
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#9CA3AF' } },
      legend: [
        { top: 0, textStyle: { fontSize: legendFont }, itemWidth: 18, itemHeight: 3, selected: selected,
          data: [NAMES.pct250, NAMES.pct50, NAMES.csi] },
        { top: legend2Top, textStyle: { fontSize: legendFont }, itemWidth: 18, itemHeight: 3, selected: selected,
          data: [NAMES.mPos, NAMES.mHist, NAMES.dPos] }
      ],
      grid: [
        { left: left, right: right, top: 36, height: '46%' },
        { left: left, right: right, top: grid2Top, height: grid2Height }
      ],
      xAxis: [
        C.timeAxis({ gridIndex: 0, min: state.start, max: null, labels: false }),
        C.timeAxis({ gridIndex: 1, min: state.start, max: null })
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, min: 0, max: 100, interval: 25, name: axisName('站上年线 %'),
          nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } },
        Object.assign(C.logAxis({ name: axisName(NAMES.csi) }), {
          gridIndex: 0, position: 'right', splitLine: { show: false }, minorSplitLine: { show: false },
          min: csiRange ? csiRange.min : null, max: csiRange ? csiRange.max : null
        }),
        { type: 'value', gridIndex: 1, min: 0, max: 100, interval: 25, name: axisName('行业 MACD>0 %'),
          nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
          axisLabel: { color: palette.muted, fontSize: 11 }, splitLine: { lineStyle: { color: '#EEF0F3' } } }
      ],
      series: [
        {
          id: 'pct250', name: NAMES.pct250, type: 'line', xAxisIndex: 0, yAxisIndex: 0,
          data: C.toTimeRows(daily.dates, daily.pct250), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1.4, color: palette.breadth250 }, itemStyle: { color: palette.breadth250 },
          markLine: {
            silent: true, symbol: 'none', animation: false,
            label: { position: 'insideEndTop', fontSize: 10, color: palette.muted, formatter: '50%' },
            lineStyle: { color: palette.muted, type: 'dashed', width: 1 },
            data: [{ yAxis: 50 }]
          },
          markArea: C.bandAreas([{ lo: 0, hi: 10 }, { lo: 90, hi: 100 }], [palette.down, palette.up], 0.10)
        },
        {
          id: 'pct50', name: NAMES.pct50, type: 'line', xAxisIndex: 0, yAxisIndex: 0,
          data: C.toTimeRows(daily.dates, daily.pct50), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1, color: palette.breadth50 }, itemStyle: { color: palette.breadth50 }
        },
        {
          id: 'csi', name: NAMES.csi, type: 'line', xAxisIndex: 0, yAxisIndex: 1,
          data: C.toTimeRows(daily.dates, csi), showSymbol: false, connectNulls: false, sampling: 'lttb',
          lineStyle: { width: 1, color: palette['000985'], opacity: 0.8 }, itemStyle: { color: palette['000985'] }
        },
        {
          id: 'mPos', name: NAMES.mPos, type: 'bar', xAxisIndex: 1, yAxisIndex: 2,
          data: C.toTimeRows(monthly.dates, monthly.pctIndustryMacdPos), barMaxWidth: 12, barMinWidth: 2,
          itemStyle: { color: palette.macdPos }
        },
        {
          id: 'mHist', name: NAMES.mHist, type: 'bar', xAxisIndex: 1, yAxisIndex: 2,
          data: C.toTimeRows(monthly.dates, monthly.pctHist), barMaxWidth: 12, barMinWidth: 2,
          itemStyle: { color: palette.macdHist }
        },
        {
          id: 'dPos', name: NAMES.dPos, type: 'line', xAxisIndex: 1, yAxisIndex: 2,
          data: state.showDaily ? C.toTimeRows(ind.dates, ind.pct) : [], showSymbol: false, connectNulls: false,
          lineStyle: { width: 1, color: palette.macdPos }, itemStyle: { color: palette.macdPos }
        }
      ]
    };
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    var fmt = ctx.fmt;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var daily = d.daily;
    var lastDate = daily.dates[daily.dates.length - 1];
    var state = { range: DEFAULT_RANGE, start: null, showDaily: false, selected: null };

    C.rangeButtons(body, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, pick);
    body.appendChild(readout(d, ctx));

    var chartEl = C.el('div', 'chart chart-trend');
    body.appendChild(chartEl);
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    var note = C.el('p', 'chart-note');
    body.appendChild(note);

    function layout() { return { narrow: body.clientWidth < NARROW_PX }; }

    // 当前图例开关状态（重建配置时保留用户的选择）
    function legendSelected() {
      var opt = chart.getOption();
      var out = {};
      ((opt && opt.legend) || []).forEach(function (lg) { Object.assign(out, lg.selected || {}); });
      return out;
    }

    function applyRange(i) {
      var years = RANGES[i].years;
      state.range = i;
      state.start = years === null ? null : C.windowStartDate(lastDate, years);
      state.showDaily = years !== null && years <= DAILY_MAX_YEARS;
    }

    function noteText() {
      var i0 = state.start ? Math.max(0, C.firstIndexOnOrAfter(daily.dates, state.start)) : 0;
      return '时间窗 ' + daily.dates[i0] + ' 至 ' + lastDate + '（' + RANGES[state.range].label + '）· 日度序列自 ' + daily.dates[0] + ' 起 · ' +
        (state.showDaily ? '下图叠加行业 MACD>0 的日度折线（最近 ' + fmt.num(d.industryDaily.dates.length) + ' 个交易日）· ' : '') +
        'MA250 ≈ 50 周线；分母为当日有成交且已有 ≥ 250 个交易日记录的股票' +
        (d.universe && isNum(d.universe.nStocksNow) ? '（现上市 ' + fmt.num(d.universe.nStocksNow) + ' 只）' : '') + '。';
    }

    function pick(i) {
      applyRange(i);
      var csiRange = overlayRange(daily.dates, d.overlay.csiAll, state.start);
      chart.setOption({
        xAxis: [{ min: state.start, max: null }, { min: state.start, max: null }],
        yAxis: [{}, { min: csiRange ? csiRange.min : null, max: csiRange ? csiRange.max : null }, {}],
        series: [{ id: 'dPos', data: state.showDaily ? C.toTimeRows(d.industryDaily.dates, d.industryDaily.pct) : [] }]
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
    id: 'trend',
    title: '市场趋势温度',
    subtitle: '沪深 A 股站上 250 日均线（后复权）的比例 · 申万一级行业 MACD 为正的比例 · 叠加中证全指',
    dataFile: 'trend.json',
    bodyMinHeight: 900,
    notes: [
      '上图蓝线是沪深 A 股中收盘价高于自身 250 日均线（后复权）的股票占比，浅蓝线为 50 日线口径（默认隐藏，可在图例里打开）；灰线是中证全指（右侧对数轴），用来对照指数位置与市场内部的广度。',
      '占比低于 10% 或高于 90% 的区域用底色标出：接近 0 说明绝大多数股票都在年线之下，接近 100 则相反；50% 虚线是中性参考。',
      '下图柱子是 31 个申万一级行业里 MACD 的 DIF > 0 的行业占比（月度，当月取最新一个交易日）；MACD 柱 > 0 的口径可在图例里打开对照。时间窗选到 3 年及以内时会叠加同口径的日度折线。'
    ],
    render: render
  });
})();
