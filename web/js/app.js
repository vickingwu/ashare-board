/*
 * A股看板 —— 前端框架。
 *
 * 职责：面板注册表、导航、懒加载 data/<id>.json、数据新鲜度条、深链接、窗口缩放、PNG 导出。
 * 面板模块只需调用 AshareBoard.registerPanel({...render(body, json, ctx)}) 即可接入。
 * 深链接可带查询串：#panel-<id>?k=v&k2=v2 —— 面板在 render 里用 ctx.hashQuery() 读取，用 ctx.setHashQuery({...}) 写回；
 * render 还可返回 exportMeta()（导出 PNG 的副标题 / 来源 / 截至日）与 onHashQuery(q)（hashchange 时收到新的查询串）。
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
    breadth250: '#1D4ED8', // 趋势温度：站上 250 日线占比
    breadth50: '#60A5FA',  // 趋势温度：站上 50 日线占比
    macdPos: '#0F766E',    // 趋势温度：行业 MACD DIF>0 占比
    macdHist: '#94A3B8',   // 趋势温度：行业 MACD 柱>0 占比（对照）
    fgLine: '#7C2D12',     // 恐贪指数：综合分折线
    fgBands: ['#2B6CB0', '#90C2E7', '#CBD5E1', '#F4A261', '#C53030'],  // 恐贪指数五段底色：极度恐慌 → 极度贪婪
    m2: '#B45309',         // 不同事物的增长：M2 赭
    gdp: '#0E7490',        // 不同事物的增长：名义 GDP 深青
    mv: '#6D28D9',         // 不同事物的增长：A 股流通市值 紫
    house70: '#9D174D',    // 不同事物的增长：70 城二手住宅 酒红
    houseTier1: '#F472B6', // 不同事物的增长：一线 4 城二手住宅 粉
    gold: '#CA8A04',       // 不同事物的增长：黄金 金黄（中证全指全收益 / 红利低波全收益 复用 000985 / H20269）
    factDividend: '#B91C1C', // 风格因子：红利
    factLowVol: '#2563EB',   // 风格因子：低波动
    factQuality: '#047857',  // 风格因子：质量
    factMomentum: '#D97706', // 风格因子：动量
    factValue: '#7C3AED',    // 风格因子：价值
    factGrowth: '#DB2777',   // 风格因子：成长
    fundEquity: '#C2410C',   // 基金指数：偏股基金
    fundBondPlus: '#0369A1', // 基金指数：非纯债债基（近似偏债）
    fundStock: '#15803D',    // 基金指数：股票基金
    fundMixed: '#A21CAF',    // 基金指数：混合基金
    fundBond: '#64748B',     // 基金指数：债券基金（沪深300全收益 / 中证A500 复用 000300 / 000510）
    marginTotal: '#9333EA',  // 投资者结构：沪市融资融券余额
    marginFin: '#C084FC',    // 投资者结构：沪市融资余额
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

  // ---------------------------------------------------------------- 深链接的 hash 解析
  // '#panel-stock?s=sh600519&r=10y' → {id: 'stock', query: {s: 'sh600519', r: '10y'}}；'#panel-growth' → {id: 'growth', query: null}；其它 → null
  function parseHash(hash) {
    var m = /^#panel-([\w-]+)(?:\?([^#]*))?$/.exec(hash || '');
    if (!m) return null;
    var query = null;
    if (m[2]) {
      query = {};
      var params = new URLSearchParams(m[2]);
      params.forEach(function (v, k) { query[k] = v; });
    }
    return { id: m[1], query: query };
  }

  // 当前 hash 指向面板 id 时返回它的查询对象（无查询串时 null），否则 null
  function hashQuery(id) {
    var h = parseHash(location.hash);
    return h && h.id === id ? h.query : null;
  }

  // 把面板状态写回地址栏（replaceState，不滚动、不产生历史记录）；值为 null / undefined 的参数省略
  function setHashQuery(id, params) {
    var qs = new URLSearchParams();
    Object.keys(params || {}).forEach(function (k) {
      if (params[k] !== null && params[k] !== undefined) qs.set(k, String(params[k]));
    });
    var s = qs.toString();
    try {
      history.replaceState(null, '', '#panel-' + id + (s ? '?' + s : ''));
    } catch (e) { /* file:// 等场景下 replaceState 可能抛错，忽略 */ }
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
      var out = p.render(p.bodyEl, json, {
        echarts: window.echarts, palette: palette, fmt: fmt,
        hashQuery: function () { return hashQuery(p.id); },
        setHashQuery: function (params) { setHashQuery(p.id, params); },
        setAsOf: function (text) { p.asofEl.textContent = text; }
      });
      p.charts = (out && out.charts) ? out.charts.filter(Boolean) : [];
      p.exportMeta = out && typeof out.exportMeta === 'function' ? out.exportMeta : null;
      p.onHashQuery = out && typeof out.onHashQuery === 'function' ? out.onHashQuery : null;
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
    var generated = manifest && manifest.generated ? String(manifest.generated).replace('T', ' ').slice(0, 16) : null;
    box.textContent = '数据更新至 ' + (asOf || '未知') + ' · ' + (n === null ? '?' : n) + ' 个交易日前（按工作日估算，未扣节假日）' +
      (generated ? ' · 生成于 ' + generated : '');

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
        box.textContent = '\u26A0 无法读取 data/manifest.json（' + (err && err.message ? err.message : err) + '）';
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

  // opts.pixelRatio：导出倍率（默认 3；汇总页用 2）。面板 render 返回的 exportMeta() 可覆盖副标题 / 来源 / 截至日
  // （个股面板的数据不来自 data/*.json，页脚要写腾讯行情与最后一根 K 线的日期）；exportMeta 抛错即导出失败（如尚未查询）。
  function exportPanel(id, opts) {
    var p = byId[id];
    if (!p) return Promise.reject(new Error('未注册的面板: ' + id));
    var ratio = (opts && opts.pixelRatio) || 3;
    var meta = {};
    return loadPanel(id).then(function () {
      if (!p.charts.length) throw new Error('该面板没有可导出的图表');
      meta = p.exportMeta ? (p.exportMeta() || {}) : {};
      var urls = p.charts.map(function (c) {
        return c.getDataURL({ type: 'png', pixelRatio: ratio, backgroundColor: '#ffffff' });
      });
      return Promise.all(urls.map(loadImage));
    }).then(function (imgs) {
      var S = ratio;
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
      ctx.fillText(meta.subtitle || p.subtitle, 24 * S, 54 * S, width - 48 * S);
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
      var foot = '数据来源：' + ((meta.sources || json.sources || []).join(' · ') || '—') + ' · 数据截至 ' + fmt.date(meta.asOf || json.asOf) +
        ' · 导出于 ' + todayStr;
      ctx.fillText(foot, 24 * S, height - footerH + 18 * S, width - 48 * S);
      return canvas.toDataURL('image/png');
    });
  }

  function downloadPanel(id, btn) {
    var p = byId[id];
    if (btn) btn.disabled = true;
    exportPanel(id).then(function (dataUrl) {
      var asOf = p.json && p.json.asOf;
      try { var m = p.exportMeta ? p.exportMeta() : null; if (m && m.asOf) asOf = m.asOf; } catch (e) { /* 文件名退回 json.asOf */ }
      var a = document.createElement('a');
      a.href = dataUrl;
      a.download = id + '_' + fmt.date(asOf) + '.png';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }).catch(function (err) {
      window.alert('导出失败：' + (err && err.message ? err.message : err));
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  // ---------------------------------------------------------------- 顶栏高度
  // 顶栏是 sticky 的，锚点 / 深链接靠 html 的 scroll-padding-top: calc(var(--header-h) + 8px) 避开它。样式表里的 --header-h
  // 是单行导航时的高度（64px，窄屏 84px）；面板增多后导航在 1200px 宽度内折成两行（窄屏更多行），顶栏实际更高，
  // 不刷新这个变量的话定位后面板标题会被顶栏盖住。这里按实际高度写回变量，并在顶栏尺寸变化（缩放、换行数变化）时重算。
  function syncHeaderHeight() {
    var header = document.querySelector('.site-header');
    if (!header) return;
    var h = Math.ceil(header.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty('--header-h', h + 'px');
  }

  function watchHeaderHeight() {
    var header = document.querySelector('.site-header');
    if (!header) return;
    if ('ResizeObserver' in window) {
      new ResizeObserver(syncHeaderHeight).observe(header);
    } else {
      window.addEventListener('resize', debounce(syncHeaderHeight, 100));
    }
  }

  // ---------------------------------------------------------------- 启动
  function init() {
    buildShell();
    syncHeaderHeight();
    watchHeaderHeight();
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
        // 只管 #nav 里生成的面板链接；顶栏的页面链接（看板 ⇄ 汇总）是静态的，不参与高亮切换
        document.querySelectorAll('#nav .nav-link').forEach(function (a) {
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
    var h0 = parseHash(location.hash);
    if (h0 && byId[h0.id]) {
      var targetId = h0.id;
      if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
      var idx = registry.findIndex(function (p) { return p.id === targetId; });
      var above = registry.slice(0, idx + 1).map(function (p) { return loadPanel(p.id); });
      Promise.all(above).then(function () {
        requestAnimationFrame(function () {
          syncHeaderHeight();
          byId[targetId].el.scrollIntoView({ block: 'start', behavior: 'instant' });
          setTimeout(enableSmooth, 150);
        });
      });
    } else {
      enableSmooth();
    }

    // 页面已打开时地址栏换成带查询串的深链接（如粘贴 #panel-stock?s=sh600519）：把查询串交给该面板；
    // 不带查询串的 hash 仍由浏览器原生锚点定位，其它面板没有 onHashQuery，不受影响
    window.addEventListener('hashchange', function () {
      var h = parseHash(location.hash);
      if (h && byId[h.id] && h.query) {
        loadPanel(h.id).then(function (pp) { if (pp.onHashQuery) pp.onHashQuery(h.query); });
      }
    });

    window.addEventListener('resize', debounce(function () {
      registry.forEach(function (p) {
        p.charts.forEach(function (c) { if (c && typeof c.resize === 'function') c.resize(); });
      });
    }, 200));

    // 把配色写进 CSS 变量，样式表可直接使用 var(--idx-000300) 等
    var root = document.documentElement.style;
    Object.keys(palette).forEach(function (k) {
      if (typeof palette[k] === 'string') root.setProperty('--idx-' + k, palette[k]);   // 数组值（fgBands）不写
    });
  }

  // defer 脚本在文档解析完成（readyState 已变为 interactive）之后、DOMContentLoaded 之前按序执行，
  // 所以必须等 DOMContentLoaded 再启动：setTimeout(0) 可能抢在尚未下载完的面板脚本之前运行，导致注册表为空、页面没有面板。
  if (document.readyState !== 'complete') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    setTimeout(init, 0);   // 脚本在页面加载完成后才被插入时的兜底
  }

  window.AshareBoard = {
    registerPanel: registerPanel,
    exportPanel: exportPanel,
    loadPanel: loadPanel,
    panels: function () { return registry.map(function (p) { return { id: p.id, title: p.title, subtitle: p.subtitle }; }); },
    parseHash: parseHash,
    hashQuery: hashQuery,
    setHashQuery: setHashQuery,
    palette: palette,
    fmt: fmt,
    DATA_BASE: DATA_BASE
  };
})();
