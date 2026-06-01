/**
 * html-design-system.js — JS utility patterns for HTML docs
 *
 * This is a reference/template file. Read it and paste ONLY the
 * functions your document needs into the <script> tag. Do not import
 * it externally — HTML docs must be self-contained.
 *
 * Functions in this file:
 *   exportResult(btn, data, label?)  — copy button with Copied! feedback
 *   toggleTheme()                    — dark (default) ↔ light toggle
 *   initScrollSpy(selector?)         — sidebar active-link scroll spy
 *   showTabs(ids, active)            — tab switching (chip-styled buttons)
 *   initSortable(tableId, data, cols) — sortable data-table
 *   renderBarChart(id, data, opts?)  — animated SVG bar chart
 *   renderLineChart(id, data, opts?) — SVG line chart with draw-in animation
 *   initSearch(inputId, rowSelector) — live search/filter on a list or table
 *   State pattern                    — single state object for editing interfaces
 */

// ─── Export button ─────────────────────────────────────────────────────────
// HTML: <button class="export-btn" onclick="exportResult(this, state)">Copy as JSON</button>
// For markdown: pass a string directly as data.
function exportResult(btn, data, label = 'Copy as JSON') {
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  navigator.clipboard.writeText(text).catch(() => {});
  const orig = btn.textContent;
  btn.textContent = 'Copied!';
  btn.classList.add('copied');
  setTimeout(() => { btn.textContent = label || orig; btn.classList.remove('copied'); }, 1500);
}

// ─── Theme toggle ──────────────────────────────────────────────────────────
// HTML:
//   <div class="theme-toggle" onclick="toggleTheme()">
//     <div class="theme-toggle-track"></div>
//     <span class="theme-toggle-label" id="theme-label">Light</span>
//   </div>
// Default = dark (no attribute). Toggle adds/removes data-theme="light".
function toggleTheme() {
  const isLight = document.body.getAttribute('data-theme') === 'light';
  document.body.setAttribute('data-theme', isLight ? '' : 'light');
  const label = document.getElementById('theme-label');
  if (label) label.textContent = isLight ? 'Dark' : 'Light';
}

// ─── Scroll spy ────────────────────────────────────────────────────────────
// Call once after DOM ready. Sidebar links must have href="#section-id" and
// the matching elements must have the corresponding id attribute.
// selector defaults to '.sidebar a'
function initScrollSpy(selector = '.sidebar a') {
  const links = document.querySelectorAll(selector);
  const sections = [...links]
    .map(l => document.querySelector(l.getAttribute('href')))
    .filter(Boolean);

  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        links.forEach(l => l.classList.remove('active'));
        const link = document.querySelector(`${selector}[href="#${e.target.id}"]`);
        if (link) link.classList.add('active');
      }
    });
  }, { threshold: 0, rootMargin: '-25% 0px -65% 0px' });

  sections.forEach(s => obs.observe(s));
}

// ─── Task checkboxes ───────────────────────────────────────────────────────
// Wires checkboxes inside .task-check labels and persists state to localStorage.
// storageKey: unique string per page (e.g. 'plan-may11'). Defaults to location.pathname.
// HTML: <li><label class="task-check"><input type="checkbox"><span class="chip …">…</span></label> prose</li>
function initTaskCheckboxes(storageKey = location.pathname) {
  const key = 'task-checks:' + storageKey;
  const saved = JSON.parse(localStorage.getItem(key) || '{}');
  let idx = 0;
  document.querySelectorAll('.task-list li label.task-check').forEach(label => {
    const li = label.closest('li');
    // Wrap non-label child nodes in .task-text so CSS can strike through prose only
    const rest = [...li.childNodes].filter(n => n !== label);
    if (rest.length) {
      const span = document.createElement('span');
      span.className = 'task-text';
      rest.forEach(n => span.appendChild(n));
      li.appendChild(span);
    }
    const cb = label.querySelector('input[type=checkbox]');
    const i = idx++;
    if (saved[i]) { cb.checked = true; li.classList.add('task-done'); }
    cb.addEventListener('change', () => {
      li.classList.toggle('task-done', cb.checked);
      const state = {};
      let j = 0;
      document.querySelectorAll('.task-list li label.task-check input[type=checkbox]').forEach(c => { if (c.checked) state[j] = true; j++; });
      localStorage.setItem(key, JSON.stringify(state));
    });
  });
}

// ─── Tabs ──────────────────────────────────────────────────────────────────
// ids: array of tab id strings e.g. ['summary', 'detail', 'raw']
// active: the id to show
// Panels must have id="tab-<id>", buttons must have data-tab="<id>"
// HTML:
//   <div style="display:flex;gap:8px;margin-bottom:14px">
//     <button class="chip chip-active" data-tab="summary" onclick="showTabs(TABS,'summary')">Summary</button>
//     <button class="chip chip-pending" data-tab="detail" onclick="showTabs(TABS,'detail')">Detail</button>
//   </div>
//   <div id="tab-summary" class="panel">...</div>
//   <div id="tab-detail" class="panel" style="display:none">...</div>
function showTabs(ids, active) {
  ids.forEach(id => {
    const panel = document.getElementById('tab-' + id);
    const btn = document.querySelector(`[data-tab="${id}"]`);
    if (panel) panel.style.display = id === active ? '' : 'none';
    if (btn) btn.className = 'chip ' + (id === active ? 'chip-active' : 'chip-pending');
  });
}

// ─── Sortable table ────────────────────────────────────────────────────────
// tableId: id of the <table> element
// data: array of plain objects
// cols: array of { key, label, render? } where render(value, row) → HTML string
// HTML: <table id="my-table" class="data-table"></table>
function initSortable(tableId, data, cols) {
  let sortKey = null, sortDir = 1;
  const table = document.getElementById(tableId);
  if (!table) return;

  if (!table.querySelector('thead')) table.innerHTML = '<thead></thead><tbody></tbody>';

  table.querySelector('thead').innerHTML = `<tr>${cols.map(c =>
    `<th style="cursor:pointer;user-select:none" onclick="_sort_('${c.key}')">${c.label}</th>`
  ).join('')}</tr>`;

  function render() {
    const sorted = sortKey
      ? [...data].sort((a, b) => {
          const av = a[sortKey], bv = b[sortKey];
          return (av < bv ? -1 : av > bv ? 1 : 0) * sortDir;
        })
      : data;
    table.querySelector('tbody').innerHTML = sorted.map(row =>
      `<tr>${cols.map(c =>
        `<td>${c.render ? c.render(row[c.key], row) : (row[c.key] ?? '')}</td>`
      ).join('')}</tr>`
    ).join('');
  }

  window._sort_ = (key) => {
    sortDir = sortKey === key ? -sortDir : 1;
    sortKey = key;
    render();
  };

  render();
}

// ─── SVG bar chart ─────────────────────────────────────────────────────────
// id: container element id
// data: array of { label, value, color? }
// opts: { height?, max?, barColor? }
// The container's offsetWidth is used for width. Call after layout is done.
function renderBarChart(id, data, opts = {}) {
  const container = document.getElementById(id);
  if (!container) return;

  const W = container.offsetWidth || 400;
  const H = opts.height || 200;
  const PAD = { top: 24, right: 12, bottom: 36, left: 36 };
  const cW = W - PAD.left - PAD.right;
  const cH = H - PAD.top - PAD.bottom;
  const max = opts.max || Math.max(...data.map(d => d.value)) * 1.1 || 1;
  const slot = cW / data.length;
  const barW = Math.min(Math.floor(slot * 0.6), 48);

  const bars = data.map((d, i) => {
    const h = (d.value / max) * cH;
    const x = PAD.left + i * slot + (slot - barW) / 2;
    const y = PAD.top + cH - h;
    const color = d.color || opts.barColor || 'var(--clay)';
    const delay = i * 0.04;
    return `
      <rect x="${x}" y="${PAD.top + cH}" width="${barW}" height="0" rx="3" fill="${color}" opacity="0.85">
        <animate attributeName="height" to="${h}" dur="0.35s" begin="${delay}s" fill="freeze"/>
        <animate attributeName="y" to="${y}" dur="0.35s" begin="${delay}s" fill="freeze"/>
      </rect>
      <text x="${x + barW / 2}" y="${H - 8}" text-anchor="middle"
        font-family="var(--mono)" font-size="10" fill="var(--gray-500)">${d.label}</text>`;
  }).join('');

  // y-axis tick labels
  const ticks = [0, 0.5, 1].map(t => {
    const val = (max * t).toFixed(max < 2 ? 2 : 0);
    const y = PAD.top + cH - t * cH;
    return `<text x="${PAD.left - 6}" y="${y + 4}" text-anchor="end"
      font-family="var(--mono)" font-size="9" fill="var(--gray-500)">${val}</text>
      <line x1="${PAD.left}" y1="${y}" x2="${PAD.left + cW}" y2="${y}"
        stroke="var(--gray-100)" stroke-width="1"/>`;
  }).join('');

  container.innerHTML = `<svg width="${W}" height="${H}" style="overflow:visible;display:block">
    ${ticks}
    <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${PAD.top + cH}"
      stroke="var(--gray-300)" stroke-width="1"/>
    <line x1="${PAD.left}" y1="${PAD.top + cH}" x2="${PAD.left + cW}" y2="${PAD.top + cH}"
      stroke="var(--gray-300)" stroke-width="1"/>
    ${bars}
  </svg>`;
}

// ─── SVG line chart ────────────────────────────────────────────────────────
// id: container element id
// series: array of { label, values: number[], color? }
// labels: x-axis label strings (must match values.length)
// opts: { height?, yMin?, yMax? }
function renderLineChart(id, series, labels, opts = {}) {
  const container = document.getElementById(id);
  if (!container) return;

  const W = container.offsetWidth || 500;
  const H = opts.height || 180;
  const PAD = { top: 20, right: 20, bottom: 32, left: 40 };
  const cW = W - PAD.left - PAD.right;
  const cH = H - PAD.top - PAD.bottom;
  const allVals = series.flatMap(s => s.values);
  const yMin = opts.yMin ?? Math.min(...allVals) * 0.95;
  const yMax = opts.yMax ?? Math.max(...allVals) * 1.05;
  const n = labels.length;

  function px(xi, yi) {
    return {
      x: PAD.left + (xi / (n - 1)) * cW,
      y: PAD.top + cH - ((yi - yMin) / (yMax - yMin)) * cH,
    };
  }

  const lines = series.map(s => {
    const pts = s.values.map((v, i) => px(i, v));
    const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const len = pts.reduce((acc, p, i) => {
      if (i === 0) return 0;
      const pp = pts[i - 1];
      return acc + Math.hypot(p.x - pp.x, p.y - pp.y);
    }, 0);
    const color = s.color || 'var(--clay)';
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
      stroke-dasharray="${len}" stroke-dashoffset="${len}">
      <animate attributeName="stroke-dashoffset" from="${len}" to="0" dur="0.7s" fill="freeze"/>
    </path>
    ${pts.map(p => `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="${color}"/>`).join('')}`;
  }).join('');

  const xLabels = labels.map((l, i) => {
    const { x } = px(i, yMin);
    return `<text x="${x.toFixed(1)}" y="${H - 6}" text-anchor="middle"
      font-family="var(--mono)" font-size="9" fill="var(--gray-500)">${l}</text>`;
  }).join('');

  container.innerHTML = `<svg width="${W}" height="${H}" style="overflow:visible;display:block">
    <line x1="${PAD.left}" y1="${PAD.top}" x2="${PAD.left}" y2="${PAD.top + cH}"
      stroke="var(--gray-300)" stroke-width="1"/>
    <line x1="${PAD.left}" y1="${PAD.top + cH}" x2="${PAD.left + cW}" y2="${PAD.top + cH}"
      stroke="var(--gray-300)" stroke-width="1"/>
    ${xLabels}
    ${lines}
  </svg>`;
}

// ─── Stacked bar (share of total — geometric alternative to donut) ────────
// id: container element id
// segments: [{ label, value, color }]
// Renders one full-width horizontal bar split proportionally + a legend.
function renderStackedBar(id, segments) {
  const container = document.getElementById(id);
  if (!container) return;
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const cells = segments.map(s => {
    const pct = (s.value / total) * 100;
    return `<div class="stacked-bar-cell" style="flex-basis:${pct.toFixed(2)}%;background:${s.color}">
      <span class="stacked-bar-label">${pct >= 6 ? `${pct.toFixed(0)}%` : ''}</span>
    </div>`;
  }).join('');
  const legend = segments.map(s =>
    `<div class="donut-legend-item">
      <span class="dot" style="background:${s.color}"></span>
      <span class="donut-legend-name">${s.label}</span>
      <span class="donut-legend-val">${s.value.toLocaleString()} (${((s.value / total) * 100).toFixed(1)}%)</span>
    </div>`
  ).join('');
  container.innerHTML = `<div class="stacked-bar">${cells}</div>
    <div class="donut-legend mt-12">${legend}</div>`;
}

// ─── Donut chart ───────────────────────────────────────────────────────────
// id: container element id
// segments: [{ label, value, color }]
function renderDonut(id, segments) {
  const container = document.getElementById(id);
  if (!container) return;
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const R = 60, r = 36, cx = 80, cy = 80;
  let acc = 0;
  const arcs = segments.map(s => {
    const start = (acc / total) * Math.PI * 2 - Math.PI / 2;
    acc += s.value;
    const end = (acc / total) * Math.PI * 2 - Math.PI / 2;
    const large = s.value / total > 0.5 ? 1 : 0;
    const x1 = cx + R * Math.cos(start), y1 = cy + R * Math.sin(start);
    const x2 = cx + R * Math.cos(end),   y2 = cy + R * Math.sin(end);
    const x3 = cx + r * Math.cos(end),   y3 = cy + r * Math.sin(end);
    const x4 = cx + r * Math.cos(start), y4 = cy + r * Math.sin(start);
    const d = `M${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} L${x3},${y3} A${r},${r} 0 ${large} 0 ${x4},${y4} Z`;
    return `<path d="${d}" fill="${s.color}" opacity="0.9"><title>${s.label}: ${s.value}</title></path>`;
  }).join('');
  const svg = `<svg class="donut-svg" width="160" height="160" viewBox="0 0 160 160">${arcs}
    <text x="80" y="78" text-anchor="middle" font-family="var(--serif)" font-size="22" fill="var(--slate)">${total.toLocaleString()}</text>
    <text x="80" y="94" text-anchor="middle" font-family="var(--mono)" font-size="9" fill="var(--gray-500)">total</text>
  </svg>`;
  const legend = segments.map(s =>
    `<div class="donut-legend-item">
      <span class="dot" style="background:${s.color}"></span>
      <span class="donut-legend-name">${s.label}</span>
      <span class="donut-legend-val">${s.value.toLocaleString()} (${((s.value / total) * 100).toFixed(1)}%)</span>
    </div>`
  ).join('');
  container.innerHTML = `<div class="donut-row">${svg}<div class="donut-legend">${legend}</div></div>`;
}

// ─── Sparkline row ─────────────────────────────────────────────────────────
// Returns HTML for one row (label, sparkline, current, delta). Caller appends.
function renderSparkRow(label, values, color, current, delta) {
  const W = 120, H = 32;
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (W - 4) + 2;
    const y = H - 4 - ((v - min) / span) * (H - 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const lastX = W - 2;
  const lastY = H - 4 - ((values[values.length - 1] - min) / span) * (H - 8);
  const deltaCls = delta >= 0 ? 'delta-up' : 'delta-down';
  const deltaSym = delta >= 0 ? '▲' : '▼';
  return `<div class="spark-row">
    <div class="spark-row-label">${label}</div>
    <svg class="spark-row-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${lastX}" cy="${lastY}" r="2.5" fill="${color}"/>
    </svg>
    <div class="spark-row-current">${current}</div>
    <div class="delta ${deltaCls} spark-row-current">${deltaSym} ${Math.abs(delta).toFixed(1)} pp</div>
  </div>`;
}

// ─── Inline sparkline (bare SVG, for table cells) ──────────────────────────
// Returns just an <svg> string — no label, value, or delta. For dense in-cell
// trends. Handles 0/1-point series gracefully (empty / single dot).
function sparklineSVG(values, color = 'var(--clay)', opts = {}) {
  const W = opts.width || 54, H = opts.height || 18;
  const vals = (values || []).map(v => +v || 0);
  if (vals.length === 0) return '';
  if (vals.length === 1) {
    return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
      `<circle cx="${(W - 2).toFixed(1)}" cy="${(H / 2).toFixed(1)}" r="2" fill="${color}"/></svg>`;
  }
  const max = Math.max(...vals), min = Math.min(...vals);
  const span = max - min || 1;
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * (W - 4) + 2;
    const y = H - 3 - ((v - min) / span) * (H - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const [lx, ly] = pts[pts.length - 1].split(',');
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
    `<polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<circle cx="${lx}" cy="${ly}" r="2" fill="${color}"/></svg>`;
}

// ─── Flowchart diagram (clickable nodes + annotation panel) ────────────────
// svgId: id of an empty <svg class="diagram-svg" viewBox="0 0 W H"> element
// annId: id of an adjacent <div class="diagram-ann"> element
// nodes: [{ id, x, y, w, h, label, sub?, c, ann?, circle? }]
// edges: [{ from, to, c, dash? }]
//
// Layout is consumer-controlled — pass coords in viewBox units. Each node with
// `ann` becomes clickable; the annotation panel toggles visible.
function renderFlowchart(svgId, annId, nodes, edges) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const annEl = document.getElementById(annId);
  const nMap = Object.fromEntries(nodes.map(n => [n.id, n]));

  function ry(n) { return n.y + n.h / 2; }
  function rxRight(n) { return n.x + n.w; }
  function rxLeft(n)  { return n.x; }

  const cnameOf = c =>
    c === 'var(--clay)'  ? 'clay'  :
    c === 'var(--olive)' ? 'olive' :
    c === 'var(--sky)'   ? 'sky'   :
    c === 'var(--rust)'  ? 'rust'  : '';

  let h = `<defs>
    <marker id="${svgId}-ah" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="var(--gray-500)"/></marker>
    <marker id="${svgId}-ah-clay"  markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="var(--clay)"/></marker>
    <marker id="${svgId}-ah-olive" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="var(--olive)"/></marker>
    <marker id="${svgId}-ah-sky"   markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="var(--sky)"/></marker>
    <marker id="${svgId}-ah-rust"  markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="var(--rust)"/></marker>
  </defs>`;

  edges.forEach(e => {
    const a = nMap[e.from], b = nMap[e.to];
    if (!a || !b) return;
    const x1 = rxRight(a), y1 = ry(a);
    const x2 = rxLeft(b),  y2 = ry(b);
    const mid = (x1 + x2) / 2;
    const cname = cnameOf(e.c);
    const mref = cname ? `url(#${svgId}-ah-${cname})` : `url(#${svgId}-ah)`;
    const dash = e.dash ? `stroke-dasharray="4,3"` : '';
    h += `<path d="M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}" fill="none" stroke="${e.c}" stroke-width="1.5" ${dash} marker-end="${mref}"/>`;
  });

  nodes.forEach(n => {
    const hasAnn = !!n.ann;
    const cur = hasAnn ? 'cursor:pointer' : '';
    const onclick = hasAnn ? `onclick="_flowAnn_('${annId}', '${n.label.replace(/'/g, "\\'")}', '${(n.ann || '').replace(/'/g, "\\'")}')"` : '';
    if (n.circle) {
      h += `<circle cx="${n.x + n.w / 2}" cy="${n.y + n.h / 2}" r="${n.w / 2}" fill="${n.c}" stroke="var(--gray-300)" stroke-width="1.5" style="${cur}" ${onclick}/>
        <text x="${n.x + n.w / 2}" y="${n.y + n.h / 2 + 4}" text-anchor="middle" font-family="var(--mono)" font-size="11" fill="var(--slate)" pointer-events="none">${n.label}</text>`;
    } else {
      h += `<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="8" fill="var(--white)" stroke="${n.c}" stroke-width="1.5" style="${cur}" ${onclick}/>
        <rect x="${n.x}" y="${n.y}" width="${n.w}" height="6" rx="5" fill="${n.c}" opacity="0.85" pointer-events="none"/>
        <text x="${n.x + n.w / 2}" y="${n.y + 24}" text-anchor="middle" font-family="var(--sans)" font-size="12" font-weight="600" fill="var(--slate)" pointer-events="none">${n.label}</text>`;
      if (n.sub) h += `<text x="${n.x + n.w / 2}" y="${n.y + 38}" text-anchor="middle" font-family="var(--mono)" font-size="10" fill="var(--gray-500)" pointer-events="none">${n.sub}</text>`;
    }
  });

  svg.innerHTML = h;
  if (annEl) annEl.classList.remove('visible');
}

window._flowAnn_ = function (annId, title, body) {
  const box = document.getElementById(annId);
  if (!box) return;
  box.innerHTML = `<strong>${title}</strong> &mdash; ${body}`;
  box.classList.add('visible');
};

// ─── Live search / filter ──────────────────────────────────────────────────
// inputId: id of <input type="search"> element
// rowSelector: CSS selector for each filterable row/card (e.g. '.result-row', 'tbody tr')
// Searches textContent of each row case-insensitively.
function initSearch(inputId, rowSelector) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase().trim();
    document.querySelectorAll(rowSelector).forEach(row => {
      row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? '' : 'none';
    });
  });
}

// ─── State + render pattern (editing interfaces) ───────────────────────────
// Copy this pattern into editing interface documents.
// All controls write to state; render() reads from state.
//
// const state = {
//   label: '',
//   rating: 3,
//   tags: [],
// };
//
// function render() {
//   document.getElementById('preview').innerHTML = `<pre>${JSON.stringify(state, null, 2)}</pre>`;
// }
//
// document.getElementById('label-input').oninput = e => { state.label = e.target.value; render(); };
// document.getElementById('rating-input').oninput = e => { state.rating = +e.target.value; render(); };
//
// render(); // initial draw
//
// // Export button:
// // <button class="export-btn" onclick="exportResult(this, state)">Copy as JSON</button>
