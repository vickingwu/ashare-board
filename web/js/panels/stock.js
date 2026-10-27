/*
 * 面板 stock —— 个股走势图（任意 A 股 / ETF / 指数）。
 *   输入名称或代码（联想来自 data/symbols.json），浏览器直接向腾讯行情取后复权 / 前复权日线（js/lib/kline.js），
 *   用 js/lib/bullbear.js 算：① 对数价格 + 区间年化 + ZigZag 牛熊阶段底色（图）② 距前高回撤 + 最大回撤（图）
 *   ③ 牛熊阶段表 ④ 多周期涨跌幅卡片 ⑤ 分年涨跌幅。时间窗 / 复权 / 阈值在浏览器里现算，不再请求。
 *   地址栏状态 #panel-stock?s=sh600519&r=10y&th=0.2&adj=hfq；页面加载时不自动取数（深链接除外）。
 * 数据：data/symbols.json → data.rows[] = [code, name, kind(stock|index|etf), market(sh|sz|bj), out(退市日|null)]
 */
(function () {
  'use strict';

  var C = window.AshareCharts;
  var K = window.AshareKline;
  var BB = window.AshareBullBear;
  var NARROW_PX = 520;
  var RANGES = [{ label: '全部', key: 'all' }, { label: '20年', key: '20y' }, { label: '10年', key: '10y' }, { label: '5年', key: '5y' }, { label: '3年', key: '3y' }, { label: '1年', key: '1y' }];
  var ADJS = [{ label: '后复权', key: 'hfq' }, { label: '前复权', key: 'qfq' }];
  var THRESHOLDS = ['0.2', '0.3', '0.4'];
  var ADJ_LABEL = { hfq: '后复权', qfq: '前复权', none: '不复权' };
  var KIND_ORDER = { stock: 0, index: 1, etf: 2 };
  var MAX_SUGGEST = 12;
  var DAY = BB.DAY;

  function normName(s) { return BB.cleanName(s).toLowerCase(); }
  function fmtDur(days) {
    if (days < 60) return Math.round(days) + ' 天';
    if (days < 730) return Math.round(days / 30.4375) + ' 个月';
    return (days / 365.25).toFixed(1) + ' 年';
  }
  function fmtTime(q) {   // 20261027134058 → 10-27 13:40
    var m = /^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})/.exec(q || '');
    return m ? m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] : '';
  }
  function setActive(group, idx) {
    Array.prototype.forEach.call(group.querySelectorAll('.range-btn'), function (b, i) {
      b.classList.toggle('active', i === idx);
      b.setAttribute('aria-pressed', i === idx ? 'true' : 'false');
    });
  }
  function tagOf(it) {
    if (it.out) return '已退市 ' + it.out.slice(0, 4);
    if (it.kind === 'index') return '指数';
    if (it.kind === 'etf') return 'ETF';
    return it.market === 'sh' ? '沪A' : it.market === 'bj' ? '北A' : '深A';
  }

  // ---------------------------------------------------------------- 联想
  function buildIndex(rows) {
    var list = rows.map(function (r) {
      var name = r[1] ? BB.cleanName(r[1]) : '';
      return { code: String(r[0]), name: name, kind: r[2] || 'stock', market: r[3], out: r[4] || null, nn: name.toLowerCase() };
    });
    var byKey = {};
    list.forEach(function (it) { byKey[it.market + it.code] = it; });
    return { list: list, byKey: byKey };
  }

  // 得分：代码 精确 100 / 前缀 80 / 子串 30；名称 精确 100 / 前缀 85 / 子串 60；已退市 −50；同分 股票 → 指数 → ETF，再按代码
  function search(index, q) {
    q = normName(q);
    var pre = /^(sh|sz|bj)(\d{6})$/.exec(q);
    if (pre) q = pre[2];
    if (!q) return [];
    var isNum = /^\d+$/.test(q);
    var res = [];
    index.list.forEach(function (it) {
      var sc = -1;
      if (isNum) {
        if (it.code === q) sc = 100; else if (it.code.indexOf(q) === 0) sc = 80; else if (it.code.indexOf(q) >= 0) sc = 30;
      } else if (it.nn) {
        if (it.nn === q) sc = 100; else if (it.nn.indexOf(q) === 0) sc = 85; else if (it.nn.indexOf(q) >= 0) sc = 60;
      }
      if (sc < 0) return;
      if (it.out) sc -= 50;
      res.push({ it: it, sc: sc });
    });
    res.sort(function (a, b) {
      return b.sc - a.sc || (KIND_ORDER[a.it.kind] - KIND_ORDER[b.it.kind]) || a.it.code.localeCompare(b.it.code);
    });
    return res.slice(0, MAX_SUGGEST).map(function (r) { return r.it; });
  }

  function toSym(it) { return { symbol: it.market + it.code, code: it.code, market: it.market, name: it.name || null, kind: it.kind, out: it.out }; }

  // 输入框文字 → 标的：带市场前缀 → 查表或原样；6 位数字 → 表里同代码（股票优先）→ 否则按首位推断市场；其它 → 联想第一项
  function resolveInput(index, value) {
    var text = String(value || '').trim();
    var sym = K.inferSymbol(text);
    if (sym) {
      if (/^(sh|sz|bj)/i.test(text)) {
        var hit = index.byKey[sym.symbol];
        return hit ? toSym(hit) : Object.assign({ name: null, kind: null, out: null }, sym);
      }
      var exact = index.list.filter(function (it) { return it.code === sym.code; });
      exact.sort(function (a, b) { return (a.out ? 1 : 0) - (b.out ? 1 : 0) || KIND_ORDER[a.kind] - KIND_ORDER[b.kind]; });
      return exact.length ? toSym(exact[0]) : Object.assign({ name: null, kind: null, out: null }, sym);
    }
    var list = search(index, text);
    return list.length ? toSym(list[0]) : null;
  }

  // ---------------------------------------------------------------- 图表配置
  function gridOf(narrow) { return { left: narrow ? 52 : 64, right: narrow ? 12 : 24, top: 36, bottom: 28 }; }

  function priceOption(A, S, ctx, layout, state) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var n = A.n, t = A.t, c = A.c;
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < n; i++) { if (c[i] < lo) lo = c[i]; if (c[i] > hi) hi = c[i]; }
    var data = new Array(n);
    for (var j = 0; j < n; j++) data[j] = [t[j] * DAY, c[j]];
    var windowDays = Math.max(1, A.days);
    var areas = A.phases.map(function (p) {
      var color = p.bull ? palette.up : palette.down;
      var wide = p.days >= windowDays * 0.04;
      return [{
        xAxis: t[p.s] * DAY, name: C.fmtSignedPct(p.ret * 100, 1) + '\n' + fmtDur(p.days),
        itemStyle: { color: C.withAlpha(color, 0.10) },
        label: { show: wide, position: 'insideTop', fontSize: narrow ? 9 : 10, color: color, lineHeight: 13 }
      }, { xAxis: t[p.e] * DAY }];
    });
    var bulls = A.phases.filter(function (p) { return p.bull; }).length;
    var head = '区间年化 ' + (isNaN(A.cagr) ? '–' : C.fmtSignedPct(A.cagr * 100, 1)) + ' · 累计 ' + C.fmtMult(A.nav[n - 1], 2) +
      ' · ' + A.phases.length + ' 个阶段（牛 ' + bulls + ' / 熊 ' + (A.phases.length - bulls) + '）';
    function tooltip(params) {
      if (!params || !params.length) return '';
      var ms = params[0].data ? params[0].data[0] : params[0].axisValue;
      var k = BB.idxOnOrBefore(A.t.map(function (x) { return { t: x }; }), Math.round(ms / DAY));
      if (k < 0) return '';
      return '<b>' + BB.fmtDate(t[k]) + '</b><br>收盘 ' + fmt.num(c[k], 2) + '<br>距前高 ' + fmt.pct(A.dd[k] * 100, 1) + '<br>净值 ' + C.fmtMult(A.nav[k], 2);
    }
    var grid = gridOf(narrow);
    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' }, formatter: tooltip },
      grid: grid,
      // 读数放在绘图区上方靠右，不与左上角的纵轴名称（后复权（对数））重叠
      graphic: [{ type: 'text', right: grid.right + 4, top: 8, silent: true, style: { text: head, fill: palette.fg, fontSize: narrow ? 11 : 12, textAlign: 'right' } }],
      xAxis: C.timeAxis({ min: t[0] * DAY, max: t[n - 1] * DAY }),
      yAxis: Object.assign(C.logAxis({ name: narrow ? '' : ADJ_LABEL[state.adj] + '（对数）' }), { min: lo / 1.08, max: hi * 1.08 }),
      series: [{
        id: 'price', name: '收盘', type: 'line', data: data, showSymbol: false, sampling: 'lttb',
        lineStyle: { width: 1.4, color: palette['000300'] }, itemStyle: { color: palette['000300'] },
        markArea: { silent: true, data: areas }
      }]
    };
  }

  function drawdownOption(A, S, ctx, layout) {
    var palette = ctx.palette, fmt = ctx.fmt;
    var narrow = layout.narrow;
    var n = A.n, t = A.t;
    var data = new Array(n);
    for (var j = 0; j < n; j++) data[j] = [t[j] * DAY, A.dd[j] * 100];
    var series = {
      id: 'dd', name: '距前高', type: 'line', data: data, showSymbol: false, sampling: 'lttb',
      lineStyle: { width: 1, color: palette.down }, itemStyle: { color: palette.down }, areaStyle: { color: palette.down, opacity: 0.25 }
    };
    var md = A.maxDD;
    if (md) {
      var endI = md.end === null ? n - 1 : md.end;
      series.markArea = { silent: true, data: [[{ xAxis: t[md.peak] * DAY, itemStyle: { color: C.withAlpha(palette.down, 0.18) } }, { xAxis: t[endI] * DAY }]] };
      var rightHalf = (t[md.trough] - t[0]) > (t[n - 1] - t[0]) / 2;
      series.markPoint = {
        silent: true, symbol: 'circle', symbolSize: 8, itemStyle: { color: palette.down },
        label: { show: true, position: rightHalf ? 'left' : 'right', fontSize: 11, color: palette.fg, formatter: '最大回撤 ' + fmt.pct(md.depth * 100, 1) },
        data: [{ coord: [t[md.trough] * DAY, md.depth * 100] }]
      };
    }
    function tooltip(params) {
      if (!params || !params.length) return '';
      var ms = params[0].data ? params[0].data[0] : params[0].axisValue;
      var k = BB.idxOnOrBefore(A.t.map(function (x) { return { t: x }; }), Math.round(ms / DAY));
      if (k < 0) return '';
      return '<b>' + BB.fmtDate(t[k]) + '</b><br>距前高 ' + fmt.pct(A.dd[k] * 100, 1);
    }
    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' }, formatter: tooltip },
      grid: gridOf(narrow),
      xAxis: C.timeAxis({ min: t[0] * DAY, max: t[n - 1] * DAY }),
      yAxis: {
        type: 'value', max: 0, name: narrow ? '' : '距前高 %', nameTextStyle: { color: palette.muted, fontSize: 11, align: 'left' },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: palette.muted, fontSize: 11, formatter: function (v) { return v + '%'; } },
        splitLine: { lineStyle: { color: '#E5E7EB' } }
      },
      series: [series]
    };
  }

  // ---------------------------------------------------------------- 面板
  function render(body, json, ctx) {
    var echarts = ctx.echarts, fmt = ctx.fmt;
    if (!echarts) throw new Error('ECharts 未加载（vendor/echarts.min.js）');
    var index = buildIndex((json.data && json.data.rows) || []);
    var state = { sym: null, name: null, kind: null, adjRequested: 'hfq', adj: null, range: 'all', th: 0.2, data: null, A: null, seq: 0 };

    // ---- 工具条 1：搜索框 + 联想 + 查询
    var tools1 = C.el('div', 'panel-tools');
    var searchBox = C.el('span', 'tools-group stock-search');
    searchBox.setAttribute('role', 'group');
    searchBox.setAttribute('aria-label', '证券查询');
    var input = document.createElement('input');
    input.type = 'search';
    input.className = 'stock-input';
    input.placeholder = '名称 / 6 位代码 / ETF 代码';
    input.setAttribute('aria-label', '证券名称或代码');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', 'stock-suggest');
    input.setAttribute('aria-autocomplete', 'list');
    var suggest = C.el('ul', 'stock-suggest');
    suggest.id = 'stock-suggest';
    suggest.setAttribute('role', 'listbox');
    suggest.hidden = true;
    var goBtn = C.el('button', 'range-btn stock-go', '查询');
    goBtn.type = 'button';
    searchBox.appendChild(input);
    searchBox.appendChild(suggest);
    tools1.appendChild(searchBox);
    tools1.appendChild(goBtn);
    body.appendChild(tools1);

    // ---- 工具条 2：时间窗 / 复权 / 阈值
    var tools2 = C.el('div', 'panel-tools');
    body.appendChild(tools2);
    var rangeGroup = C.rangeButtons(tools2, RANGES.map(function (r) { return r.label; }), 0, function (i) { state.range = RANGES[i].key; applyView(); }, { label: '时间窗' });
    var adjGroup = C.rangeButtons(tools2, ADJS.map(function (a) { return a.label; }), 0, function (i) {
      state.adjRequested = ADJS[i].key;
      if (state.sym) query(state.sym);
    }, { label: '复权' });
    var noAdj = C.el('span', 'tools-label stock-noadj', '指数无复权');
    noAdj.hidden = true;
    tools2.appendChild(noAdj);
    var thSelect = C.selectControl(tools2, {
      label: 'ZigZag 阈值', name: 'stock-th', value: '0.2',
      options: THRESHOLDS.map(function (v) { return { value: v, text: Math.round(+v * 100) + '%' }; }),
      onChange: function (v) { state.th = +v; applyView(); }
    });

    // ---- 状态行、读数、图、表、卡片
    var status = C.el('p', 'stock-status', '输入名称或代码后点查询；也可直接粘贴 #panel-stock?s=sh600519 这样的链接');
    status.setAttribute('role', 'status');
    body.appendChild(status);
    var head = C.el('div', 'stock-head readout');
    body.appendChild(head);
    var priceEl = C.el('div', 'chart chart-stock-price');
    body.appendChild(priceEl);
    var ddEl = C.el('div', 'chart chart-stock-dd');
    body.appendChild(ddEl);
    var ddNote = C.el('p', 'chart-note stock-dd-note');
    body.appendChild(ddNote);
    body.appendChild(C.el('h3', null, '牛熊阶段'));
    var phasesHost = C.el('div', 'stock-phases');
    body.appendChild(phasesHost);
    body.appendChild(C.el('h3', null, '多周期涨跌幅'));
    var cards = C.el('div', 'cards stock-cards');
    body.appendChild(cards);
    body.appendChild(C.el('h3', null, '分年涨跌幅'));
    var chips = C.el('div', 'year-chips');
    body.appendChild(chips);
    var priceChart = echarts.init(priceEl, null, { renderer: 'canvas' });
    var ddChart = echarts.init(ddEl, null, { renderer: 'canvas' });
    echarts.connect([priceChart, ddChart]);

    function layout() { return { narrow: body.clientWidth < NARROW_PX }; }
    function setStatus(text, isError, title) {
      status.textContent = text;
      status.classList.toggle('is-error', !!isError);
      if (title) status.title = title; else status.removeAttribute('title');
    }
    function clearOutput() {
      priceChart.clear();
      ddChart.clear();
      head.innerHTML = '';
      ddNote.textContent = '';
      phasesHost.innerHTML = '';
      cards.innerHTML = '';
      chips.innerHTML = '';
    }

    // ---- 联想下拉
    var items = [], active = -1, hideTimer = null;
    function closeSuggest() {
      suggest.hidden = true;
      suggest.innerHTML = '';
      items = [];
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }
    function paintActive() {
      Array.prototype.forEach.call(suggest.children, function (li, i) {
        li.classList.toggle('active', i === active);
        li.setAttribute('aria-selected', i === active ? 'true' : 'false');
      });
      if (active >= 0) input.setAttribute('aria-activedescendant', 'stock-opt-' + active); else input.removeAttribute('aria-activedescendant');
    }
    function openSuggest(list) {
      items = list;
      suggest.innerHTML = '';
      if (!list.length) { closeSuggest(); return; }
      list.forEach(function (it, i) {
        var li = C.el('li');
        li.id = 'stock-opt-' + i;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        li.appendChild(C.el('span', 'code', it.code));
        li.appendChild(C.el('span', 'name', it.name || '（无简称）'));
        li.appendChild(C.el('span', 'tag', tagOf(it)));
        li.addEventListener('mousedown', function (ev) { ev.preventDefault(); pick(it); });
        suggest.appendChild(li);
      });
      suggest.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
      paintActive();
    }
    function pick(it) {
      input.value = (it.name || it.code) + ' ' + it.code;
      closeSuggest();
      query(toSym(it));
    }
    function submit() {
      var sym = resolveInput(index, input.value);
      closeSuggest();
      if (!sym) { setStatus('没有匹配的证券：请输入名称、6 位代码或 sh/sz 前缀 + 代码', true); return; }
      query(sym);
    }
    var debouncedSearch = (function () {
      var timer = null;
      return function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          var q = input.value.trim();
          // 刚选过一项时输入框是「名称 代码」，不再弹联想
          if (!q || /\s\d{6}$/.test(q)) { closeSuggest(); return; }
          openSuggest(search(index, q));
        }, 120);
      };
    })();
    input.addEventListener('input', debouncedSearch);
    input.addEventListener('keydown', function (ev) {
      if (ev.isComposing) return;
      if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        if (suggest.hidden) { openSuggest(search(index, input.value.trim())); }
        if (items.length) { active = Math.min(active + 1, items.length - 1); paintActive(); }
      } else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (items.length) { active = Math.max(active - 1, -1); paintActive(); }
      } else if (ev.key === 'Enter') {
        ev.preventDefault();
        if (!suggest.hidden && active >= 0 && items[active]) pick(items[active]); else submit();
      } else if (ev.key === 'Escape') {
        closeSuggest();
      }
    });
    input.addEventListener('blur', function () { hideTimer = setTimeout(closeSuggest, 150); });
    input.addEventListener('focus', function () { clearTimeout(hideTimer); });
    goBtn.addEventListener('click', submit);

    // ---- 取数
    function query(sym) {
      var seq = ++state.seq;
      var adj = sym.kind === 'index' ? '' : state.adjRequested;
      var label = (sym.name ? sym.name + ' ' : '') + sym.symbol;
      setStatus('正在加载 ' + label + ' 的日线…', false);
      goBtn.disabled = true;
      body.setAttribute('aria-busy', 'true');
      K.loadKline(sym.symbol, adj).then(function (r) {
        if (seq !== state.seq) return;
        state.sym = Object.assign({}, sym, { symbol: r.symbol, code: r.code, market: r.market });
        state.name = r.name || sym.name || r.code;
        state.kind = sym.kind || (r.adj === 'none' && adj !== '' ? 'index' : 'stock');
        state.adj = r.adj;
        state.data = r;
        var isIndex = state.kind === 'index';
        adjGroup.hidden = isIndex;
        noAdj.hidden = !isIndex;
        if (!isIndex) setActive(adjGroup, state.adjRequested === 'qfq' ? 1 : 0);
        var adjText = ADJ_LABEL[r.adj] + (isIndex ? '（指数）' : '');
        var extra = r.fromCache ? '会话缓存' : r.requests + ' 次请求';
        var title = r.rebased && r.rebased.length ? '窗口拼接时有 ' + r.rebased.length + ' 处后复权基准按重叠日比值换算' : null;
        setStatus('已加载 ' + fmt.num(r.n) + ' 根日线 · ' + r.first + ' → ' + r.last + ' · ' + adjText + ' · ' + extra, false, title);
        ctx.setAsOf('数据截至 ' + r.last + ' · 腾讯行情 · 代码表 ' + fmt.date(json.asOf));
        applyView();
      }).catch(function (err) {
        if (seq !== state.seq) return;
        var code = err && err.code;
        var msg = code === 'NO_DATA' ? err.message
          : code === 'NETWORK' ? '腾讯行情接口暂时不可达，请一两分钟后重试（短时间内查询过多会被腾讯限流；' + (err.message || '') + '）'
            : '数据解析失败：' + (err && err.message ? err.message : err);
        state.data = null;
        state.A = null;
        clearOutput();
        setStatus(msg, true);
        if (window.console && console.warn) console.warn('[stock] ' + sym.symbol + ' 加载失败:', err && err.message);
      }).then(function () {
        if (seq !== state.seq) return;
        goBtn.disabled = false;
        body.removeAttribute('aria-busy');
      });
    }

    // ---- 计算与渲染（每次成功查询、以及之后每次切换时间窗 / 阈值都重算，并把完整状态写回地址栏）
    function applyView() {
      var d = state.data;
      if (!d) return;
      ctx.setHashQuery({ s: d.symbol, r: state.range, th: state.th, adj: state.kind === 'index' ? null : state.adjRequested });
      var view = BB.sliceByRange(d.bars, state.range);
      if (!view || view.length < 2) { clearOutput(); setStatus('该时间窗内不足 2 根日线', true); return; }
      var A = BB.analyze(view, state.th, 'auto');
      state.A = A;
      var S = BB.summary(A);
      var lay = layout();
      var rangeLabel = RANGES.filter(function (r) { return r.key === state.range; })[0].label;
      var adjLabel = ADJ_LABEL[state.adj];

      head.innerHTML = '';
      var strong = C.el('strong', null, state.name);
      head.appendChild(strong);
      head.appendChild(C.el('span', 'stock-code', d.symbol));
      var closeSpan = C.el('span', null, '最新收盘 ');
      var closeVal = C.el('span', 'stock-close', fmt.num(S.lastClose, 2));
      closeVal.setAttribute('data-close', String(S.lastClose));
      closeSpan.appendChild(closeVal);
      closeSpan.appendChild(document.createTextNode('（' + adjLabel + ' · ' + S.last + '）'));
      head.appendChild(closeSpan);
      if (d.quote && d.quote.price !== null && d.quote.price !== undefined) {
        head.appendChild(C.el('span', null, '最新价 ' + fmt.num(d.quote.price, 2) + (d.quote.time ? '（' + fmtTime(d.quote.time) + '）' : '')));
      }
      head.appendChild(C.el('span', null, '窗口 ' + S.first + ' → ' + S.last + '（' + rangeLabel + '）'));
      var cagrSpan = C.el('span', null, '区间年化 ');
      var cagrVal = C.el('span', isNaN(S.cagr) ? null : (S.cagr >= 0 ? 'pos' : 'neg'), isNaN(S.cagr) ? '–' : C.fmtSignedPct(S.cagr * 100, 1));
      cagrVal.setAttribute('data-cagr', isNaN(S.cagr) ? '' : String(S.cagr));
      cagrSpan.appendChild(cagrVal);
      head.appendChild(cagrSpan);
      head.appendChild(C.el('span', null, '累计 ' + C.fmtMult(S.multiple, 2)));
      head.appendChild(C.el('span', null, '最大回撤 ' + (S.maxDD ? fmt.pct(S.maxDD.depth * 100, 1) : '–')));
      head.appendChild(C.el('span', null, 'ZigZag ' + Math.round(state.th * 100) + '%（' + { D: '日', W: '周', M: '月' }[S.basis] + '线找转折）'));

      priceChart.setOption(priceOption(A, S, ctx, lay, state), { notMerge: true });
      ddChart.setOption(drawdownOption(A, S, ctx, lay), { notMerge: true });
      ddNote.textContent = S.maxDD
        ? '最大回撤 ' + fmt.pct(S.maxDD.depth * 100, 1) + '：前高 ' + S.maxDD.peak + ' → 低点 ' + S.maxDD.trough + ' → 修复 ' + (S.maxDD.recovery || '–（尚未修复）') +
          ' · 当前距前高 ' + fmt.pct(S.curDD * 100, 1) + '（前高 ' + S.curPeak + '）· 年化波动 ' + (isNaN(S.vol) ? '–' : fmt.pct(S.vol * 100, 1))
        : '窗口内没有回撤（收盘从未低于前高）· 年化波动 ' + (isNaN(S.vol) ? '–' : fmt.pct(S.vol * 100, 1));

      // 阶段表
      phasesHost.innerHTML = '';
      var rows = BB.phaseRows(A).map(function (p) {
        var kindText = (p.bull ? '牛' : '熊') + (p.first ? '（起点截断）' : '') + (p.ongoing ? '（进行中）' : '');
        var cls = p.bull ? 'pos' : 'neg';
        return [
          { t: p.from + ' → ' + p.to, v: p.from },
          { t: kindText, v: p.bull ? 1 : 0, cls: cls },
          { t: C.fmtSignedPct(p.ret * 100, 1), v: p.ret, cls: cls },
          { t: fmt.num(p.days), v: p.days },
          { t: p.ann === null ? '–' : C.fmtSignedPct(p.ann * 100, 1), v: p.ann, cls: p.ann === null ? 'dim' : cls }
        ];
      });
      C.makeTable(phasesHost, ['阶段起止', '牛熊', '涨跌幅', '天数', '年化'], rows, { className: 'phase-table' });

      // 多周期卡片（全部历史，不随时间窗变）
      cards.innerHTML = '';
      BB.horizons(d.bars).forEach(function (h) {
        var card = C.el('div', 'card');
        card.setAttribute('data-k', h.k);
        card.appendChild(C.el('div', 'card-label', h.k));
        var val = C.el('div', 'card-value' + (h.v === null ? ' dim' : h.v >= 0 ? ' pos' : ' neg'), h.v === null ? '–' : C.fmtSignedPct(h.v * 100, 1));
        card.appendChild(val);
        var meta = h.v === null ? h.note : (h.ann !== null ? '年化 ' + C.fmtSignedPct(h.ann * 100, 1) + ' · ' : '') + '基准 ' + h.baseDate;
        card.appendChild(C.el('div', 'card-meta', meta));
        cards.appendChild(card);
      });

      // 分年
      chips.innerHTML = '';
      BB.yearlyReturns(d.bars).forEach(function (y) {
        var chip = C.el('span', y.v >= 0 ? 'pos' : 'neg', y.y + (y.partial ? '*' : '') + ' ' + C.fmtSignedPct(y.v * 100, 1));
        if (y.fromListing) chip.title = '首个年份：自 ' + d.first + ' 起算';
        else if (y.partial) chip.title = '年内至 ' + d.last;
        chips.appendChild(chip);
      });
    }

    // ---- 地址栏参数
    function applyParams(q) {
      if (!q) return;
      var ri = RANGES.map(function (r) { return r.key; }).indexOf(q.r);
      if (ri >= 0) { state.range = RANGES[ri].key; setActive(rangeGroup, ri); }
      if (THRESHOLDS.indexOf(String(q.th)) >= 0) { state.th = +q.th; thSelect.value = String(q.th); }
      var ai = ADJS.map(function (a) { return a.key; }).indexOf(q.adj);
      if (ai >= 0) { state.adjRequested = ADJS[ai].key; setActive(adjGroup, ai); }
    }
    function fromHash(q) {
      applyParams(q);
      if (q && q.s) {
        var sym = resolveInput(index, q.s);
        if (sym) { input.value = (sym.name || sym.code) + ' ' + sym.code; query(sym); return; }
        setStatus('链接里的代码无法识别：' + q.s, true);
      }
    }
    fromHash(ctx.hashQuery());

    var lastWidth = priceEl.clientWidth, timer = null;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (priceEl.clientWidth !== lastWidth) {
          lastWidth = priceEl.clientWidth;
          if (state.data) applyView();
        }
      }, 260);
    });

    return {
      charts: [priceChart, ddChart],
      exportMeta: function () {
        if (!state.data || !state.A) throw new Error('请先查询一只证券再导出');
        var S = BB.summary(state.A);
        var adjLabel = ADJ_LABEL[state.adj];
        return {
          subtitle: state.name + ' ' + state.data.symbol + ' · ' + S.first + ' → ' + S.last + ' · ' + adjLabel + ' · ZigZag ' + Math.round(state.th * 100) + '%',
          sources: ['腾讯行情（' + adjLabel + '日线）'],
          asOf: S.last
        };
      },
      onHashQuery: fromHash
    };
  }

  window.AshareBoard.registerPanel({
    id: 'stock',
    title: '个股走势图',
    subtitle: '任意 A 股 / ETF / 指数：对数走势与区间年化 · 回撤 · ZigZag 牛熊阶段 · 多周期涨跌幅',
    dataFile: 'symbols.json',
    bodyMinHeight: 1100,
    notes: [
      '数据来源与口径：腾讯行情日线接口（web.ifzq.gtimg.cn），浏览器直接请求、不经任何服务器中转。股票 / ETF 默认后复权（把历史价格按分红送转折算到当前基准，长期曲线连续；前复权则以最新价为基准往前折算，最新值等于当前价），指数无复权；历史自 2005-01-01 或上市日起。每次查询 1 + 最多 10 次请求：先取最新一页，再按两年窗口并行补齐并按重叠日的收盘比值拼接；收盘前（15:00 之前）的当日 K 线不计入。',
      'ZigZag 规则：自阶段极值反向运行 ≥ 阈值（默认 20%）即确认转折；跌破本轮牛市的起点（上一个谷）也结束牛市。区间 ≥ 6 年用月末收盘、≥ 2 年用周末收盘、否则用日收盘找转折，再在日线上精确定位峰谷；进行中的阶段必须已自起点走出 ≥ 阈值的行情，否则并入上一阶段；首末两段按时间窗截断。',
      '「牛」「熊」只是两态技术标签：多年横盘可能被整体标成一段牛市或熊市；阈值越高阶段越少、越长。阶段表的年化只对满一年的阶段给出。',
      '多周期涨跌幅的基准日 = 该期限之前（含）最近一个交易日的收盘；年初至今以上一年最后一个交易日为基准；满一年的期限同时给年化。多周期与分年用全部历史，不随时间窗变化；时间窗只影响上面两张图与阶段表。',
      '浏览器只在本次会话内缓存已查询的日线（sessionStorage），不向任何服务器保存查询。代码联想来自 data/symbols.json（Baostock 证券资料 + 手工维护的指数 / ETF 清单），不在列表里的代码可直接输入 6 位数字（5 / 6 / 9 开头按沪市、4 / 8 开头按北交所、其余按深市）。'
    ],
    render: render
  });
})();
