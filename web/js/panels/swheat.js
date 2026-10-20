/*
 * 面板 swheat —— 申万一级行业年度涨跌幅。
 *   一张热力图：行 = 31 个申万一级行业，列 = 自然年 2000…上一年 ＋ 今年以来 ＋ 四个年化列（全程 / 近10年 / 近5年 / 近3年）。
 *   年度与今年以来用同一条 ±60% 的红绿色阶（只截断颜色，不截断数字）；年化列用自己的对称色阶；
 *   工具条下拉按任一年化列、最近完整年或今年以来重排行序。窄屏隐藏格内数字并给横轴加 dataZoom。
 * 数据：data/swheat.json → data.years[], ytdYear, ytdAsOf, nByYear[], nYtd,
 *   industries[]{code,name,firstYear,gapYears[],rets[],ytd,ytdRank,cagr{all,y10,y5,y3},cagrYears{all,from},ranks[]}
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var NARROW_PX = 700;
  var CLAMP = 60;                       // 年度色阶截断（%）
  var CAGR_COLS = [
    { key: 'all', label: '全程年化', axisLabel: '全程\n年化' },
    { key: 'y10', label: '近10年年化', axisLabel: '近10年\n年化', n: 10 },
    { key: 'y5', label: '近5年年化', axisLabel: '近5年\n年化', n: 5 },
    { key: 'y3', label: '近3年年化', axisLabel: '近3年\n年化', n: 3 }
  ];

  function isNum(v) { return v !== null && v !== undefined && !isNaN(v); }
  function clamp(v) { return Math.max(-CLAMP, Math.min(CLAMP, v)); }

  // 列布局：年份 | 空列 | 今年以来 | 空列 | 四个年化列
  function layoutCols(d) {
    var nY = d.years.length;
    return { nY: nY, ytd: nY + 1, cagr0: nY + 3, total: nY + 7 };
  }

  function xCategories(d) {
    var cats = d.years.map(String);
    cats.push('');
    cats.push('YTD ' + d.ytdYear);
    cats.push('');
    CAGR_COLS.forEach(function (c) { cats.push(c.label); });
    return cats;
  }

  // 排序键 → 取值函数
  function sortValue(key, d) {
    var lastIdx = d.years.length - 1;
    if (key === 'ytd') return function (ind) { return ind.ytd; };
    if (key === 'last') return function (ind) { return ind.rets[lastIdx]; };
    return function (ind) { return ind.cagr[key]; };
  }

  function sortRows(d, key) {
    var val = sortValue(key, d);
    return d.industries.slice().sort(function (a, b) {
      var va = val(a), vb = val(b);
      var na = !isNum(va), nb = !isNum(vb);
      if (na && nb) return a.name.localeCompare(b.name, 'zh');
      if (na) return 1;
      if (nb) return -1;
      if (vb !== va) return vb - va;
      return a.name.localeCompare(b.name, 'zh');
    });
  }

  // 两个 series 的数据：[xi, yi, 着色值, 真实值, 排名, 参与排名/年数]
  function buildData(d, rows) {
    var L = layoutCols(d);
    var s0 = [], s1 = [];
    rows.forEach(function (ind, yi) {
      ind.rets.forEach(function (v, xi) {
        if (isNum(v)) s0.push([xi, yi, clamp(v), v, ind.ranks[xi], d.nByYear[xi]]);
      });
      if (isNum(ind.ytd)) s0.push([L.ytd, yi, clamp(ind.ytd), ind.ytd, ind.ytdRank, d.nYtd]);
      CAGR_COLS.forEach(function (c, k) {
        var v = ind.cagr[c.key];
        if (isNum(v)) s1.push([L.cagr0 + k, yi, v, v, null, c.key === 'all' ? ind.cagrYears.all : c.n]);
      });
    });
    return { s0: s0, s1: s1 };
  }

  function cagrScaleMax(d) {
    var m = 0;
    d.industries.forEach(function (ind) {
      CAGR_COLS.forEach(function (c) { var v = ind.cagr[c.key]; if (isNum(v)) m = Math.max(m, Math.abs(v)); });
    });
    return Math.max(5, Math.ceil(m / 5) * 5);
  }

  function chartOption(d, state, ctx, narrow) {
    var palette = ctx.palette;
    var L = layoutCols(d);
    var lastYear = d.years[L.nY - 1];
    var rows = state.rows;
    var data = buildData(d, rows);
    var M = cagrScaleMax(d);
    var opt = C.heatmapBase({ xCats: xCategories(d), yCats: rows.map(function (r) { return r.name; }), narrow: narrow });
    // 底部要放两条色阶（窄屏上下叠放，再加一条 dataZoom 滑条），比骨架默认值多留一些
    opt.grid.bottom = narrow ? 132 : 64;
    opt.xAxis.axisLabel.rotate = narrow ? 90 : 0;
    // 按类目文字而不是下标判断：dataZoom 时 formatter 收到的下标是可见区间内的相对位置
    var ytdCat = 'YTD ' + d.ytdYear, cagrByLabel = {};
    CAGR_COLS.forEach(function (c) { cagrByLabel[c.label] = c; });
    opt.xAxis.axisLabel.formatter = function (value) {
      if (value === ytdCat) return narrow ? d.ytdYear + ' 至今' : d.ytdYear + '\n至今';
      var c = cagrByLabel[value];
      if (c) return narrow ? c.label : c.axisLabel;
      return value;
    };

    function colLabel(xi) {
      if (xi < L.nY) return String(d.years[xi]);
      if (xi === L.ytd) return d.ytdYear + ' 今年以来（至 ' + d.ytdAsOf + '）';
      return CAGR_COLS[xi - L.cagr0].label;
    }

    opt.tooltip.formatter = function (p) {
      var v = p.value, ind = rows[v[1]], xi = v[0];
      var head = '<b>' + ind.name + '</b> · ' + colLabel(xi) + '<br>';
      if (p.seriesIndex === 0) {
        var rankTxt = isNum(v[4]) ? '　' + (xi === L.ytd ? '排名' : '当年排名') + ' ' + v[4] + '/' + v[5] : '';
        return head + '涨跌幅 ' + C.fmtSignedPct(v[3], 1) + rankTxt;
      }
      var c = CAGR_COLS[xi - L.cagr0];
      var from = c.key === 'all' ? ind.cagrYears.from : lastYear - c.n + 1;
      return head + '年化 ' + C.fmtSignedPct(v[3], 1) + '（' + from + '–' + lastYear + '，' + v[5] + ' 年）';
    };

    var cellStyle = { borderColor: palette.neutral, borderWidth: 1 };
    var emphasis = { itemStyle: { borderColor: palette.fg, borderWidth: 1.5 } };
    opt.series = [
      {
        name: '年度', type: 'heatmap', data: data.s0, itemStyle: cellStyle, emphasis: emphasis,
        label: C.heatCellLabel(!narrow, function (p) { return C.fmtPp(p.value[3], 0); })
      },
      {
        name: '年化', type: 'heatmap', data: data.s1, itemStyle: cellStyle, emphasis: emphasis,
        label: C.heatCellLabel(!narrow, function (p) { return C.fmtPp(p.value[3], 1); })
      }
    ];
    var scale = [palette.down, palette.neutral, palette.up];
    var vmText = { color: palette.muted, fontSize: 11 };
    opt.visualMap = [
      // 宽屏：两条色阶并排（年度居中、年化靠右）；窄屏：上下叠放在左侧
      { type: 'continuous', seriesIndex: 0, dimension: 2, min: -CLAMP, max: CLAMP, calculable: false,
        inRange: { color: scale }, orient: 'horizontal', left: narrow ? 4 : 'center', bottom: 0,
        text: ['年度 +' + CLAMP + '%', '\u2212' + CLAMP + '%'], textStyle: vmText, itemWidth: 10, itemHeight: narrow ? 110 : 140 },
      { type: 'continuous', seriesIndex: 1, dimension: 2, min: -M, max: M, calculable: false,
        inRange: { color: scale }, orient: 'horizontal', left: narrow ? 4 : undefined, right: narrow ? undefined : 0, bottom: narrow ? 28 : 0,
        text: ['年化 +' + M + '%', '\u2212' + M + '%'], textStyle: vmText, itemWidth: 10, itemHeight: narrow ? 110 : 120 }
    ];
    if (narrow) {
      var startIdx = Math.max(0, L.nY - 10), endIdx = L.total - 1;
      opt.dataZoom = [
        { type: 'inside', xAxisIndex: 0, startValue: startIdx, endValue: endIdx, filterMode: 'none' },
        { type: 'slider', xAxisIndex: 0, startValue: startIdx, endValue: endIdx, filterMode: 'none',
          height: 16, bottom: 60, brushSelect: false, showDetail: false }
      ];
    }
    return opt;
  }

  // 起点说明：哪些行业的指数从 2015 / 2022 才开始，哪些中间有缺段
  function startNote(d) {
    var byYear = {};
    d.industries.forEach(function (ind) {
      if (ind.firstYear === d.years[0]) return;
      (byYear[ind.firstYear] = byYear[ind.firstYear] || []).push(ind.name);
    });
    var parts = Object.keys(byYear).sort().map(function (y) { return y + ' 年起：' + byYear[y].join('、'); });
    var gaps = d.industries.filter(function (ind) { return ind.gapYears && ind.gapYears.length; }).map(function (ind) {
      var g = ind.gapYears;
      return ind.name + ' ' + g[0] + '–' + g[g.length - 1] + ' 年无数据（年化从 ' + ind.cagrYears.from + ' 年起算）';
    });
    return '空格 = 该行业指数当年尚无数据。' + parts.join('；') + (gaps.length ? '；' + gaps.join('；') : '') + '。';
  }

  function render(body, json, ctx) {
    var d = json.data;
    var echarts = ctx.echarts;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var state = { sortKey: 'all', rows: sortRows(d, 'all') };
    var lastYear = d.years[d.years.length - 1];

    var tools = C.el('div', 'panel-tools');
    C.selectControl(tools, {
      id: 'swheat-sort', name: 'swheat-sort', label: '排序', value: 'all',
      options: [
        { value: 'all', text: '全程年化' }, { value: 'y10', text: '近10年年化' }, { value: 'y5', text: '近5年年化' },
        { value: 'y3', text: '近3年年化' }, { value: 'last', text: '最近完整年（' + lastYear + '）' }, { value: 'ytd', text: '今年以来' }
      ],
      onChange: function (v) {
        state.sortKey = v;
        state.rows = sortRows(d, v);
        var data = buildData(d, state.rows);
        chart.setOption({ yAxis: { data: state.rows.map(function (r) { return r.name; }) }, series: [{ data: data.s0 }, { data: data.s1 }] });
      }
    });
    body.appendChild(tools);
    body.appendChild(C.el('p', 'heat-legend',
      '年度与今年以来：红涨绿跌，着色在 ±' + CLAMP + '% 截断（数字不截断）；年化列单独着色；空格 = 该行业指数当年尚无数据。'));

    var chartEl = C.el('div', 'chart chart-heat');
    body.appendChild(chartEl);
    var narrow = body.clientWidth < NARROW_PX;
    var chart = echarts.init(chartEl, null, { renderer: 'canvas' });
    chart.setOption(chartOption(d, state, ctx, narrow));

    var timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var now = body.clientWidth < NARROW_PX;
        if (now !== narrow) {
          narrow = now;
          chart.setOption(chartOption(d, state, ctx, narrow), { notMerge: true });
        }
      }, 220);
    });

    body.appendChild(C.el('p', 'chart-note', startNote(d)));
    return { charts: [chart] };
  }

  window.AshareBoard.registerPanel({
    id: 'swheat',
    title: '申万一级行业年度涨跌幅',
    subtitle: '31 个申万一级行业 · 自然年涨跌幅 · 今年以来 · 完整年度年化',
    dataFile: 'swheat.json',
    bodyMinHeight: 900,
    notes: [
      '横着看一行是一个行业的历年表现，竖着看一列是同一年各行业的分化；年化列把起点不同的行业放在一起，只能各自对照自己的历史。',
      '用「排序」下拉按年化、最近完整年或今年以来重排行序；鼠标停在格子上可看当年在参与排名的行业里的名次。'
    ],
    render: render
  });
})();
