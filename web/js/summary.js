/*
 * A股看板 —— 汇总页（summary.html）。
 * app.js 在离屏渲染台（.summary-stage，960px 宽）里生成全部已注册面板的骨架；这里按注册顺序逐个 loadPanel → exportPanel
 * （与「导出 PNG」同一条流程，倍率 2），把得到的 PNG 放进缩略图网格；点缩略图回到 index.html 的对应面板；
 * 「全部导出」把已生成的 PNG 逐张下载（间隔 300 ms，不拼长图）。进度与总耗时写在 #summary-progress，
 * 结果同时挂在 window.AshareSummary = {results, total, done} 供检查。
 * 页面不含 stock.js / loss.js，所以这里只会看到 14 个面板；SKIP 是兜底。
 */
(function () {
  'use strict';

  var SKIP = { stock: 1, loss: 1 };
  var RATIO = 2;
  var DOWNLOAD_GAP_MS = 300;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function run() {
    var board = window.AshareBoard;
    var grid = document.getElementById('summary-grid');
    var progress = document.getElementById('summary-progress');
    var exportAll = document.getElementById('summary-export-all');
    var panels = board.panels().filter(function (p) { return !SKIP[p.id]; });
    var total = panels.length;
    var results = [];
    var t0 = performance.now();
    var done = false;

    function setProgress(text) { progress.textContent = text; }

    function one(p) {
      var tp = performance.now();
      var fig = el('figure', 'thumb');
      fig.setAttribute('data-panel', p.id);
      grid.appendChild(fig);
      return board.loadPanel(p.id).then(function (pp) {
        if (pp.bodyEl && pp.bodyEl.querySelector('.panel-error')) throw new Error(pp.bodyEl.querySelector('.panel-error').textContent);
        return board.exportPanel(p.id, { pixelRatio: RATIO }).then(function (url) {
          var asOf = pp.json && pp.json.asOf ? board.fmt.date(pp.json.asOf) : '–';
          var a = el('a');
          a.href = 'index.html#panel-' + p.id;
          a.title = '打开「' + p.title + '」';
          var img = new Image();
          img.alt = p.title;
          img.loading = 'lazy';
          img.src = url;
          a.appendChild(img);
          var cap = el('figcaption');
          cap.appendChild(el('strong', null, p.title));
          cap.appendChild(document.createTextNode(' · 数据截至 ' + asOf));
          fig.appendChild(a);
          fig.appendChild(cap);
          results.push({ id: p.id, title: p.title, asOf: asOf, bytes: url.length, ms: Math.round(performance.now() - tp), ok: true, url: url });
        });
      }).catch(function (err) {
        fig.classList.add('is-error');
        fig.appendChild(el('figcaption', null, p.title + '：生成失败（' + (err && err.message ? err.message : err) + '）'));
        results.push({ id: p.id, title: p.title, asOf: null, bytes: 0, ms: Math.round(performance.now() - tp), ok: false, error: String(err && err.message || err) });
      }).then(function () {
        setProgress('已生成 ' + results.length + ' / ' + total + ' …');
      });
    }

    var chain = Promise.resolve();
    panels.forEach(function (p) { chain = chain.then(function () { return one(p); }); });
    var finished = chain.then(function () {
      done = true;
      var seconds = (performance.now() - t0) / 1000;
      var ok = results.filter(function (r) { return r.ok; });
      var maxAsOf = ok.reduce(function (m, r) { return r.asOf && r.asOf > m ? r.asOf : m; }, '');
      setProgress(ok.length + ' 张缩略图' + (ok.length < total ? '（' + (total - ok.length) + ' 张失败）' : '') + ' · 共 ' + seconds.toFixed(1) + ' 秒' + (maxAsOf ? ' · 数据截至 ' + maxAsOf : ''));
      exportAll.disabled = ok.length === 0;
      window.AshareSummary.total = { count: ok.length, attempted: total, seconds: +seconds.toFixed(2), maxAsOf: maxAsOf || null };
      return window.AshareSummary.total;
    });

    exportAll.addEventListener('click', function () {
      if (!done) return;
      exportAll.disabled = true;
      var ok = results.filter(function (r) { return r.ok; });
      ok.forEach(function (r, i) {
        setTimeout(function () {
          var a = document.createElement('a');
          a.href = r.url;
          a.download = r.id + '_' + (r.asOf || 'latest') + '.png';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          if (i === ok.length - 1) exportAll.disabled = false;
        }, i * DOWNLOAD_GAP_MS);
      });
    });

    window.AshareSummary = { results: results, total: null, done: finished, ratio: RATIO };
  }

  // app.js 在 DOMContentLoaded 里 buildShell；排到它之后再开始（defer 脚本总在 DOMContentLoaded 之前执行；complete 只是兜底）
  if (document.readyState !== 'complete') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(run, 0); }, { once: true });
  } else {
    setTimeout(run, 0);
  }
})();
