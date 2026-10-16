/*
 * 面板 coverage —— 宽基指数覆盖范围。三块内容：
 *   A. 格子图（ECharts custom）：全指成分按总市值排名，每 50 只一格，格子深浅 = 该格里属于某指数的只数 / 50
 *   B. 市值梯子（ECharts bar）：四级规模宽基 + 未覆盖 的市值占比；中证A500 vs 其余
 *   C. 重叠矩阵（HTML table）：行指数成分里同时属于列指数的比例
 * 数据：data/coverage.json（见 README「数据契约」）
 */
(function () {
  'use strict';

  var NARROW_PX = 700;          // 容器窄于此值时启用 dataZoom 平移，而不是把 100 多格挤成几像素
  var NARROW_CELLS = 40;        // 窄屏默认一屏显示的格数

  var ROWS = [
    { key: '000300', name: '沪深300' },
    { key: '000905', name: '中证500' },
    { key: '000852', name: '中证1000' },
    { key: '932000', name: '中证2000' },
    { key: '000985', name: '中证全指' },
    { key: '000510', name: '中证A500' },
    { key: 'uncovered', name: '未覆盖' }
  ];

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ------------------------------------------------------------------ A. 格子图
  function gridOption(d, ctx, narrow) {
    var palette = ctx.palette;
    var fmt = ctx.fmt;
    var cells = d.cells;
    var cellSize = d.cellSize || 50;
    var byCode = {};
    d.indexes.forEach(function (i) { byCode[i.code] = i; });

    var rowNames = ROWS.map(function (r) { return r.name; });
    var rowStats = ROWS.map(function (r) {
      var src = r.key === 'uncovered' ? d.uncovered : byCode[r.key];
      return src ? fmt.num(src.count) + '只 · ' + src.mvShare.toFixed(1) + '%市值' : '';
    });
    var rowColors = ROWS.map(function (r) { return palette[r.key]; });

    var items = [];
    cells.forEach(function (c) {
      ROWS.forEach(function (r, ri) {
        var n = r.key === 'uncovered' ? c.uncovered : (c.counts[r.key] || 0);
        items.push([c.k, ri, n, c.rankFrom, c.rankTo, c.mvMin, c.mvMax]);
      });
    });

    function renderItem(params, api) {
      var k = api.value(0), r = api.value(1), n = api.value(2);
      var pt = api.coord([k, r]);
      var w = api.size([1, 0])[0];
      var hh = api.size([0, 1])[1];
      return {
        type: 'rect',
        shape: {
          x: pt[0] + w * 0.1,
          y: pt[1] - hh * 0.32,
          width: Math.max(w * 0.8, 0.6),
          height: hh * 0.64,
          r: Math.min(2, w * 0.3)
        },
        style: { fill: rowColors[r], opacity: n > 0 ? Math.max(0.12, n / cellSize) : 0.04 }
      };
    }

    function tooltip(params) {
      var v = params.value;
      var k = v[0];
      var c = cells[k];
      var head = '第 ' + (k + 1) + ' 格 · 市值排名第 ' + c.rankFrom + '–' + c.rankTo + ' 名 · 总市值 ' +
        fmt.num(c.mvMin, 1) + '–' + fmt.num(c.mvMax, 1) + ' 亿';
      var lines = ROWS.filter(function (r) { return r.key !== '000985'; }).map(function (r) {
        var n = r.key === 'uncovered' ? c.uncovered : (c.counts[r.key] || 0);
        return '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + palette[r.key] +
          ';margin-right:6px"></span>' + r.name + ' ' + n + '/' + (c.rankTo - c.rankFrom + 1);
      });
      return head + '<br>' + lines.join('<br>');
    }

    var opt = {
      animation: false,
      tooltip: { trigger: 'item', confine: true, formatter: tooltip, textStyle: { fontSize: 12 } },
      grid: narrow
        ? { left: 72, right: 12, top: 26, bottom: 64 }
        : { left: 96, right: 160, top: 30, bottom: 44 },
      xAxis: {
        type: 'value',
        min: 0,
        max: cells.length,
        splitLine: { show: false },
        axisLine: { lineStyle: { color: '#D1D5DB' } },
        axisTick: { show: false },
        axisLabel: { color: palette.muted, fontSize: 11, showMaxLabel: false, formatter: function (v) { return v * cellSize; } },
        name: narrow ? '' : '按总市值排名（名）',
        nameLocation: 'middle',
        nameGap: 26,
        nameTextStyle: { color: palette.muted, fontSize: 11 }
      },
      yAxis: [
        {
          type: 'category', data: rowNames, inverse: true,
          axisTick: { show: false }, axisLine: { show: false },
          axisLabel: { color: palette.fg, fontSize: narrow ? 11 : 12, fontWeight: 500 }
        },
        {
          type: 'category', data: rowStats, inverse: true, position: 'right',
          show: !narrow,
          axisTick: { show: false }, axisLine: { show: false },
          axisLabel: { color: palette.muted, fontSize: 11 }
        }
      ],
      series: [{
        type: 'custom',
        name: '覆盖格',
        renderItem: renderItem,
        encode: { x: 0, y: 1 },
        data: items,
        yAxisIndex: 0,
        markLine: {
          silent: true,
          symbol: 'none',
          animation: false,
          lineStyle: { type: 'dashed', color: '#9CA3AF', width: 1 },
          label: { position: 'start', color: palette.muted, fontSize: 10, formatter: function (p) { return p.name; } },
          data: (d.boundaries || []).map(function (b) { return { xAxis: b / cellSize, name: b + '只' }; })
        }
      }]
    };
    if (narrow) {
      opt.dataZoom = [
        { type: 'inside', xAxisIndex: 0, startValue: 0, endValue: Math.min(NARROW_CELLS, cells.length), filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, startValue: 0, endValue: Math.min(NARROW_CELLS, cells.length), filterMode: 'none',
          height: 18, bottom: 8, brushSelect: false, labelFormatter: function (v) { return Math.round(v) * cellSize; } }
      ];
    }
    return opt;
  }

  // ------------------------------------------------------------------ B. 市值梯子
  function ladderOption(d, ctx) {
    var palette = ctx.palette;
    var a500 = null;
    d.indexes.forEach(function (i) { if (i.code === '000510') a500 = i.mvShare; });
    var rest = a500 === null ? null : Math.round((100 - a500) * 100) / 100;

    function labelOpt(dark) {
      return {
        show: true, position: 'inside', fontSize: 11, color: dark ? palette.fg : '#fff',
        formatter: function (p) { return (p.value !== null && p.value >= 3) ? p.value.toFixed(1) + '%' : ''; }
      };
    }
    var series = d.mvLadder.map(function (seg) {
      return {
        name: seg.name, type: 'bar', stack: 'ladder', barWidth: 26,
        data: [seg.mvShare, null],
        itemStyle: { color: palette[seg.code] || palette.uncovered },
        label: labelOpt(seg.code === 'uncovered')
      };
    });
    series.push({
      name: '中证A500', type: 'bar', stack: 'ladder', barWidth: 26, data: [null, a500],
      itemStyle: { color: palette['000510'] }, label: labelOpt(false)
    });
    series.push({
      name: '其余', type: 'bar', stack: 'ladder', barWidth: 26, data: [null, rest],
      itemStyle: { color: palette.uncovered }, label: labelOpt(true)
    });

    return {
      animation: false,
      tooltip: {
        trigger: 'item', confine: true,
        formatter: function (p) { return p.seriesName + '：' + ctx.fmt.pct(p.value, 2) + ' 总市值'; }
      },
      grid: { left: 150, right: 20, top: 8, bottom: 28, containLabel: false },
      xAxis: {
        type: 'value', min: 0, max: 100,
        axisLabel: { formatter: '{value}%', color: palette.muted, fontSize: 11 },
        splitLine: { lineStyle: { color: '#EEF0F3' } }
      },
      yAxis: {
        type: 'category', inverse: true,
        data: ['四级规模宽基 + 未覆盖', '中证A500 vs 其余'],
        axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { color: palette.fg, fontSize: 12 }
      },
      series: series
    };
  }

  // ------------------------------------------------------------------ C. 重叠矩阵
  function overlapTable(d, ctx) {
    var fmt = ctx.fmt;
    var ov = d.overlap;
    var names = ov.names || ov.codes;
    var table = h('table', 'data-table overlap-table');
    table.appendChild(h('caption', null, '行指数成分中同时属于列指数的比例（行 → 列）'));
    var thead = h('thead');
    var hr = h('tr');
    hr.appendChild(h('th', null, '行 ＼ 列'));
    names.forEach(function (n) {
      var th = h('th', null, n);
      th.setAttribute('scope', 'col');
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = h('tbody');
    ov.matrix.forEach(function (row, i) {
      var tr = h('tr');
      var th = h('th', null, names[i]);
      th.setAttribute('scope', 'row');
      tr.appendChild(th);
      row.forEach(function (v) {
        var td = h('td', null, fmt.pct(v, 1));
        td.style.backgroundColor = 'rgba(31,95,191,' + (v / 100 * 0.75).toFixed(3) + ')';
        if (v >= 60) td.className = 'strong';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  // ------------------------------------------------------------------ render
  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    var palette = ctx.palette;
    var fmt = ctx.fmt;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');

    var byCode = {};
    d.indexes.forEach(function (i) { byCode[i.code] = i; });

    // 图例（同时承载各行统计，窄屏隐藏右侧轴标签时信息不丢）
    var legend = h('div', 'legend');
    ROWS.forEach(function (r) {
      var src = r.key === 'uncovered' ? d.uncovered : byCode[r.key];
      var item = h('span', 'legend-item');
      var sw = h('span', 'swatch');
      sw.style.background = palette[r.key];
      item.appendChild(sw);
      item.appendChild(document.createTextNode(
        r.name + (src ? '（' + fmt.num(src.count) + '只 · ' + src.mvShare.toFixed(1) + '%市值）' : '')));
      legend.appendChild(item);
    });
    body.appendChild(legend);

    // A
    var gridEl = h('div', 'chart cov-grid');
    gridEl.style.height = '420px';
    body.appendChild(gridEl);
    var unc = d.uncovered;
    body.appendChild(h('p', 'chart-note',
      '未被规模宽基覆盖：' + fmt.num(unc.count) + ' 只（占全市场 ' + unc.mvShare.toFixed(2) + '% 市值），不在沪深300 / 中证500 / 中证1000 / 中证2000 任何一个里；' +
      '全市场 = 中证全指 ' + fmt.num(d.universe.count) + ' 只，合计总市值 ' + fmt.num(d.universe.mvTotal / 10000, 1) + ' 万亿。'));

    // B
    body.appendChild(h('h3', null, '市值梯子'));
    var ladderEl = h('div', 'chart cov-ladder');
    ladderEl.style.height = '170px';
    body.appendChild(ladderEl);

    // C
    body.appendChild(h('h3', null, '重叠矩阵'));
    var wrap = h('div', 'scroll-x');
    wrap.appendChild(overlapTable(d, ctx));
    body.appendChild(wrap);

    var narrow = body.clientWidth < NARROW_PX;
    var gridChart = echarts.init(gridEl, null, { renderer: 'canvas' });
    gridChart.setOption(gridOption(d, ctx, narrow));
    var ladderChart = echarts.init(ladderEl, null, { renderer: 'canvas' });
    ladderChart.setOption(ladderOption(d, ctx));

    // 跨过窄/宽阈值时重建格子图配置（app.js 已负责 resize）
    var timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var now = body.clientWidth < NARROW_PX;
        if (now !== narrow) {
          narrow = now;
          gridChart.setOption(gridOption(d, ctx, narrow), { notMerge: true });
        }
        gridChart.resize();
      }, 220);
    });

    return { charts: [gridChart, ladderChart] };
  }

  window.AshareBoard.registerPanel({
    id: 'coverage',
    title: '宽基指数覆盖范围',
    subtitle: '1格 = 50只 · 按总市值排名连续排列 · 格子深浅 = 这50只里属于该指数的比例',
    dataFile: 'coverage.json',
    bodyMinHeight: 820,
    notes: [
      '横轴是中证全指全部成分按「总市值」从大到小的排名，每 50 只切一格；格子越深，表示这 50 只里属于该行指数的越多。虚线是 300 / 800 / 1800 / 3800 只的名义分界。',
      '中证规模指数选样用的是自由流通市值并带缓冲区，这里按总市值排名，所以边界处会有一些「越界」的格子，这是口径差异而不是数据错误。',
      '「中证全指」在这里当作全市场：剔除了 ST 和上市不久的新股，因此只数会略少于交易所上市公司总数。',
      '中证A500 不是规模层：它在各行业里挑龙头，所以既有大盘股也会覆盖到部分中小盘，和沪深300 的重叠度看矩阵。',
      '「未覆盖」= 全指成分里不属于上述四个规模指数中任何一个的股票，数量不少但市值占比很小。',
      '成分名单来自中证指数官网，总市值来自腾讯行情；数据需要手工运行 make update 刷新，页面顶部会提示距今几个交易日。'
    ],
    render: render
  });
})();
