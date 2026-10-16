/*
 * 面板 loss —— 抄底数学题。纯表格，无图表（因此没有导出按钮）。
 * 数据：data/loss.json → data.rows[{price, drawdownPct}], data.cols[买入价], data.matrix[i][j], data.recovery[{dropPct, gainPct}]
 */
(function () {
  'use strict';

  function cell(tag, text, attrs) {
    var e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = text;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }

  function render(body, json, ctx) {
    var d = json.data;
    var fmt = ctx.fmt;

    // ---- 主表：现价 × 买入价 → 浮亏
    var table = document.createElement('table');
    table.className = 'data-table loss-table';
    table.appendChild(cell('caption', '单元格 =（现价 − 买入价）÷ 买入价，即这笔买入目前的浮亏；高点记为 100。'));
    var thead = document.createElement('thead');
    var hr = document.createElement('tr');
    hr.appendChild(cell('th', '现价 ＼ 买入价', { scope: 'col' }));
    d.cols.forEach(function (b) { hr.appendChild(cell('th', '买入 ' + b, { scope: 'col' })); });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    d.rows.forEach(function (row, i) {
      var tr = document.createElement('tr');
      tr.appendChild(cell('th', '现价 ' + row.price + '（高位回撤 ' + row.drawdownPct + '%）', { scope: 'row' }));
      d.matrix[i].forEach(function (v) {
        var td;
        if (v === null || v === undefined) {
          td = cell('td', '/', { 'aria-label': '已盈利，不适用' });
          td.className = 'na';
        } else {
          td = cell('td', fmt.pct(v * 100));
          var a = Math.abs(v);
          // 绿 = 亏（红涨绿跌），越亏越深
          td.style.backgroundColor = 'rgba(30,158,90,' + (0.08 + 0.72 * a).toFixed(3) + ')';
          if (a > 0.5) td.className = 'deep';
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var wrap = document.createElement('div');
    wrap.className = 'scroll-x';   // 7 列在窄屏下横向滚动，不撑破页面
    wrap.appendChild(table);
    body.appendChild(wrap);

    // ---- 附表：回本需要的涨幅
    var h3 = cell('h3', '回本需要的涨幅');
    body.appendChild(h3);
    var t2 = document.createElement('table');
    t2.className = 'data-table recovery-table';
    t2.appendChild(cell('caption', '需上涨 = 下跌幅度 ÷（100 − 下跌幅度）'));
    var th2 = document.createElement('thead');
    var hr2 = document.createElement('tr');
    hr2.appendChild(cell('th', '下跌', { scope: 'col' }));
    hr2.appendChild(cell('th', '回到原价需上涨', { scope: 'col' }));
    th2.appendChild(hr2);
    t2.appendChild(th2);
    var tb2 = document.createElement('tbody');
    d.recovery.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.appendChild(cell('th', fmt.pct(-r.dropPct, 0), { scope: 'row' }));
      var td = cell('td', '+' + fmt.pct(r.gainPct, 1));
      td.style.color = ctx.palette.up;
      tr.appendChild(td);
      tb2.appendChild(tr);
    });
    t2.appendChild(tb2);
    body.appendChild(t2);
    // 无图表 → 不返回 charts，导出按钮保持隐藏
  }

  window.AshareBoard.registerPanel({
    id: 'loss',
    title: '抄底数学题',
    subtitle: '跌去的幅度和回本需要的涨幅并不对称',
    dataFile: 'loss.json',
    bodyMinHeight: 380,
    notes: [
      '这是一张只讲算术的表：行是价格从高点 100 跌到的位置，列是你买入的位置，交叉处是这笔买入现在的浮亏。',
      '斜线「/」表示现价已经高于买入价，不在「亏损」的讨论范围内。',
      '附表换个角度看同一件事：跌掉一半要翻倍才回本，跌掉 80% 要涨 4 倍。'
    ],
    render: render
  });
})();
