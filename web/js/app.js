/*
 * A股看板 —— 前端框架。
 *
 * 职责：面板注册表、导航、懒加载 data/<id>.json、数据新鲜度条、深链接、窗口缩放、PNG 导出。
 * 面板模块只需调用 AshareBoard.registerPanel({...render(body, json, ctx)}) 即可接入。
 * 不依赖任何构建工具；ES2017+，直接在浏览器运行。
 */
(function () {
  'use strict';

  var DATA_BASE = 'data/';

  // ---------------------------------------------------------------- 配色（唯一定义处）
  var palette = {
    '000300': '#1F5FBF',   // 沪深300  蓝
    '000905': '#0F9D8A',   // 中证500  青
    '000852': '#E0A100',   // 中证1000 琥珀
    '932000': '#D9542B',   // 中证2000 橙红
    '000985': '#4B5563',   // 中证全指 石板灰
    '000510': '#7C4DFF',   // 中证A500 紫
    '000906': '#C026D3',   // 中证800  洋红
    '000688': '#0891B2',   // 科创50   青蓝
    '000016': '#DB2777',   // 上证50   玫红
    'H30269': '#7F1D1D',   // 红利低波 栗色（全收益 H20269 同色）
    'H20269': '#7F1D1D',
    uncovered: '#B0B7C3',  // 未覆盖   浅灰
    dividend: '#2E9E6B',   // 收益拆解：股息
    earnings: '#5A6FD8',   // 收益拆解：盈利
    valuation: '#E8903A',  // 收益拆解：估值
    amount: '#AEB9CC',     // 成交额柱
    amountMa: '#3B5BDB',   // 成交额 20 日均线
    turnover: '#B7410E',   // 换手率折线
    heatLow: '#F2F6FC',    // 单色热力图：最冷
    heatHigh: '#1F5FBF',   // 单色热力图：最热
    neutral: '#FFFFFF',    // 红绿双向色阶的中点
    up: '#D64541',
    down: '#1E9E5A',
    muted: '#6B7280',
    fg: '#1F2328'
  };

  // ---------------------------------------------------------------- 格式化
  var fmt = {
    pct: function (x, d) {
      if (x === null || x === undefined || isNaN(x)) return '–';
      if (d === undefined) d = 1;
      var s = Math.abs(x).toFixed(d) + '%';
      return x < 0 ? '\u2212' + s : s;   // U+2212 真减号
    },
    num: function (x, d) {
      if (x === null || x === undefined || isNaN(x)) return '–';
      if (d === undefined) d = 0;
      var parts = Math.abs(x).toFixed(d).split('.');
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      return (x < 0 ? '\u2212' : '') + parts.join('.');
    },
    yi: function (x, d) {
      return fmt.num(x, d === undefined ? 0 : d) + ' 亿';
    },
    date: function (s) {
      return s ? String(s).slice(0, 10) : '–';
    }
  };

  // ---------------------------------------------------------------- 注册表与状态
  var registry = [];          // 注册顺序即页面顺序
  var byId = {};
  var fetchCache = new Map(); // url -> Promise<json>
  var manifestPromise = null;

  function registerPanel(spec) {
    if (!spec || !spec.id || typeof spec.render !== 'function') {
      throw new Error('registerPanel 需要 {id, title, dataFile, render}');
    }
    var p = {
      id: spec.id,
      title: spec.title || spec.id,
      subtitle: spec.subtitle || '',
      dataFile: spec.dataFile || (spec.id + '.json'),
      notes: spec.notes || [],
      bodyMinHeight: spec.bodyMinHeight || 420,
      render: spec.render,
      el: null,
      bodyEl: null,
      charts: [],
      json: null,
      loadPromise: null
    };
    registry.push(p);
    byId[p.id] = p;
    return p;
  }

  function fetchJSON(file) {
    var url = DATA_BASE + file;
    if (!fetchCache.has(url)) {
      fetchCache.set(url, fetch(url, { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
        return r.json();
      }));
    }
    return fetchCache.get(url);
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'hidden') e.hidden = !!attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function debounce(fn, ms) {
    var t = null;
    return function () {
      clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }

  // ---------------------------------------------------------------- 构建页面骨架
  function buildShell() {
    var nav = document.getElementById('nav');
    var main = document.getElementById('main');
    registry.forEach(function (p) {
      var link = el('a', { class: 'nav-link', href: '#panel-' + p.id, 'data-panel': p.id }, p.title);
      nav.appendChild(link);

      var section = el('section', { class: 'panel', id: 'panel-' + p.id, 'aria-labelledby': 'panel-' + p.id + '-title' });
      var head = el('div', { class: 'panel-head' });
      head.appendChild(el('h2', { id: 'panel-' + p.id + '-title' }, p.title));
      var btn = el('button', { class: 'btn-export', type: 'button', 'aria-label': '导出 ' + p.title + ' 为 PNG', hidden: true }, '导出 PNG');
      btn.addEventListener('click', function () { downloadPanel(p.id, btn); });
      head.appendChild(btn);
      section.appendChild(head);
      section.appendChild(el('p', { class: 'panel-subtitle' }, p.subtitle));
      var asof = el('p', { class: 'panel-asof' });
      section.appendChild(asof);
      var body = el('div', { class: 'panel-body' });
      body.style.minHeight = p.bodyMinHeight + 'px';
      body.appendChild(el('p', { class: 'panel-loading' }, '加载中…'));
      section.appendChild(body);
      var details = el('details', { class: 'panel-notes' });
      details.appendChild(el('summary', null, '看板说明'));
      var ul = el('ul');
      p.notes.forEach(function (n) { ul.appendChild(el('li', null, n)); });
      details.appendChild(ul);
      section.appendChild(details);
      main.appendChild(section);

      p.el = section;
      p.bodyEl = body;
      p.btnExport = btn;
      p.asofEl = asof;
      p.notesUl = ul;
      p.headEl = head;
    });
  }

  // ---------------------------------------------------------------- 加载与渲染单个面板
  function loadPanel(id) {
    var p = byId[id];
    if (!p) return Promise.reject(new Error('未注册的面板: ' + id));
    if (p.loadPromise) return p.loadPromise;
    p.loadPromise = fetchJSON(p.dataFile).then(function (json) {
      p.json = json;
      p.asofEl.textContent = '';
      p.asofEl.appendChild(document.createTextNode('数据截至 ' + fmt.date(json.asOf)));
      if (json.generated) {
        p.asofEl.appendChild(el('span', { class: 'asof-suffix' }, ' · 生成于 ' + String(json.generated).replace('T', ' ').slice(0, 16)));
      }
      (json.notes || []).forEach(function (n) { p.notesUl.appendChild(el('li', null, n)); });
      p.bodyEl.innerHTML = '';
      var out = p.render(p.bodyEl, json, { echarts: window.echarts, palette: palette, fmt: fmt });
      p.charts = (out && out.charts) ? out.charts.filter(Boolean) : [];
      p.btnExport.hidden = p.charts.length === 0;
      return p;
    }).catch(function (err) {
      p.bodyEl.innerHTML = '';
      var box = el('div', { class: 'panel-error', role: 'alert' });
      box.textContent = '\u26A0 数据加载失败：' + (err && err.message ? err.message : String(err));
      p.bodyEl.appendChild(box);
      p.bodyEl.style.minHeight = '0';
      p.charts = [];
      p.btnExport.hidden = true;
      if (window.console && console.warn) console.warn('[AshareBoard] 面板 ' + id + ' 加载失败:', err && err.message);
      return p;
    });
    return p.loadPromise;
  }

  // ---------------------------------------------------------------- 新鲜度条
  function weekdaysBetween(fromISO, toDate) {
    // 统计满足 fromISO < d <= toDate 的周一至周五天数（本地日期；不扣节假日）
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(fromISO || '');
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3]);
    var end = new Date(toDate.getFullYear(), toDate.getMonth(), toDate.getDate());
    var n = 0;
    while (d < end) {
      d.setDate(d.getDate() + 1);
      var w = d.getDay();
      if (w >= 1 && w <= 5) n++;
      if (n > 10000) break;
    }
    return n;
  }

  function renderFreshness(manifest) {
    var box = document.getElementById('freshness');
    var panels = (manifest && manifest.panels) || {};
    var asOf = null;
    Object.keys(panels).forEach(function (k) {
      var e = panels[k];
      if (e && e.ok && !e.static && e.asOf && (!asOf || e.asOf > asOf)) asOf = e.asOf;
    });
    if (!asOf && manifest && manifest.generated) asOf = String(manifest.generated).slice(0, 10);
    var n = asOf ? weekdaysBetween(asOf, new Date()) : null;
    box.className = 'freshness ' + (n === null ? 'fresh-amber' : n <= 1 ? 'fresh-green' : n <= 5 ? 'fresh-amber' : 'fresh-red');
    box.textContent = '';
    box.appendChild(document.createTextNode(
      '数据更新至 ' + (asOf || '未知') + ' · ' + (n === null ? '?' : n) + ' 个交易日前（按工作日估算，未扣节假日） · 手工更新：'));
    box.appendChild(el('code', null, 'make update'));

    Object.keys(panels).forEach(function (k) {
      var e = panels[k];
      var p = byId[k];
      if (!p || !e || e.ok) return;
      var txt = e.asOf ? '本次更新失败，显示 ' + e.asOf + ' 的上一版数据' : '本次更新失败，暂无数据';
      var badge = el('span', { class: 'panel-badge-stale', title: e.error || '' }, txt);
      p.headEl.appendChild(badge);
    });
  }

  function loadManifest() {
    if (!manifestPromise) {
      manifestPromise = fetchJSON('manifest.json').then(renderFreshness).catch(function (err) {
        var box = document.getElementById('freshness');
        box.className = 'freshness fresh-amber';
        box.textContent = '\u26A0 无法读取 data/manifest.json（' + (err && err.message ? err.message : err) + '）· 先运行 make update';
      });
    }
    return manifestPromise;
  }

  // ---------------------------------------------------------------- 导出 PNG
  function loadImage(dataUrl) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('图片解码失败')); };
      img.src = dataUrl;
    });
  }

  function exportPanel(id) {
    var p = byId[id];
    if (!p) return Promise.reject(new Error('未注册的面板: ' + id));
    return loadPanel(id).then(function () {
      if (!p.charts.length) throw new Error('该面板没有可导出的图表');
      var urls = p.charts.map(function (c) {
        return c.getDataURL({ type: 'png', pixelRatio: 3, backgroundColor: '#ffffff' });
      });
      return Promise.all(urls.map(loadImage));
    }).then(function (imgs) {
      var S = 3;
      var headerH = 90 * S, footerH = 56 * S, gap = 10 * S;
      var width = Math.max.apply(null, imgs.map(function (i) { return i.width; }));
      var height = headerH + footerH + imgs.reduce(function (a, i) { return a + i.height + gap; }, 0);
      var canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      var ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      var fontStack = '-apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';
      ctx.fillStyle = palette.fg;
      ctx.font = 'bold ' + (20 * S) + 'px ' + fontStack;
      ctx.textBaseline = 'top';
      ctx.fillText(p.title, 24 * S, 22 * S);
      ctx.fillStyle = palette.muted;
      ctx.font = (13 * S) + 'px ' + fontStack;
      ctx.fillText(p.subtitle, 24 * S, 54 * S);
      var y = headerH;
      imgs.forEach(function (img) {
        ctx.drawImage(img, 0, y);
        y += img.height + gap;
      });
      var json = p.json || {};
      var today = new Date();
      var todayStr = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0');
      ctx.fillStyle = palette.muted;
      ctx.font = (12 * S) + 'px ' + fontStack;
      var foot = '数据来源：' + ((json.sources || []).join(' · ') || '—') + ' · 数据截至 ' + fmt.date(json.asOf) +
        ' · 导出于 ' + todayStr;
      ctx.fillText(foot, 24 * S, height - footerH + 18 * S, width - 48 * S);
      return canvas.toDataURL('image/png');
    });
  }

  function downloadPanel(id, btn) {
    var p = byId[id];
    if (btn) btn.disabled = true;
    exportPanel(id).then(function (dataUrl) {
      var a = document.createElement('a');
      a.href = dataUrl;
      a.download = id + '_' + fmt.date(p.json && p.json.asOf) + '.png';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }).catch(function (err) {
      window.alert('导出失败：' + (err && err.message ? err.message : err));
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  // ---------------------------------------------------------------- 启动
  function init() {
    buildShell();
    loadManifest();

    // 懒加载：进入视口前 600px 开始取数
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            var id = en.target.id.replace(/^panel-/, '');
            loadPanel(id);
            io.unobserve(en.target);
          }
        });
      }, { rootMargin: '600px 0px' });
      registry.forEach(function (p) { io.observe(p.el); });

      // 导航高亮：取当前可见比例最大的面板
      var ratios = {};
      var navIo = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { ratios[en.target.id] = en.isIntersecting ? en.intersectionRatio : 0; });
        var best = null;
        Object.keys(ratios).forEach(function (k) { if (ratios[k] > 0 && (best === null || ratios[k] > ratios[best])) best = k; });
        document.querySelectorAll('.nav-link').forEach(function (a) {
          a.classList.toggle('active', best !== null && a.getAttribute('href') === '#' + best);
        });
      }, { threshold: [0.25, 0.5] });
      registry.forEach(function (p) { navIo.observe(p.el); });
    } else {
      registry.forEach(function (p) { loadPanel(p.id); });
    }

    // 深链接：先把目标及其上方的面板都渲染好（避免上方面板撑开后目标位移），再滚动一次。
    // 期间关闭浏览器的滚动位置恢复，并在就位后才启用平滑滚动，否则两者会和这次定位打架。
    var enableSmooth = function () { document.documentElement.classList.add('smooth'); };
    var m = /^#panel-([\w-]+)$/.exec(location.hash || '');
    if (m && byId[m[1]]) {
      var targetId = m[1];
      if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
      var idx = registry.findIndex(function (p) { return p.id === targetId; });
      var above = registry.slice(0, idx + 1).map(function (p) { return loadPanel(p.id); });
      Promise.all(above).then(function () {
        requestAnimationFrame(function () {
          byId[targetId].el.scrollIntoView({ block: 'start', behavior: 'instant' });
          setTimeout(enableSmooth, 150);
        });
      });
    } else {
      enableSmooth();
    }

    window.addEventListener('resize', debounce(function () {
      registry.forEach(function (p) {
        p.charts.forEach(function (c) { if (c && typeof c.resize === 'function') c.resize(); });
      });
    }, 200));

    // 把配色写进 CSS 变量，样式表可直接使用 var(--idx-000300) 等
    var root = document.documentElement.style;
    Object.keys(palette).forEach(function (k) { root.setProperty('--idx-' + k, palette[k]); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // defer 脚本在 DOMContentLoaded 之前按序执行，正常不会走到这里；保险起见等面板脚本注册完再启动
    setTimeout(init, 0);
  }

  window.AshareBoard = {
    registerPanel: registerPanel,
    exportPanel: exportPanel,
    loadPanel: loadPanel,
    palette: palette,
    fmt: fmt,
    DATA_BASE: DATA_BASE
  };
})();
