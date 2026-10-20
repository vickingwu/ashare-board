/*
 * A股看板 —— 面板共用的图表与表格小工具（window.AshareCharts）。
 * 在 app.js 之后、各面板脚本之前加载。面板不要各自重写这里已有的归一、年化、渐变色、月末判断与坐标轴配置，
 * 也不要重写 N 日涨幅 / 滚动均值 / 横截面排名分位 / 热力图骨架 / 工具条小件（下拉、按钮组、面包屑）。
 */
(function () {
  'use strict';

  var board = window.AshareBoard;
  var palette = board.palette;
  var fmt = board.fmt;

  // ---------------------------------------------------------------- 日期与数值
  function parseDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : null;
  }

  function yearsBetween(d0, d1) {
    var a = parseDate(d0), b = parseDate(d1);
    if (!a || !b) return null;
    return (b - a) / 86400000 / 365.25;
  }

  // 年化收益率（%）；期限不足半年返回 null
  function cagr(v0, v1, years) {
    if (v0 === null || v1 === null || v0 <= 0 || v1 <= 0 || !(years >= 0.5)) return null;
    return (Math.pow(v1 / v0, 1 / years) - 1) * 100;
  }

  // 升序日期数组里第一个 ≥ dateStr 的下标；没有则返回 -1
  function firstIndexOnOrAfter(dates, dateStr) {
    var lo = 0, hi = dates.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (dates[mid] < dateStr) lo = mid + 1; else hi = mid;
    }
    return lo < dates.length ? lo : -1;
  }

  // endDate 往前推 years 年（同月同日；2 月 29 日退到 28 日）
  function windowStartDate(endDate, years) {
    var d = parseDate(endDate);
    if (!d) return endDate;
    var y = d.getUTCFullYear() - years, m = d.getUTCMonth(), day = d.getUTCDate();
    var t = new Date(Date.UTC(y, m, day));
    if (t.getUTCMonth() !== m) t = new Date(Date.UTC(y, m, 28));
    return t.toISOString().slice(0, 10);
  }

  // 以 values[idx] 为 base 归一；null 保留
  function normalizeFrom(values, idx, base) {
    if (base === undefined) base = 100;
    var b = (idx >= 0 && idx < values.length) ? values[idx] : null;
    if (b === null || b === undefined || b <= 0) return values.map(function () { return null; });
    return values.map(function (v) { return (v === null || v === undefined) ? null : v / b * base; });
  }

  // 该日期之后、同月内是否再无周一至周五
  function isLastWeekdayOfMonth(dateStr) {
    var d = parseDate(dateStr);
    if (!d) return false;
    var m = d.getUTCMonth();
    var t = new Date(d.getTime() + 86400000);
    while (t.getUTCMonth() === m) {
      var w = t.getUTCDay();
      if (w >= 1 && w <= 5) return false;
      t = new Date(t.getTime() + 86400000);
    }
    return true;
  }

  // 每个 YYYY-MM 最后一条的下标
  function monthEndIndexes(dates) {
    var out = [];
    for (var i = 0; i < dates.length; i++) {
      var next = dates[i + 1];
      if (!next || next.slice(0, 7) !== dates[i].slice(0, 7)) out.push(i);
    }
    return out;
  }

  function fmtMult(x, d) {
    if (x === null || x === undefined || isNaN(x)) return '–';
    return fmt.num(x, d === undefined ? 2 : d) + ' 倍';
  }

  // 带符号的百分点
  function fmtPp(x, d) {
    if (x === null || x === undefined || isNaN(x)) return '–';
    if (d === undefined) d = 1;
    var s = Math.abs(x).toFixed(d);
    return (x < 0 ? '\u2212' : '+') + s;
  }

  function fmtSignedPct(x, d) {
    if (x === null || x === undefined || isNaN(x)) return '–';
    return fmtPp(x, d) + '%';
  }

  // ---------------------------------------------------------------- 序列计算（热力图 / 换手率面板共用；空值一律 null）
  function isNum(v) { return v !== null && v !== undefined && typeof v === 'number' && !isNaN(v); }

  // n 步涨跌幅（%）：values[i] / values[i-n] − 1；下标 < n 或两端任一为空 → null
  function pctChangeN(values, n) {
    var out = new Array(values.length);
    for (var i = 0; i < values.length; i++) {
      var a = i >= n ? values[i - n] : null, b = values[i];
      out[i] = (isNum(a) && isNum(b) && a > 0) ? (b / a - 1) * 100 : null;
    }
    return out;
  }

  // 尾随 n 个值的简单均值；窗口内任一为空 → null
  function rollingMean(values, n) {
    var out = new Array(values.length);
    for (var i = 0; i < values.length; i++) {
      if (i < n - 1) { out[i] = null; continue; }
      var sum = 0, ok = true;
      for (var j = i - n + 1; j <= i; j++) {
        if (!isNum(values[j])) { ok = false; break; }
        sum += values[j];
      }
      out[i] = ok ? sum / n : null;
    }
    return out;
  }

  // 尾随 n 个布尔值里 true 的占比（%）；窗口内有 null/undefined → null
  function rollingShare(flags, n) {
    var out = new Array(flags.length);
    for (var i = 0; i < flags.length; i++) {
      if (i < n - 1) { out[i] = null; continue; }
      var cnt = 0, ok = true;
      for (var j = i - n + 1; j <= i; j++) {
        var f = flags[j];
        if (f !== true && f !== false) { ok = false; break; }
        if (f) cnt++;
      }
      out[i] = ok ? cnt / n * 100 : null;
    }
    return out;
  }

  // 同一天的横截面排名分位：100 × #(v_j < v_i) / (n − 1)，n = 非空个数（n ≥ 2，否则全为 null）；1 位小数
  function rankPct(vals) {
    var sorted = [];
    for (var i = 0; i < vals.length; i++) if (isNum(vals[i])) sorted.push(vals[i]);
    var n = sorted.length;
    if (n < 2) return vals.map(function () { return null; });
    sorted.sort(function (a, b) { return a - b; });
    return vals.map(function (v) {
      if (!isNum(v)) return null;
      var lo = 0, hi = n;                 // 第一个 ≥ v 的位置 = 严格小于 v 的个数
      while (lo < hi) { var mid = (lo + hi) >> 1; if (sorted[mid] < v) lo = mid + 1; else hi = mid; }
      return Math.round(lo / (n - 1) * 1000) / 10;
    });
  }

  // x 在 arr 里的含等号分位（0–100）：#(v ≤ x) / n；空值跳过；无样本或 x 为空 → null
  function percentileRank(arr, x) {
    if (!isNum(x)) return null;
    var n = 0, le = 0;
    for (var i = 0; i < arr.length; i++) {
      if (!isNum(arr[i])) continue;
      n++;
      if (arr[i] <= x) le++;
    }
    return n ? le / n * 100 : null;
  }

  // 均值与样本标准差（空值跳过；n < 2 时 std = 0，n = 0 时 mean = null）
  function meanStd(arr) {
    var n = 0, sum = 0;
    for (var i = 0; i < arr.length; i++) if (isNum(arr[i])) { n++; sum += arr[i]; }
    if (!n) return { mean: null, std: 0 };
    var mean = sum / n, ss = 0;
    for (var j = 0; j < arr.length; j++) if (isNum(arr[j])) ss += (arr[j] - mean) * (arr[j] - mean);
    return { mean: mean, std: n > 1 ? Math.sqrt(ss / (n - 1)) : 0 };
  }

  // ---------------------------------------------------------------- 颜色
  function hexToRgb(hex) {
    var h = hex.replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(function (v) {
      var s = Math.max(0, Math.min(255, Math.round(v))).toString(16);
      return s.length === 1 ? '0' + s : s;
    }).join('');
  }

  function lerpColor(hexA, hexB, t) {
    var a = hexToRgb(hexA), b = hexToRgb(hexB);
    t = Math.max(0, Math.min(1, t));
    return rgbToHex(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t);
  }

  var MID_NEUTRAL = '#E6C84B';   // 绿（低/便宜）→ 黄 → 红（高/贵）的中间色
  function gradientGreenRed(t) {
    t = Math.max(0, Math.min(1, t));
    return t < 0.5 ? lerpColor(palette.down, MID_NEUTRAL, t * 2) : lerpColor(MID_NEUTRAL, palette.up, (t - 0.5) * 2);
  }

  function withAlpha(hex, alpha) {
    var c = hexToRgb(hex);
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + alpha + ')';
  }

  // ---------------------------------------------------------------- ECharts 配置片段
  function logAxis(opts) {
    opts = opts || {};
    return {
      type: 'log',
      logBase: 10,
      name: opts.name || '',
      nameTextStyle: { color: palette.muted, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#E5E7EB' } },
      minorSplitLine: { show: true, lineStyle: { color: '#F3F4F6' } },
      axisLabel: {
        color: palette.muted, fontSize: 11,
        formatter: function (v) { return v >= 100 ? fmt.num(v, 0) : fmt.num(v, v >= 10 ? 0 : 1); }
      }
    };
  }

  // 稀疏的年份刻度：每年第一个点显示 YYYY；step > 1 时每 step 年只标一个（窄屏上 20 多个年份会叠在一起）
  function yearLabelFormatter(dates, step) {
    step = step || 1;
    var firstYear = dates.length ? +dates[0].slice(0, 4) : 0;
    return function (value, idx) {
      var prev = dates[idx - 1];
      if (prev && prev.slice(0, 4) === value.slice(0, 4)) return '';
      return ((+value.slice(0, 4) - firstYear) % step === 0) ? value.slice(0, 4) : '';
    };
  }

  // 按绘图区宽度算年份刻度的步长：每个标签至少留 minPx（默认 44）像素
  function yearLabelStep(dates, plotWidthPx, minPx) {
    if (!dates || !dates.length) return 1;
    var nYears = +dates[dates.length - 1].slice(0, 4) - +dates[0].slice(0, 4) + 1;
    var slots = Math.max(1, Math.floor((plotWidthPx || 0) / (minPx || 44)));
    return Math.max(1, Math.ceil(nYears / slots));
  }

  function baseLineOption(opts) {
    opts = opts || {};
    var dates = opts.dates || [];
    return {
      animation: false,
      tooltip: { trigger: 'axis', confine: true, textStyle: { fontSize: 12 }, axisPointer: { type: 'line' } },
      legend: opts.legend === false ? { show: false } : { top: opts.legendTop || 0, textStyle: { fontSize: 12 }, itemWidth: 18, itemHeight: 3 },
      grid: opts.grid || { left: 56, right: 120, top: 36, bottom: 32 },
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#D1D5DB' } },
        axisTick: { show: false },
        axisLabel: { color: palette.muted, fontSize: 11, interval: 0, showMaxLabel: false, formatter: yearLabelFormatter(dates, opts.yearStep) },
        splitLine: { show: false }
      },
      yAxis: { type: 'value' },
      series: []
    };
  }

  // 均值 ±1σ 阴影 + ±2σ 虚线 + 均值实线
  function sigmaBands(mean, std, color, decimals) {
    if (decimals === undefined) decimals = 2;
    var line = { color: color || palette.muted };
    return {
      markArea: {
        silent: true,
        itemStyle: { color: withAlpha(color || palette.muted, 0.10) },
        data: [[{ yAxis: mean - std }, { yAxis: mean + std }]]
      },
      markLine: {
        silent: true,
        symbol: 'none',
        animation: false,
        label: { position: 'insideEndTop', fontSize: 10, color: palette.muted, formatter: function (p) { return p.name; } },
        data: [
          { yAxis: mean, name: '均值 ' + mean.toFixed(decimals), lineStyle: { color: line.color, width: 1 } },
          { yAxis: mean + 2 * std, name: '+2σ', lineStyle: { color: line.color, type: 'dashed', width: 1 } },
          { yAxis: mean - 2 * std, name: '\u22122σ', lineStyle: { color: line.color, type: 'dashed', width: 1 } }
        ]
      }
    };
  }

  // 热力图配置骨架：类目轴 × 类目轴，纵轴从上往下，不画网格线与刻度；面板只需补 series / visualMap / 标签
  function heatmapBase(opts) {
    opts = opts || {};
    var narrow = !!opts.narrow;
    return {
      animation: false,
      tooltip: { trigger: 'item', confine: true, textStyle: { fontSize: 12 } },
      grid: { left: narrow ? 84 : 120, right: narrow ? 12 : 24, top: 16, bottom: narrow ? 60 : 36 },
      xAxis: {
        type: 'category', data: opts.xCats || [],
        splitArea: { show: false }, axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { color: palette.muted, fontSize: 10.5, rotate: 0, interval: 0 }
      },
      yAxis: {
        type: 'category', data: opts.yCats || [], inverse: true,
        splitArea: { show: false }, axisTick: { show: false }, axisLine: { show: false },
        axisLabel: { color: palette.fg, fontSize: narrow ? 11 : 12 }
      },
      series: []
    };
  }

  // 热力图格内文字
  function heatCellLabel(show, formatter) {
    return { show: !!show, fontSize: 10, color: palette.fg, formatter: formatter };
  }

  // ---------------------------------------------------------------- DOM 小件
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  // 按钮组；onPick(index) 在点击时调用。
  // container 本身是 .panel-tools 时，按钮装进一个 span.tools-group 追加到该工具条里（可带 opts.label 前缀），返回这个 span；
  // 否则新建一个 .panel-tools 挂到 container 下并返回它（旧行为）。
  function rangeButtons(container, labels, activeIdx, onPick, opts) {
    opts = opts || {};
    var inline = !!(container.classList && container.classList.contains('panel-tools'));
    var box = el(inline ? 'span' : 'div', inline ? 'tools-group' : 'panel-tools');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', opts.label || '时间窗');
    if (opts.label) box.appendChild(el('span', 'tools-label', opts.label));
    var btns = labels.map(function (label, i) {
      var b = el('button', 'range-btn' + (i === activeIdx ? ' active' : ''), label);
      b.type = 'button';
      b.setAttribute('aria-pressed', i === activeIdx ? 'true' : 'false');
      b.addEventListener('click', function () {
        btns.forEach(function (o, j) {
          o.classList.toggle('active', j === i);
          o.setAttribute('aria-pressed', j === i ? 'true' : 'false');
        });
        onPick(i);
      });
      box.appendChild(b);
      return b;
    });
    container.appendChild(box);
    return box;
  }

  // 下拉选择：「标签 + <select>」追加到 container（通常是 .panel-tools），返回 select 元素
  function selectControl(container, spec) {
    spec = spec || {};
    var wrap = el('span', 'tools-group');
    if (spec.label) wrap.appendChild(el('span', 'tools-label', spec.label));
    var sel = el('select', 'inline-select');
    if (spec.id) sel.id = spec.id;
    if (spec.name) sel.name = spec.name;
    sel.setAttribute('aria-label', spec.label || spec.name || spec.id || '选择');
    (spec.options || []).forEach(function (o) {
      var opt = el('option', null, o.text);
      opt.value = String(o.value);
      if (spec.value !== undefined && String(o.value) === String(spec.value)) opt.selected = true;
      sel.appendChild(opt);
    });
    if (typeof spec.onChange === 'function') {
      sel.addEventListener('change', function () { spec.onChange(sel.value, sel); });
    }
    wrap.appendChild(sel);
    container.appendChild(wrap);
    return sel;
  }

  // 面包屑：items 用「›」连接（最后一项加粗）；给了 onBack 就在末尾放一个「返回一级」按钮。返回 .crumbs 元素
  function crumbs(container, items, onBack) {
    var box = el('div', 'crumbs');
    items.forEach(function (t, i) {
      if (i) box.appendChild(el('span', 'sep', '\u203A'));
      box.appendChild(el(i === items.length - 1 ? 'strong' : 'span', null, t));
    });
    if (typeof onBack === 'function') {
      var b = el('button', 'range-btn btn-back', '返回一级');
      b.type = 'button';
      b.addEventListener('click', onBack);
      box.appendChild(b);
    }
    container.appendChild(box);
    return box;
  }

  // 数据表：headers = [{text, numeric}] 或字符串；rows = [[cell,...]]，cell 为 {t: 显示文本, v: 排序值, cls} 或原始值
  function makeTable(container, headers, rows, opts) {
    opts = opts || {};
    var table = el('table', 'data-table' + (opts.className ? ' ' + opts.className : ''));
    if (opts.caption) table.appendChild(el('caption', null, opts.caption));
    var thead = el('thead'), tr = el('tr');
    var hs = headers.map(function (h) { return typeof h === 'string' ? { text: h } : h; });
    var state = { col: -1, desc: true };

    function cellOf(c) {
      if (c !== null && typeof c === 'object') return c;
      return { t: (c === null || c === undefined) ? '–' : String(c), v: c };
    }
    var data = rows.map(function (r) { return r.map(cellOf); });

    function renderBody() {
      var tbody = table.querySelector('tbody') || el('tbody');
      tbody.innerHTML = '';
      var order = data.slice();
      if (state.col >= 0) {
        var c = state.col;
        order.sort(function (a, b) {
          var va = a[c].v, vb = b[c].v;
          var na = (va === null || va === undefined || va === ''), nb = (vb === null || vb === undefined || vb === '');
          if (na && nb) return 0;
          if (na) return 1;          // 空值永远排最后
          if (nb) return -1;
          var r = (typeof va === 'number' && typeof vb === 'number') ? va - vb : String(va).localeCompare(String(vb), 'zh');
          return state.desc ? -r : r;
        });
      }
      order.forEach(function (r) {
        var trr = el('tr');
        r.forEach(function (c, i) {
          var td = el(i === 0 ? 'th' : 'td', c.cls || null, c.t);
          if (i === 0) td.setAttribute('scope', 'row');
          if (c.title) td.title = c.title;
          if (c.style) td.setAttribute('style', c.style);
          trr.appendChild(td);
        });
        tbody.appendChild(trr);
      });
      if (!tbody.parentNode) table.appendChild(tbody);
      hs.forEach(function (h, i) {
        var th = tr.children[i];
        if (!opts.sortable) return;
        if (i === state.col) th.setAttribute('aria-sort', state.desc ? 'descending' : 'ascending');
        else th.removeAttribute('aria-sort');
      });
    }

    hs.forEach(function (h, i) {
      var th = el('th', opts.sortable ? 'sortable' : null, h.text);
      th.setAttribute('scope', 'col');
      if (opts.sortable) {
        th.tabIndex = 0;
        th.title = '点击排序';
        var toggle = function () {
          if (state.col === i) state.desc = !state.desc;
          else { state.col = i; state.desc = h.numeric !== false; }
          renderBody();
        };
        th.addEventListener('click', toggle);
        th.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); } });
      }
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    table.appendChild(thead);
    if (opts.defaultSort) { state.col = opts.defaultSort.col; state.desc = opts.defaultSort.desc !== false; }
    renderBody();
    var wrap = el('div', 'scroll-x');
    wrap.appendChild(table);
    container.appendChild(wrap);
    return table;
  }

  function colorCell(hex) {
    var sw = el('span', 'swatch');
    sw.style.background = hex;
    return sw;
  }

  window.AshareCharts = {
    parseDate: parseDate,
    yearsBetween: yearsBetween,
    cagr: cagr,
    firstIndexOnOrAfter: firstIndexOnOrAfter,
    windowStartDate: windowStartDate,
    normalizeFrom: normalizeFrom,
    isLastWeekdayOfMonth: isLastWeekdayOfMonth,
    monthEndIndexes: monthEndIndexes,
    fmtMult: fmtMult,
    fmtPp: fmtPp,
    fmtSignedPct: fmtSignedPct,
    pctChangeN: pctChangeN,
    rollingMean: rollingMean,
    rollingShare: rollingShare,
    rankPct: rankPct,
    percentileRank: percentileRank,
    meanStd: meanStd,
    heatmapBase: heatmapBase,
    heatCellLabel: heatCellLabel,
    lerpColor: lerpColor,
    gradientGreenRed: gradientGreenRed,
    withAlpha: withAlpha,
    logAxis: logAxis,
    yearLabelFormatter: yearLabelFormatter,
    yearLabelStep: yearLabelStep,
    baseLineOption: baseLineOption,
    sigmaBands: sigmaBands,
    el: el,
    rangeButtons: rangeButtons,
    selectControl: selectControl,
    crumbs: crumbs,
    makeTable: makeTable,
    colorCell: colorCell
  };
})();
