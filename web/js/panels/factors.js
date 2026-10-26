/*
 * 面板 factors —— 风格因子净值。
 *   一组时间窗按钮驱动两张图：上图 = 六个中证风格 / 策略指数在窗口起点归一为 100 的对数坐标折线（价格指数虚线），线末标注窗口内年化；
 *   下图 = 相对强弱：每个因子 ÷ 同口径的中证全指（全收益 ÷ H00985，价格 ÷ 000985），窗口起点归一为 100，100 处虚线；
 *   两图共用一个自绘图例；下方表格给各因子自起点以来的倍数、年化与最大回撤。
 * 数据：data/factors.json → data.dates[], start, series[]{key,name,code,kind("tr"|"price"),universe,values[]},
 *   bench{tr{code,name,values[]}, price{code,name,values[]}}, summary[]{key,name,code,kind,start,end,first,last,multiple,years,cagrAll,cagr10y,cagr5y,cagr3y,maxDrawdown}, lagging[]
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 520;
  var RANGES = [{ label: '全部', years: 0 }, { label: '10年', years: 10 }, { label: '5年', years: 5 }, { label: '3年', years: 3 }, { label: '1年', years: 1 }];
  var DEFAULT_RANGE = 0;
  var COLOR_KEY = { dividend: 'factDividend', lowvol: 'factLowVol', quality: 'factQuality', momentum: 'factMomentum', value: 'factValue', growth: 'factGrowth' };

  function colorOf(s, palette) { return palette[COLOR_KEY[s.key]] || palette.muted; }
  function kindText(kind) { return kind === 'tr' ? '全收益' : '价格'; }

  // 因子 ÷ 同口径基准（任一为空则为空）；返回与 series 同形的 {key, name, kind, values}
  function ratioSeries(d) {
    return d.series.map(function (s) {
      var bench = (s.kind === 'tr' ? d.bench.tr : d.bench.price).values;
      var out = new Array(s.values.length);
      for (var i = 0; i < s.values.length; i++) {
        var a = s.values[i], b = bench[i];
        out[i] = (a === null || a === undefined || b === null || b === undefined || b <= 0) ? null : a / b;
      }
      return { key: s.key, name: s.name, kind: s.kind, values: out };
    });
  }

  function startIndex(d, rangeIdx) {
    var dates = d.dates;
    var years = RANGES[rangeIdx].years;
    var i = years ? C.firstIndexOnOrAfter(dates, C.windowStartDate(dates[dates.length - 1], years)) : 0;
    return i < 0 ? 0 : i;
  }

  // 两张图共用的骨架：对数轴 + 年份刻度 + 线末标注；mode = 'nav' | 'rel'
  function chartOption(winDates, series, ctx, layout, mode) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var grid = { left: 56, right: narrow ? 96 : 124, top: 16, bottom: 30 };
    var plotW = Math.max(80, (layout.width || 1000) - grid.left - grid.right);
    var opt = C.baseLineOption({ dates: winDates, legend: false, grid: grid, yearStep: C.yearLabelStep(winDates, plotW) });
    opt.yAxis = C.logAxis({ name: mode === 'rel' ? '相对强弱（对数）' : '归一净值（对数）' });
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
        var label = p.marker + s.meta.name + '（' + kindText(s.meta.kind) + '）　';
        lines.push(mode === 'rel'
          ? label + '相对 ' + fmt.num(v, 1) + (v >= 100 ? '（跑赢）' : '（跑输）')
          : label + fmt.num(v, 1) + ' · 起点以来 ' + C.fmtMult(v / 100, 2));
      });
      return lines.join('<br>');
    };
    opt.series = series.map(function (s, i) {
      var m = s.meta;
      var color = colorOf(m, palette);
      var last = s.lastIdx >= 0 ? s.values[s.lastIdx] : null;
      var item = {
        name: m.name,
        type: 'line',
        data: s.values,
        showSymbol: false,
        sampling: 'lttb',
        connectNulls: false,
        lineStyle: { width: m.kind === 'price' ? 1.2 : 1.6, type: m.kind === 'price' ? 'dashed' : 'solid', color: color },
        itemStyle: { color: color },
        emphasis: { focus: 'series', lineStyle: { width: 2.6 } },
        endLabel: {
          show: true, fontSize: narrow ? 10 : 11, color: color, distance: 6, width: grid.right - 14, overflow: 'truncate',
          formatter: function () {
            return mode === 'rel'
              ? m.name + ' ' + C.fmtMult(last === null ? null : last / 100, 2)
              : m.name + (s.cagr === null ? '' : ' ' + fmt.pct(s.cagr, 1));
          }
        },
        labelLayout: { moveOverlap: 'shiftY' }
      };
      if (mode === 'rel' && i === 0) {
        item.markLine = {
          silent: true, symbol: 'none', animation: false,
          label: { position: 'insideStartTop', fontSize: 10, color: palette.muted, formatter: '100 = 与中证全指同步' },
          lineStyle: { color: palette.muted, type: 'dashed', width: 1 },
          data: [{ yAxis: 100 }]
        };
      }
      return item;
    });
    return opt;
  }

  function summaryTable(d, ctx, container) {
    var fmt = ctx.fmt;
    var uni = {};
    d.series.forEach(function (s) { uni[s.key] = s.universe; });
    function pct(x) { return { t: fmt.pct(x, 1), v: x, cls: x === null ? 'dim' : (x < 0 ? 'neg' : '') }; }
    var rows = d.summary.map(function (s) {
      return [
        { t: s.name, v: s.name },
        { t: s.code + ' · ' + kindText(s.kind) + ' · ' + (uni[s.key] || ''), v: s.code },
        { t: s.start, v: s.start },
        { t: C.fmtMult(s.multiple, 2), v: s.multiple },
        pct(s.cagrAll), pct(s.cagr10y), pct(s.cagr5y), pct(s.cagr3y),
        { t: fmt.pct(s.maxDrawdown, 1), v: s.maxDrawdown, cls: 'neg' }
      ];
    });
    C.makeTable(container,
      [{ text: '因子', numeric: false }, { text: '代表指数（代码·口径·样本空间）', numeric: false }, { text: '起点', numeric: false },
       '累计倍数', '年化·全程', '年化·10年', '年化·5年', '年化·3年', '最大回撤'],
      rows, { sortable: true, caption: '累计倍数与全程年化按各自首末日计算；10 / 5 / 3 年年化从最新日期同月同日往前推；最大回撤为全程收盘价口径。' });
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var ratios = ratioSeries(d);
    var current = DEFAULT_RANGE;

    var tools = C.el('div', 'panel-tools');
    body.appendChild(tools);
    C.rangeButtons(tools, RANGES.map(function (r) { return r.label; }), DEFAULT_RANGE, function (i) {
      current = i;
      apply();
    });

    var legendHost = C.el('div');
    body.appendChild(legendHost);
    var navEl = C.el('div', 'chart chart-factors');
    body.appendChild(navEl);
    body.appendChild(C.el('p', 'chart-note', '每条线在所选时间窗首日归一为 100，虚线为价格指数（不含股息）；线末数字是该窗口内的年化。'));
    body.appendChild(C.el('h3', null, '相对中证全指的强弱'));
    var relEl = C.el('div', 'chart chart-factors-rel');
    body.appendChild(relEl);
    body.appendChild(C.el('p', 'chart-note', '高于 100 = 跑赢中证全指；全收益因子除以中证全指全收益，价格因子除以中证全指价格指数，窗口首日归一为 100。'));

    var navChart = echarts.init(navEl, null, { renderer: 'canvas' });
    var relChart = echarts.init(relEl, null, { renderer: 'canvas' });

    function layout(elm) { return { narrow: body.clientWidth < NARROW_PX, width: elm.clientWidth || body.clientWidth }; }
    function apply(first) {
      var i0 = startIndex(d, current);
      var winDates = d.dates.slice(i0);
      var mergeOpt = first ? undefined : { replaceMerge: ['series'] };
      navChart.setOption(chartOption(winDates, C.normalizeWindow(d.dates, d.series, i0), ctx, layout(navEl), 'nav'), mergeOpt);
      relChart.setOption(chartOption(winDates, C.normalizeWindow(d.dates, ratios, i0), ctx, layout(relEl), 'rel'), mergeOpt);
    }
    apply(true);

    // 一个图例同时控制两张图：legendToggle 直接驱动上图，onToggle 把同一动作转发到下图
    C.legendToggle(legendHost, d.series.map(function (s) {
      return { name: s.name, label: s.name + '（' + kindText(s.kind) + '）', color: colorOf(s, ctx.palette) };
    }), navChart, {
      onToggle: function (name, hidden) { relChart.dispatchAction({ type: hidden ? 'legendUnSelect' : 'legendSelect', name: name }); }
    });

    var lastWidth = navEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (navEl.clientWidth !== lastWidth) { lastWidth = navEl.clientWidth; apply(); }
      }, 260);
    });

    body.appendChild(C.el('h3', null, '各因子长期表现'));
    summaryTable(d, ctx, body);
    return { charts: [navChart, relChart] };
  }

  window.AshareBoard.registerPanel({
    id: 'factors',
    title: '风格因子净值',
    subtitle: '六个中证风格 / 策略指数 · 2005-01-04 起 · 对数坐标 · 下图为相对中证全指的强弱',
    dataFile: 'factors.json',
    bodyMinHeight: 1120,
    notes: [
      '每个因子用一个中证指数代表（代码、口径与样本空间见表）：红利、动量、价值、成长为全收益指数，低波动与质量只有价格指数。',
      '价格指数不含成分股分红再投资，长期回报会低估大约等于股息率的部分，与全收益指数相比时请留意「口径」列。',
      '相对强弱 = 因子指数 ÷ 同口径的中证全指，在时间窗起点归一为 100：线向上说明该段时间跑赢全市场，向下则跑输。'
    ],
    render: render
  });
})();
