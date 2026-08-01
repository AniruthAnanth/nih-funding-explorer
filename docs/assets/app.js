/* Data explorer for the NIH funding rankings.

   No framework and no build step: the page is a table tool, and the whole of it
   is a fetch, a filter, a sort and a render. Column-oriented payloads are
   unpacked once into row objects keyed by column name.

   One rule overrides every other decision in this file. NIH does not
   department-code independent hospitals, so the Mass General Brigham
   departmental figure is reconstructed from dated PubMed author affiliations
   and is a LOWER BOUND. Every path that can put such a row on screen — table,
   chart, export, clipboard — marks it. See isRecon() and its call sites. */

'use strict';

// Roll-up ids whose departmental figure is publication-derived rather than
// read from an NIH department code.
const RECON_IDS = { MGH: 1, BWH: 1, MGB_CORE: 1, MGB_SYSTEM: 1 };
const NON_DEPT = new Set(['UNKNOWN', 'UNCLASSIFIED']);
/* A roll-up and its members are the same dollars counted twice, so exactly one
   of the two readings is ever on screen. Mass General Brigham is Massachusetts
   General plus the Brigham; combined is the default because that is the entity
   people ask about, and split is one control away.

   The members used to be deleted from the payload outright. That left no way
   to see that the headline department-of-surgery figure is two hospitals'
   departments added together — and MGB leads on citations only as a merged
   figure, neither hospital leading alone — so the reading that carries the
   caveat was also the only reading available. */
const MGB_ROLLUPS = new Set(['MGB_CORE']);
const MGB_MEMBERS = new Set(['MGH', 'BWH']);
// The wider system roll-up is a third entity again, and is not published.
const NEVER_SHOWN = new Set(['MGB_SYSTEM']);
const mgbHidden = (r, split) => {
  const id = r.canonical_org_id;
  if (NEVER_SHOWN.has(id)) return true;
  return split ? MGB_ROLLUPS.has(id) : MGB_MEMBERS.has(id);
};
const surgSplit = () => {
  const n = document.getElementById('s-split');
  return !!n && n.value === 'split';
};
// Surgery-tab view: honours that tab's own control.
const surgView = rows => rows.filter(r => !mgbHidden(r, surgSplit()));

/* One rule ships, so there is one confidence floor. The control that used to
   switch between two is gone: both settings produced byte-identical files, and
   the second carried the validation statistics of a rule it did not implement. */
const SURG_FLOOR = 'corroborated';
const BAR_DEFAULT = '#9ba7b4';
const LOWER_BOUND_NOTE =
  'Reconstructed from dated PubMed author affiliations. A lower bound, not '
  + 'like-for-like with an NIH department-coded row. The rule was validated on one '
  + 'binary call only \u2014 surgical or not, Cohen\u2019s \u03ba 0.916 \u2014 so a '
  + 'reconstructed row in a non-surgical department rests on an extension of the rule '
  + 'that has not been measured. Treat surgical rows as validated and the rest as indicative.';

/* A roll-up row is held out of the ordinary rank because it aggregates rows
   already in the table. Where it is drawn at its as-a-single-institution
   position instead, that position is marked everywhere it is printed. */
const AS_SINGLE_MARK = '§';
const AS_SINGLE_NOTE =
  'A roll-up such as Mass General Brigham is held out of the ordinary rank because it aggregates '
  + 'rows already in the table, and counting it would push every real institution down a place. '
  + 'Its hollow markers sit instead at the position it would take as a single institution '
  + 'inserted into that ranking, counted against the non-roll-up population only. The two are '
  + 'different measurements; the tooltip and the ' + AS_SINGLE_MARK + ' marker in the table say '
  + 'which one is on screen. A dashed line is a separate fact: a figure reconstructed from '
  + 'publication affiliations, and a lower bound.';
const asifMark = () =>
  `<sup class="asif" title="${esc(AS_SINGLE_NOTE)}">${AS_SINGLE_MARK}</sup>`;

/* A row is reconstructed if the pipeline says so, or if it is one of the MGB
   entities, which are never NIH department-coded. Both tests are kept: the
   flag can be absent from a grain, the org id never is. */
const isRecon = r =>
  !!(r && (r.is_reconstructed === 1 || RECON_IDS[r.canonical_org_id]));

/* Metrics that divide out department size. Ranking by raw dollars mostly ranks
   departments by how big they are, so these are offered alongside. */
const INTENSITY = new Set([
  'funding_per_investigator', 'r01_funding_per_investigator',
  'funding_per_project', 'mean_award_size', 'projects_per_investigator',
  'r01_share_of_funding',
]);
const PCT_METRICS = new Set(['r01_share_of_funding']);
const RATIO_METRICS = new Set(['projects_per_investigator']);

const MECH_KEYS = [
  ['funding_R01', 'R01'], ['funding_R_OTHER', 'Other R'], ['funding_U', 'U'],
  ['funding_P', 'P'], ['funding_K', 'K'], ['funding_T', 'T'],
  ['funding_F', 'F'], ['funding_OTHER', 'Other'],
];

const state = {
  core: null, pairs: {}, sort: {}, expanded: {}, view: {},
  trend: {}, trendReq: {}, trendIx: {},
};

/* The explorer's whole interface state. Everything here round-trips through
   the location hash, so a view is a URL. */
const EX = {
  grain: 'pairs',
  period: 'FY2021_FY2025',
  metric: 'total_funding',
  country: 'UNITED STATES',
  q: '',
  floor: 5,
  dept: new Set(),
  spec: new Set(),
  inst: new Set(),
  mech: new Set(),
  mechMode: 'any',
  recon: 'all',
  hideRollup: false,
  // 'combined' shows the roll-up, 'split' shows its member hospitals. Never both.
  mgb: 'combined',
  uncoded: false,
  ranges: [],
  page: 1,
  pageSize: 100,
  cols: null,
  topn: 20,
  scale: 'lin',
};

/* The trend view's state, kept apart from the explorer's because the two share
   no filter and answer different questions. It round-trips through the hash on
   the same terms. */
const TR = {
  grain: 'inst',
  metric: 'total_funding',
  axis: 'rank',
  pool: 250,
  n: 10,
  sel: new Set(),
  dept: new Set(),
};

const colorFor = id => (state.core && state.core.colors && state.core.colors[id]) || BAR_DEFAULT;

/* --- formatting --------------------------------------------------------- */

const money = n => {
  if (n == null) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return '$' + Math.round(n / 1e3) + 'K';
  return '$' + n;
};
const int = n => (n == null ? '—' : Number(n).toLocaleString('en-US'));
const pct = n => (n == null ? '—' : Number(n).toFixed(1) + '%');
const ratio = n => (n == null ? '—' : Number(n).toFixed(2));
const fmtFor = k =>
  PCT_METRICS.has(k) ? 'pct' : RATIO_METRICS.has(k) ? 'ratio'
    : (k.includes('funding') || k.includes('award_size')) ? 'money' : 'int';
const fmtFn = f => (f === 'money' ? money : f === 'pct' ? pct : f === 'ratio' ? ratio : int);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* "1.5M", "250k", "2B", "44.8" all mean something to someone typing a bound
   into a funding filter. Accept them all rather than demanding 1500000. */
function parseNum(s) {
  s = String(s == null ? '' : s).trim().replace(/[$,\s]/g, '');
  if (!s) return null;
  const m = /^(-?\d*\.?\d+)([kmb])?%?$/i.exec(s);
  if (!m) return NaN;
  const mul = { k: 1e3, m: 1e6, b: 1e9 }[(m[2] || '').toLowerCase()] || 1;
  return parseFloat(m[1]) * mul;
}

const unpack = t => {
  if (!t) return [];
  const { cols, rows } = t;
  return rows.map(r => {
    const o = {};
    cols.forEach((c, i) => { o[c] = r[i]; });
    return o;
  });
};

function toast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('on'), 2200);
}

/* --- generic sortable table -------------------------------------------- */

/* Multi-key sort. Shift-click appends a key; the header shows the precedence
   number so a three-deep sort is readable rather than mysterious. */
function toggleSort(mountId, key, additive) {
  const cur = (state.sort[mountId] || []).slice();
  const i = cur.findIndex(x => x.key === key);
  if (additive) {
    if (i === -1) cur.push({ key, dir: 'desc' });
    else if (cur[i].dir === 'desc') cur[i] = { key, dir: 'asc' };
    else cur.splice(i, 1);
    state.sort[mountId] = cur;
  } else if (i === 0 && cur.length === 1) {
    state.sort[mountId] = [{ key, dir: cur[0].dir === 'desc' ? 'asc' : 'desc' }];
  } else {
    state.sort[mountId] = [{ key, dir: 'desc' }];
  }
}

function cmpBy(a, b, s) {
  const x = a[s.key], y = b[s.key];
  if (x == null && y == null) return 0;
  if (x == null) return 1;          // nulls sink, whichever direction
  if (y == null) return -1;
  const sign = s.dir === 'asc' ? 1 : -1;
  if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign;
  return String(x).localeCompare(String(y)) * sign;
}

function applySort(rows, mountId, fallbackKey) {
  let keys = state.sort[mountId];
  if (!keys || !keys.length) keys = [{ key: fallbackKey, dir: 'desc' }];
  return rows.slice().sort((a, b) => {
    for (let i = 0; i < keys.length; i++) {
      const c = cmpBy(a, b, keys[i]);
      if (c) return c;
    }
    return 0;
  });
}

function skeleton(mountId, n) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  mount.className = 'skel';
  let s = '';
  for (let i = 0; i < (n || 10); i++) {
    s += `<div class="skel-row"><i style="width:${18 + (i * 7) % 26}%"></i>`
      + `<i style="width:${9 + (i * 5) % 14}%"></i><i style="width:8%"></i><i style="width:7%"></i></div>`;
  }
  mount.innerHTML = `<div class="skel-head"></div>${s}`;
}

function cellHTML(c, r, opts, flag, tint, max) {
  if (c.key === '__bar') {
    const v = Number(r[opts.barKey]) || 0;
    const w = max > 0 ? Math.max((v / max) * 100, 0.6) : 0;
    return `<td class="barcell"><span class="bartrack"><span class="bar${flag ? ' hatched' : ''}" `
      + `style="width:${w.toFixed(2)}%;background-color:${tint}"></span></span></td>`;
  }
  const v = r[c.key];
  const st = c.sticky ? ` sticky" style="left:${c.stickyLeft}px` : '';
  if (c.fmt === 'money') return `<td class="num">${money(v)}</td>`;
  if (c.fmt === 'pct') return `<td class="num">${pct(v)}</td>`;
  if (c.fmt === 'ratio') return `<td class="num">${ratio(v)}</td>`;
  if (c.fmt === 'int') return `<td class="num">${int(v)}</td>`;
  // A rank taken as-a-single-institution is a different measurement from a
  // peer-set rank, so it carries a footnote marker wherever it is printed.
  if (c.fmt === 'rank') {
    if (v == null) return '<td class="num flat">—</td>';
    return `<td class="num">${int(v)}${c.asifKey && r[c.asifKey] ? asifMark() : ''}</td>`;
  }
  if (c.fmt === 'delta') {
    if (v == null) return '<td class="num flat">—</td>';
    const n = Number(v);
    return `<td class="num ${n > 0 ? 'up' : n < 0 ? 'down' : 'flat'}">`
      + `${n > 0 ? '+' : ''}${int(n)}${c.asifKey && r[c.asifKey] ? asifMark() : ''}</td>`;
  }
  if (c.fmt === 'name') {
    return `<td class="name${st}"><span class="swatch" style="background:${tint}"></span>${esc(v)}${
      flag ? '<span class="tag lb" title="' + esc(LOWER_BOUND_NOTE) + '">lower bound</span>' : ''}</td>`;
  }
  if (c.fmt === 'dept') {
    return `<td class="dept${st}">${esc(v)}${
      flag && !opts.nameCol ? '<span class="tag lb" title="' + esc(LOWER_BOUND_NOTE) + '">lower bound</span>' : ''}</td>`;
  }
  return `<td>${esc(v)}</td>`;
}

/* Expanded detail: the full mechanism-family split for one row, drawn as a
   single proportional bar plus the figures. */
function detailHTML(r, span, flag) {
  const parts = MECH_KEYS.map(([k, label]) => [label, Number(r[k]) || 0])
    .filter(p => p[1] > 0);
  const total = parts.reduce((s, p) => s + p[1], 0);
  const colors = window.RMGBCharts ? RMGBCharts.MECH_COLORS : {};
  const cmap = {
    R01: colors.R01, 'Other R': colors.R_OTHER, U: colors.U, P: colors.P,
    K: colors.K, T: colors.T, F: colors.F, Other: colors.OTHER,
  };
  let mech = '';
  if (total > 0) {
    mech = '<div class="mixbar">' + parts.map(([label, v]) =>
      `<span title="${esc(label)}: ${esc(money(v))}" style="width:${((v / total) * 100).toFixed(2)}%;`
      + `background:${cmap[label] || BAR_DEFAULT}"></span>`).join('') + '</div>'
      + '<div class="mixkey">' + parts.map(([label, v]) =>
        `<span class="lgd"><i style="background:${cmap[label] || BAR_DEFAULT}"></i>${esc(label)} `
        + `<b>${esc(money(v))}</b> <em>${((v / total) * 100).toFixed(1)}%</em></span>`).join('') + '</div>';
  } else {
    mech = '<p class="hint">No mechanism split is carried at this grain.</p>';
  }

  const fields = [
    ['Total funding', money(r.total_funding)],
    ['Award-years', int(r.award_years)],
    ['Distinct projects', int(r.distinct_projects)],
    ['R01 funding', money(r.r01_funding)],
    ['R01 award-years', int(r.r01_award_years)],
    ['Funded investigators', int(r.funded_investigators)],
    ['$ / investigator', money(r.funding_per_investigator)],
    ['$ / project', money(r.funding_per_project)],
    ['Mean award size', money(r.mean_award_size)],
    ['R01 share', pct(r.r01_share_of_funding)],
    ['Projects / investigator', ratio(r.projects_per_investigator)],
    ['Country', esc(r.org_country || '—')],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');

  return `<tr class="detailrow${flag ? ' flagrow' : ''}"><td colspan="${span}"><div class="detail">`
    + `<div class="detail-h">${esc(r.display_name || r.canonical_name || r.nih_org_dept || '')}`
    + `${r.nih_org_dept && r.display_name ? ' · ' + esc(r.nih_org_dept) : ''}`
    + (flag ? `<span class="tag lb">lower bound</span>` : '') + '</div>'
    + (flag ? `<p class="hint recon-note">${esc(LOWER_BOUND_NOTE)}</p>` : '')
    + mech + `<dl class="detail-g">${fields}</dl></div></td></tr>`;
}

function renderTable(mountId, rows, spec, opts) {
  opts = opts || {};
  const mount = document.getElementById(mountId);
  if (!mount) return;
  if (!rows.length) {
    mount.className = 'empty';
    mount.innerHTML = `<p class="empty-t">${esc(opts.emptyTitle || 'No rows match.')}</p>`
      + `<p class="empty-s">${esc(opts.emptyHint || 'Loosen a filter, or clear them all.')}</p>`
      + (opts.emptyAction ? `<p><button type="button" class="btn" id="${opts.emptyAction}">Clear all filters</button></p>` : '');
    if (opts.onEmptyAction) {
      const b = mount.querySelector('button');
      if (b) b.addEventListener('click', opts.onEmptyAction);
    }
    return;
  }
  mount.className = '';
  const max = opts.barKey
    ? Math.max.apply(null, rows.map(r => Number(r[opts.barKey]) || 0)) : 0;
  const sorts = state.sort[mountId] || [];
  const sortIx = {};
  sorts.forEach((s, i) => { sortIx[s.key] = { dir: s.dir, n: i + 1 }; });
  const offset = opts.offset || 0;
  const expand = !!opts.keyFn;
  const open = state.expanded[mountId] || (state.expanded[mountId] = new Set());
  opts.nameCol = spec.some(c => c.fmt === 'name');

  // Sticky left edge: the rank column, then whichever column carries identity.
  let left = 0;
  spec.forEach(c => {
    if (c.key === '__rank' || c.fmt === 'name' || (c.fmt === 'dept' && !opts.nameCol)) {
      c.sticky = true;
      c.stickyLeft = left;
      left += c.key === '__rank' ? 46 : (c.fmt === 'name' ? 250 : 210);
    }
  });

  const head = (expand ? '<th class="nosort xcol" aria-label="Expand"></th>' : '') + spec.map(c => {
    const s = sortIx[c.key];
    const cls = [c.sortable === false ? 'nosort' : '', c.sticky ? 'sticky' : '',
      c.fmt === 'money' || c.fmt === 'int' || c.fmt === 'pct' || c.fmt === 'ratio'
        || c.fmt === 'delta' || c.fmt === 'rank' ? 'r' : ''].filter(Boolean).join(' ');
    const style = c.sticky ? ` style="left:${c.stickyLeft}px"` : '';
    const aria = s ? ` aria-sort="${s.dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    const tab = c.sortable === false ? '' : ' tabindex="0" role="button"';
    const ind = s
      ? `<span class="so">${s.dir === 'asc' ? '▲' : '▼'}${sorts.length > 1 ? `<sup>${s.n}</sup>` : ''}</span>`
      : '';
    const title = c.sortable === false ? '' : ' title="Click to sort. Shift-click to add a secondary key."';
    return `<th class="${cls}"${style}${aria}${tab}${title} data-key="${esc(c.key)}">${esc(c.label)}${ind}</th>`;
  }).join('');

  const span = spec.length + (expand ? 1 : 0);
  const body = rows.map((r, i) => {
    const flag = opts.flagFn ? opts.flagFn(r) : null;
    const tint = opts.colorFn ? opts.colorFn(r) : BAR_DEFAULT;
    const rk = expand ? opts.keyFn(r) : null;
    const isOpen = rk != null && open.has(rk);
    const cells = spec.map(c => {
      if (c.key === '__rank') {
        return `<td class="rk sticky" style="left:${c.stickyLeft}px">${offset + i + 1}</td>`;
      }
      return cellHTML(c, r, opts, flag, tint, max);
    }).join('');
    const xc = expand
      ? `<td class="xcol"><button type="button" class="xbtn" data-rk="${esc(rk)}" `
        + `aria-expanded="${isOpen}" aria-label="Show the mechanism breakdown">${isOpen ? '−' : '+'}</button></td>`
      : '';
    return `<tr class="${flag ? 'flagrow' : ''}${isOpen ? ' isopen' : ''}">${xc}${cells}</tr>`
      + (isOpen ? detailHTML(r, span, flag) : '');
  }).join('');

  mount.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

  mount.querySelectorAll('th[data-key]').forEach(th => {
    if (th.classList.contains('nosort')) return;
    const key = th.dataset.key;
    if (key.startsWith('__')) return;
    const go = additive => { toggleSort(mountId, key, additive); if (opts.onSort) opts.onSort(); };
    th.addEventListener('click', e => go(e.shiftKey));
    th.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e.shiftKey); }
    });
  });

  if (expand) {
    mount.querySelectorAll('.xbtn').forEach(b => b.addEventListener('click', () => {
      const rk = b.dataset.rk;
      if (open.has(rk)) open.delete(rk); else open.add(rk);
      if (opts.onSort) opts.onSort();
    }));
  }
}

/* --- column catalogue ---------------------------------------------------- */

const METRIC_LABEL = {
  total_funding: 'Total funding', award_years: 'Award-years',
  distinct_projects: 'Projects', r01_funding: 'R01 funding',
  r01_award_years: 'R01 award-years', funded_investigators: 'Investigators',
  funding_R01: 'R01 (incl. R37)', funding_R_OTHER: 'Other R',
  funding_U: 'U funding', funding_P: 'P funding', funding_K: 'K funding',
  funding_T: 'T funding', funding_F: 'F funding', funding_OTHER: 'Other mechanisms',
  funding_per_investigator: '$ / investigator',
  r01_funding_per_investigator: 'R01 $ / investigator',
  funding_per_project: '$ / project',
  mean_award_size: 'Mean award size',
  projects_per_investigator: 'Projects / investigator',
  r01_share_of_funding: 'R01 share',
  m_award_years: 'M award-years',
};

/* Order matters: this is the column order in the table and in the export. */
const NUMERIC_ORDER = [
  'total_funding', 'award_years', 'distinct_projects', 'r01_funding',
  'r01_award_years', 'funded_investigators',
  'funding_R01', 'funding_R_OTHER', 'funding_U', 'funding_P', 'funding_K',
  'funding_T', 'funding_F', 'funding_OTHER',
  'funding_per_investigator', 'r01_funding_per_investigator',
  'funding_per_project', 'mean_award_size', 'projects_per_investigator',
  'r01_share_of_funding',
];
const DEFAULT_ON = new Set([
  'total_funding', 'award_years', 'distinct_projects', 'r01_funding',
  'r01_award_years', 'funded_investigators',
]);

let AVAILABLE = new Set();          // columns the current grain actually carries

function textCols(grain) {
  const out = [];
  if (grain !== 'dept') out.push({ key: 'display_name', label: 'Institution', fmt: 'name', locked: true });
  if (grain !== 'inst') out.push({ key: 'nih_org_dept', label: 'Department', fmt: 'dept', locked: true });
  if (grain !== 'inst') out.push({ key: 'specialty', label: 'Specialty group', fmt: 'text' });
  if (grain !== 'dept') out.push({ key: 'org_country', label: 'Country', fmt: 'text' });
  return out;
}

function columnCatalogue(grain) {
  const out = textCols(grain).filter(c => AVAILABLE.has(c.key));
  NUMERIC_ORDER.forEach(k => {
    if (!AVAILABLE.has(k)) return;
    out.push({ key: k, label: METRIC_LABEL[k] || k, fmt: fmtFor(k) });
  });
  return out;
}

function defaultCols(grain) {
  const s = new Set();
  columnCatalogue(grain).forEach(c => {
    if (c.locked || DEFAULT_ON.has(c.key)) s.add(c.key);
  });
  s.add(EX.metric);
  return s;
}

/* --- searchable multi-select -------------------------------------------- */

/* 5,010 institutions will not fit in a <select multiple>, and a native one is
   unusable with a keyboard anyway. This is a filter box over a checkbox list,
   with the live row count for each option so the reader can see what a choice
   would cost before making it. */
function multiSelect(mountId, opts) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  const st = mount._st || (mount._st = { q: '' });
  const sel = opts.selected;
  const all = opts.options;                    // [{value, label, n}]
  const q = st.q.toLowerCase();
  const hits = q ? all.filter(o => o.label.toLowerCase().includes(q)) : all;
  const cap = opts.cap || 400;
  const shown = hits.slice(0, cap);

  mount.innerHTML =
    `<input type="search" class="mselq" placeholder="${esc(opts.placeholder || 'Filter…')}" `
    + `aria-label="${esc(opts.placeholder || 'Filter options')}" value="${esc(st.q)}">`
    + `<div class="mselacts"><button type="button" data-a="all">Select all shown</button>`
    + `<button type="button" data-a="none">Clear</button>`
    + `<span class="mselcount">${sel.size ? sel.size + ' selected' : 'all ' + all.length}</span></div>`
    + `<div class="msellist" role="group" aria-label="${esc(opts.placeholder || 'Options')}">`
    + (shown.length ? shown.map(o =>
      `<label class="mopt${sel.has(o.value) ? ' on' : ''}"><input type="checkbox" value="${esc(o.value)}"`
      + `${sel.has(o.value) ? ' checked' : ''}><span class="mlab">${esc(o.label)}</span>`
      + `<span class="mn">${o.n == null ? '' : int(o.n)}</span></label>`).join('')
      : '<p class="hint pad">Nothing matches that.</p>')
    + `</div>`
    + (hits.length > shown.length
      ? `<p class="hint pad">${int(hits.length - shown.length)} more — refine the box above.</p>` : '');

  const qEl = mount.querySelector('.mselq');
  let t;
  qEl.addEventListener('input', () => {
    st.q = qEl.value;
    clearTimeout(t);
    t = setTimeout(() => {
      const pos = qEl.selectionStart;
      multiSelect(mountId, opts);
      const nq = mount.querySelector('.mselq');
      nq.focus();
      try { nq.setSelectionRange(pos, pos); } catch (e) { /* ignore */ }
    }, 120);
  });
  mount.querySelectorAll('.msellist input').forEach(cb => cb.addEventListener('change', () => {
    if (cb.checked) sel.add(cb.value); else sel.delete(cb.value);
    opts.onChange();
  }));
  mount.querySelectorAll('.mselacts button').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.a === 'all') shown.forEach(o => sel.add(o.value));
    else sel.clear();
    opts.onChange();
  }));
}

/* --- explorer: data pipeline --------------------------------------------- */

async function loadPairs(period) {
  if (state.pairs[period]) return state.pairs[period];
  const r = await fetch(`data/pairs_${period}.json`);
  state.pairs[period] = unpack(await r.json());
  return state.pairs[period];
}

async function baseRows() {
  let rows;
  if (EX.grain === 'pairs') rows = await loadPairs(EX.period);
  else rows = unpack(state.core[`${EX.grain}_${EX.period}`]);
  AVAILABLE = new Set(rows.length ? Object.keys(rows[0]) : []);
  // One of the roll-up and its members, never both; the explorer's own control
  // says which. Both are in the payload and in the downloadable CSVs.
  return rows.filter(r => !mgbHidden(r, EX.mgb === 'split'));
}

/* `skip` names one facet to leave un-applied, so that facet's option counts
   reflect what selecting each option would actually give you. */
function filterRows(rows, skip) {
  const out = [];
  const q = EX.q.trim().toLowerCase();
  const isIntensity = INTENSITY.has(EX.metric);
  const wantMech = EX.mech.size > 0 && AVAILABLE.has('funding_R01');

  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];

    if (!EX.uncoded && r.specialty != null && NON_DEPT.has(r.specialty)) continue;

    if (skip !== 'country' && EX.grain !== 'dept') {
      if (EX.country === 'UNITED STATES' && r.org_country !== 'UNITED STATES') continue;
      if (EX.country === '__NONUS' && r.org_country === 'UNITED STATES') continue;
    }
    if (skip !== 'dept' && EX.dept.size && !EX.dept.has(r.nih_org_dept)) continue;
    if (skip !== 'spec' && EX.spec.size && !EX.spec.has(r.specialty)) continue;
    if (skip !== 'inst' && EX.inst.size && !EX.inst.has(r.canonical_org_id)) continue;

    if (skip !== 'recon') {
      if (EX.recon === 'measured' && isRecon(r)) continue;
      if (EX.recon === 'recon' && !isRecon(r)) continue;
    }
    if (EX.hideRollup && r.is_rollup === 1) continue;

    if (wantMech && skip !== 'mech') {
      let n = 0;
      EX.mech.forEach(k => { if ((Number(r[k]) || 0) > 0) n++; });
      if (EX.mechMode === 'any' && n === 0) continue;
      if (EX.mechMode === 'all' && n < EX.mech.size) continue;
      if (EX.mechMode === 'none' && n > 0) continue;
    }

    if (skip !== 'range') {
      let bad = false;
      for (let j = 0; j < EX.ranges.length; j++) {
        const g = EX.ranges[j];
        const v = r[g.key];
        if (v == null) { bad = true; break; }
        if (g.min != null && v < g.min) { bad = true; break; }
        if (g.max != null && v > g.max) { bad = true; break; }
      }
      if (bad) continue;
    }

    if (isIntensity && skip !== 'floor') {
      if ((r.funded_investigators || 0) < EX.floor || r[EX.metric] == null) continue;
    }

    if (q) {
      const hay = ((r.display_name || r.canonical_name || '') + ' '
        + (r.nih_org_dept || '') + ' ' + (r.specialty || '') + ' '
        + (r.org_country || '')).toLowerCase();
      if (!hay.includes(q)) continue;
    }
    out.push(r);
  }
  return out;
}

function tally(rows, key) {
  const m = new Map();
  rows.forEach(r => {
    const v = r[key];
    if (v == null) return;
    m.set(v, (m.get(v) || 0) + 1);
  });
  return m;
}

const rowKey = r =>
  (r.canonical_org_id || '') + '||' + (r.nih_org_dept || '') + '||' + (r.specialty || '');

/* --- explorer: chips ----------------------------------------------------- */

function chipsHTML() {
  const out = [];
  const add = (kind, val, label) =>
    out.push(`<button type="button" class="chip" data-kind="${kind}" data-v="${esc(val)}">`
      + `${esc(label)}<span class="x" aria-hidden="true">×</span>`
      + `<span class="vis-hidden"> — remove this filter</span></button>`);

  if (EX.country !== 'UNITED STATES' && EX.grain !== 'dept') {
    add('country', '', 'Country: ' + (EX.country === '__NONUS' ? 'outside the US' : 'all'));
  }
  if (EX.q) add('q', '', 'Search: “' + EX.q + '”');
  EX.dept.forEach(v => add('dept', v, 'Dept: ' + v));
  EX.spec.forEach(v => add('spec', v, 'Specialty: ' + v.replace(/_/g, ' ').toLowerCase()));
  EX.inst.forEach(v => add('inst', v, 'Inst: ' + instLabel(v)));
  if (EX.mech.size) {
    const names = Array.from(EX.mech).map(k => (MECH_KEYS.find(m => m[0] === k) || [, k])[1]);
    add('mech', '', `Mechanism ${EX.mechMode === 'none' ? 'has none of' : 'has ' + EX.mechMode} : ${names.join(', ')}`);
  }
  if (EX.recon !== 'all') {
    add('recon', '', EX.recon === 'recon' ? 'Reconstructed rows only' : 'NIH-coded rows only');
  }
  if (EX.hideRollup) add('rollup', '', 'Roll-up rows hidden');
  if (EX.uncoded) add('uncoded', '', 'Uncoded departments included');
  EX.ranges.forEach((g, i) => {
    const f = fmtFn(fmtFor(g.key));
    const lo = g.min == null ? '' : f(g.min), hi = g.max == null ? '' : f(g.max);
    const txt = lo && hi ? `${lo} – ${hi}` : lo ? `≥ ${lo}` : `≤ ${hi}`;
    add('range', String(i), `${METRIC_LABEL[g.key] || g.key}: ${txt}`);
  });
  if (INTENSITY.has(EX.metric) && EX.floor > 1) {
    add('floor', '', `${EX.floor}+ investigators`);
  }
  if (!out.length) return '<span class="chip-none">No filters beyond the defaults.</span>';
  return out.join('') + '<button type="button" class="chip clear" id="chip-clear">Clear all</button>';
}

const INST_NAMES = {};
function instLabel(id) { return INST_NAMES[id] || id; }

function clearAllFilters() {
  EX.dept.clear(); EX.spec.clear(); EX.inst.clear(); EX.mech.clear();
  EX.ranges = [];
  EX.q = '';
  EX.recon = 'all';
  EX.hideRollup = false;
  EX.uncoded = false;
  EX.country = 'UNITED STATES';
  EX.page = 1;
  syncControls();
  renderExplorer();
}

function syncControls() {
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  const chk = (id, v) => { const e = document.getElementById(id); if (e) e.checked = v; };
  set('c-grain', EX.grain);
  set('c-period', EX.period);
  set('c-country', EX.country);
  set('c-search', EX.q);
  set('c-floor', String(EX.floor));
  set('c-pagesize', String(EX.pageSize));
  set('ex-topn', String(EX.topn));
  set('ex-scale', EX.scale);
  set('tr-grain', TR.grain);
  set('tr-metric', TR.metric);
  set('tr-axis', TR.axis);
  set('tr-pool', String(TR.pool));
  set('tr-n', String(TR.n));
  chk('c-rollup', EX.hideRollup);
  set('c-mgb', EX.mgb);
  chk('c-uncoded', EX.uncoded);
  document.querySelectorAll('input[name="recon"]').forEach(r => { r.checked = r.value === EX.recon; });
  document.querySelectorAll('input[name="mechmode"]').forEach(r => { r.checked = r.value === EX.mechMode; });
}

/* --- explorer: render ---------------------------------------------------- */

let exBusy = false;

async function renderExplorer() {
  if (exBusy) return;
  exBusy = true;
  try {
    const base = await baseRows();

    // The metric list depends on which columns the grain carries.
    buildMetricSelect();
    if (!AVAILABLE.has(EX.metric)) EX.metric = 'total_funding';
    document.getElementById('c-metric').value = EX.metric;

    const isIntensity = INTENSITY.has(EX.metric);
    document.getElementById('f-floor').hidden = !isIntensity;
    document.getElementById('f-country').hidden = EX.grain === 'dept';
    document.getElementById('fb-inst').hidden = EX.grain === 'dept';
    document.getElementById('fb-dept').hidden = EX.grain === 'inst';
    document.getElementById('fb-spec').hidden = EX.grain === 'inst';
    document.getElementById('fb-mech').hidden = !AVAILABLE.has('funding_R01');

    const rows = filterRows(base);

    // Facet counts, each computed with its own facet lifted.
    const deptCounts = tally(filterRows(base, 'dept'), 'nih_org_dept');
    const specCounts = tally(filterRows(base, 'spec'), 'specialty');
    const instRows = filterRows(base, 'inst');
    const instCounts = tally(instRows, 'canonical_org_id');
    instRows.forEach(r => {
      if (r.canonical_org_id) INST_NAMES[r.canonical_org_id] = r.display_name || r.canonical_name;
    });

    if (EX.grain !== 'inst') {
      const opts = Array.from(new Set(base.map(r => r.nih_org_dept).filter(v => v != null)))
        .sort()
        .map(v => ({ value: v, label: v, n: deptCounts.get(v) || 0 }));
      document.getElementById('n-dept').textContent =
        EX.dept.size ? `${EX.dept.size} of ${opts.length}` : `${opts.length} available`;
      multiSelect('ms-dept', {
        options: opts, selected: EX.dept, placeholder: 'Filter departments…',
        onChange: () => { EX.page = 1; renderExplorer(); },
      });

      const sopts = Array.from(new Set(base.map(r => r.specialty).filter(v => v != null)))
        .sort()
        .map(v => ({ value: v, label: v.replace(/_/g, ' '), n: specCounts.get(v) || 0 }));
      document.getElementById('n-spec').textContent =
        EX.spec.size ? `${EX.spec.size} of ${sopts.length}` : `${sopts.length} available`;
      multiSelect('ms-spec', {
        options: sopts, selected: EX.spec, placeholder: 'Filter specialty groups…',
        onChange: () => { EX.page = 1; renderExplorer(); },
      });
    }

    if (EX.grain !== 'dept') {
      const seen = new Map();
      base.forEach(r => {
        if (r.canonical_org_id && !seen.has(r.canonical_org_id)) {
          seen.set(r.canonical_org_id, r.display_name || r.canonical_name || r.canonical_org_id);
        }
      });
      const iopts = Array.from(seen, ([value, label]) => ({ value, label, n: instCounts.get(value) || 0 }))
        .sort((a, b) => a.label.localeCompare(b.label));
      document.getElementById('n-inst').textContent =
        EX.inst.size ? `${EX.inst.size} of ${int(iopts.length)}` : `${int(iopts.length)} available`;
      multiSelect('ms-inst', {
        options: iopts, selected: EX.inst, placeholder: 'Filter institutions…', cap: 250,
        onChange: () => { EX.page = 1; renderExplorer(); },
      });
    }

    buildMechChecks();
    buildRangeUI();
    buildColumnChecks();

    const sorted = applySort(rows, 't-explorer', EX.metric);
    state.view.explorer = { rows: sorted, grain: EX.grain, period: EX.period };

    const total = sorted.length;
    const size = EX.pageSize || total || 1;
    const pages = Math.max(1, Math.ceil(total / size));
    if (EX.page > pages) EX.page = pages;
    const start = EX.pageSize ? (EX.page - 1) * size : 0;
    const shown = EX.pageSize ? sorted.slice(start, start + size) : sorted;

    const cols = EX.cols || defaultCols(EX.grain);
    const cat = columnCatalogue(EX.grain);
    const spec = [{ key: '__rank', label: '#', sortable: false }];
    cat.filter(c => c.locked && cols.has(c.key)).forEach(c => spec.push(Object.assign({}, c)));
    spec.push({ key: '__bar', label: '', sortable: false });
    spec.push({ key: EX.metric, label: METRIC_LABEL[EX.metric] || EX.metric, fmt: fmtFor(EX.metric) });
    cat.forEach(c => {
      if (c.locked || c.key === EX.metric || !cols.has(c.key)) return;
      spec.push(Object.assign({}, c));
    });

    const nRecon = sorted.reduce((s, r) => s + (isRecon(r) ? 1 : 0), 0);
    document.getElementById('c-count').innerHTML =
      `<strong>${int(total)}</strong> row${total === 1 ? '' : 's'}`
      + (total ? ` · ${esc(money(sorted.reduce((s, r) => s + (Number(r.total_funding) || 0), 0)))} total` : '')
      + (nRecon ? ` · <span class="warnink">${nRecon} reconstructed lower bound${nRecon === 1 ? '' : 's'}</span>` : '');

    renderPager(total, pages, start, shown.length);

    renderTable('t-explorer', shown, spec, {
      barKey: EX.metric,
      offset: start,
      keyFn: rowKey,
      flagFn: r => (isRecon(r) ? 'recon' : null),
      colorFn: r => colorFor(r.canonical_org_id),
      onSort: renderExplorer,
      emptyTitle: 'Nothing matches these filters.',
      emptyHint: 'The combination you have picked returns no rows. Remove a filter chip above, '
        + 'or clear them all and start again.',
      emptyAction: 'empty-clear',
      onEmptyAction: clearAllFilters,
    });

    document.getElementById('ex-chips').innerHTML = chipsHTML();
    renderExplorerChart(sorted);
    writeHash();
  } finally {
    exBusy = false;
  }
}

function renderPager(total, pages, start, n) {
  const p = document.getElementById('ex-pager');
  if (!p) return;
  if (!EX.pageSize || total <= (EX.pageSize || 0)) {
    p.innerHTML = total ? `<span class="pginfo">all ${int(total)}</span>` : '';
    return;
  }
  p.innerHTML =
    `<button type="button" class="btn pg" data-pg="first" ${EX.page === 1 ? 'disabled' : ''} aria-label="First page">⏮</button>`
    + `<button type="button" class="btn pg" data-pg="prev" ${EX.page === 1 ? 'disabled' : ''} aria-label="Previous page">‹</button>`
    + `<span class="pginfo">${int(start + 1)}–${int(start + n)} of ${int(total)}</span>`
    + `<button type="button" class="btn pg" data-pg="next" ${EX.page === pages ? 'disabled' : ''} aria-label="Next page">›</button>`
    + `<button type="button" class="btn pg" data-pg="last" ${EX.page === pages ? 'disabled' : ''} aria-label="Last page">⏭</button>`;
  p.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    const a = b.dataset.pg;
    EX.page = a === 'first' ? 1 : a === 'last' ? pages : a === 'prev' ? EX.page - 1 : EX.page + 1;
    renderExplorer();
    document.querySelector('#explorer .tablewrap').scrollTop = 0;
  }));
}

function buildMetricSelect() {
  const sel = document.getElementById('c-metric');
  const sig = EX.grain + '|' + Array.from(AVAILABLE).join(',');
  if (sel._sig === sig) return;
  sel._sig = sig;
  const raw = NUMERIC_ORDER.filter(k => AVAILABLE.has(k) && !INTENSITY.has(k));
  const norm = NUMERIC_ORDER.filter(k => AVAILABLE.has(k) && INTENSITY.has(k));
  sel.innerHTML =
    raw.map(k => `<option value="${k}">${esc(METRIC_LABEL[k] || k)}</option>`).join('')
    + (norm.length
      ? `<optgroup label="Size-normalised">${norm.map(k =>
        `<option value="${k}">${esc(METRIC_LABEL[k] || k)}</option>`).join('')}</optgroup>` : '');
}

function buildMechChecks() {
  const box = document.getElementById('mech-chks');
  if (box._built) {
    box.querySelectorAll('input').forEach(i => { i.checked = EX.mech.has(i.value); });
    return;
  }
  box._built = true;
  box.innerHTML = MECH_KEYS.map(([k, label]) =>
    `<label><input type="checkbox" value="${k}"> ${esc(label)}</label>`).join('');
  box.querySelectorAll('input').forEach(i => i.addEventListener('change', () => {
    if (i.checked) EX.mech.add(i.value); else EX.mech.delete(i.value);
    EX.page = 1;
    renderExplorer();
  }));
}

function buildRangeUI() {
  const sel = document.getElementById('rg-metric');
  const keys = NUMERIC_ORDER.filter(k => AVAILABLE.has(k));
  const sig = keys.join(',');
  if (sel._sig !== sig) {
    sel._sig = sig;
    const keep = sel.value;
    sel.innerHTML = keys.map(k => `<option value="${k}">${esc(METRIC_LABEL[k] || k)}</option>`).join('');
    if (keys.includes(keep)) sel.value = keep;
  }
  const list = document.getElementById('rg-list');
  list.innerHTML = EX.ranges.length
    ? EX.ranges.map((g, i) => {
      const f = fmtFn(fmtFor(g.key));
      const lo = g.min == null ? '−∞' : f(g.min), hi = g.max == null ? '∞' : f(g.max);
      return `<div class="rgitem"><span>${esc(METRIC_LABEL[g.key] || g.key)}</span>`
        + `<code>${esc(lo)} … ${esc(hi)}</code>`
        + `<button type="button" class="x" data-i="${i}" aria-label="Remove this range">×</button></div>`;
    }).join('')
    : '<p class="hint pad">No range bounds set.</p>';
  list.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    EX.ranges.splice(Number(b.dataset.i), 1);
    EX.page = 1;
    renderExplorer();
  }));
}

function buildColumnChecks() {
  const box = document.getElementById('col-chks');
  const cat = columnCatalogue(EX.grain);
  const cols = EX.cols || defaultCols(EX.grain);
  const sig = EX.grain + '|' + cat.map(c => c.key).join(',') + '|' + Array.from(cols).sort().join(',');
  if (box._sig === sig) return;
  box._sig = sig;
  document.getElementById('n-cols').textContent = `${cols.size} of ${cat.length}`;
  box.innerHTML = cat.map(c =>
    `<label${c.locked ? ' class="lockedcol"' : ''}><input type="checkbox" value="${esc(c.key)}"`
    + `${cols.has(c.key) ? ' checked' : ''}${c.locked ? ' disabled' : ''}> ${esc(c.label)}</label>`).join('')
    + '<div class="chkacts"><button type="button" data-a="all">All</button>'
    + '<button type="button" data-a="min">Minimal</button></div>';
  box.querySelectorAll('input').forEach(i => i.addEventListener('change', () => {
    const s = new Set(EX.cols || defaultCols(EX.grain));
    if (i.checked) s.add(i.value); else s.delete(i.value);
    EX.cols = s;
    box._sig = null;
    renderExplorer();
  }));
  box.querySelectorAll('.chkacts button').forEach(b => b.addEventListener('click', () => {
    const s = new Set();
    cat.forEach(c => { if (b.dataset.a === 'all' || c.locked) s.add(c.key); });
    s.add(EX.metric);
    EX.cols = s;
    box._sig = null;
    renderExplorer();
  }));
}

/* --- explorer chart, linked to the table --------------------------------- */

function renderExplorerChart(sorted) {
  const mount = document.getElementById('ex-chart');
  if (!mount) return;
  const n = EX.topn;
  const key = EX.metric;
  const rows = sorted.filter(r => r[key] != null).slice(0, n);
  const f = fmtFn(fmtFor(key));
  const label = (METRIC_LABEL[key] || key).toLowerCase();
  const byDept = EX.grain === 'dept';

  document.getElementById('ex-ch-title').textContent =
    `Top ${rows.length} of the current view by ${label}`;
  document.getElementById('ex-ch-sub').textContent =
    `${PERIOD_LABEL[EX.period]} · ${int(sorted.length)} rows pass the filters · `
    + `click a bar to ${byDept ? 'filter to that department' : 'filter to that institution'} · `
    + 'hatched bars are reconstructed lower bounds';

  RMGBCharts.barChart(mount, rows.map(r => ({
    label: byDept ? r.nih_org_dept
      : (r.display_name || r.canonical_name) + (r.nih_org_dept ? ' — ' + r.nih_org_dept : ''),
    value: r[key],
    color: colorFor(r.canonical_org_id),
    hatched: isRecon(r),
    selected: byDept ? EX.dept.has(r.nih_org_dept) : EX.inst.has(r.canonical_org_id),
    sub: byDept ? null : r.org_country,
    detail: `${int(r.award_years)} award-years · ${int(r.distinct_projects)} projects · `
      + `${money(r.r01_funding)} R01`,
    _row: r,
  })), {
    fmt: f,
    labelW: byDept ? 240 : 320,
    scale: EX.scale === 'log' ? 'log' : 'lin',
    onSelect: d => {
      const r = d._row;
      if (byDept) {
        if (EX.dept.has(r.nih_org_dept)) EX.dept.delete(r.nih_org_dept);
        else EX.dept.add(r.nih_org_dept);
      } else if (EX.inst.has(r.canonical_org_id)) {
        EX.inst.delete(r.canonical_org_id);
      } else {
        EX.inst.add(r.canonical_org_id);
      }
      EX.page = 1;
      renderExplorer();
      document.querySelector('#explorer .tablebar').scrollIntoView({ block: 'start' });
    },
  });
}

/* --- trends over time ---------------------------------------------------- */

/* Two per-year files, one row per entity per fiscal year, each carrying both
   the figure and the rank NIH's own ordering gives it that year. They are
   large and only one tab needs them, so they are fetched the first time that
   tab is opened and never again. */
const TREND_FILES = {
  inst: 'data/trend_institution.json',
  pairs: 'data/trend_institution_department.json',
};
const TREND_YEARS = [2021, 2022, 2023, 2024, 2025];
const TREND_METRICS = [
  'total_funding', 'award_years', 'distinct_projects', 'r01_funding', 'r01_award_years',
];
// Running-text forms. Lower-casing METRIC_LABEL mid-sentence would print
// "r01 funding", which is not what the mechanism is called.
const TREND_PHRASE = {
  total_funding: 'total funding', award_years: 'award-years',
  distinct_projects: 'distinct projects', r01_funding: 'R01 funding',
  r01_award_years: 'R01 award-years',
};
// Departments that exist in the file only as the absence of a department.
const TREND_NO_DEPT = new Set(['__MISSING__', 'NONE', '']);

/* Fallback series colours for the ~4,970 institutions with no brand colour in
   core.json. Taken from the published figures rather than invented, so a chart
   of unbranded institutions still looks like the rest of the site. */
const SERIES_RAMP = [
  '#1b3a5c', '#b8352c', '#2e6b4f', '#a8481f', '#2e6b9e', '#7a1f5c',
  '#4a5967', '#4e9ac4', '#8a7a2e', '#c97c5d', '#6b8f71', '#7fbfd8',
];
// Weighted-RGB distance below which two lines read as the same colour.
const COLOR_MIN_GAP = 85;

const hexToRgb = h => {
  const s = String(h == null ? '' : h).replace('#', '');
  const p = s.length === 3
    ? [s[0] + s[0], s[1] + s[1], s[2] + s[2]]
    : [s.slice(0, 2), s.slice(2, 4), s.slice(4, 6)];
  const n = p.map(x => parseInt(x, 16));
  return n.some(x => Number.isNaN(x)) ? null : n;
};
const rgbToHex = a => '#' + a.map(v => {
  const s = Math.max(0, Math.min(255, Math.round(v))).toString(16);
  return s.length === 1 ? '0' + s : s;
}).join('');
const colorGap = (a, b) => Math.sqrt(
  2 * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + 3 * (a[2] - b[2]) ** 2);
// k > 0 mixes toward white, k < 0 toward black.
const shade = (rgb, k) => rgb.map(v => (k > 0 ? v + (255 - v) * k : v * (1 + k)));

async function loadTrend(grain) {
  if (state.trend[grain]) return state.trend[grain];
  if (!state.trendReq[grain]) {
    state.trendReq[grain] = fetch(TREND_FILES[grain])
      .then(r => r.json())
      .then(j => { state.trend[grain] = unpack(j); return state.trend[grain]; })
      .catch(err => { state.trendReq[grain] = null; throw err; });
  }
  return state.trendReq[grain];
}

const trendKey = (r, grain) => (grain === 'inst'
  ? r.canonical_org_id
  : r.canonical_org_id + '||' + (r.nih_org_dept == null ? '' : r.nih_org_dept));

/* One entity per key, with its five years hung off it. Built once per grain. */
function trendIndex(grain) {
  if (state.trendIx[grain]) return state.trendIx[grain];
  const rows = state.trend[grain] || [];
  const map = new Map();
  const denom = {};
  rows.forEach(r => {
    if (r.n_ranked != null) denom[r.fiscal_year] = r.n_ranked;
    // MGH, BWH and the roll-up are all in the payload so a reader can follow
    // either. The wider MGB_SYSTEM roll-up is a third entity again and is not
    // published; the trend picker holds no view control, so both readings are
    // offered here and the user selects whichever lines they want.
    if (NEVER_SHOWN.has(r.canonical_org_id)) return;
    if (grain === 'pairs' && TREND_NO_DEPT.has(r.nih_org_dept)) return;
    const k = trendKey(r, grain);
    let e = map.get(k);
    if (!e) {
      const name = r.display_name || r.canonical_org_id;
      e = {
        key: k,
        canonical_org_id: r.canonical_org_id,
        nih_org_dept: grain === 'inst' ? null : r.nih_org_dept,
        specialty: r.specialty || null,
        display_name: name,
        label: grain === 'inst' ? name : name + ' — ' + r.nih_org_dept,
        org_country: r.org_country,
        is_rollup: r.is_rollup,
        years: {},
      };
      map.set(k, e);
    }
    e.years[r.fiscal_year] = r;
  });
  state.trendIx[grain] = { map, list: Array.from(map.values()), denom };
  return state.trendIx[grain];
}

/* The standing to plot for one entity in one year, and which of the two
   measurements it came from.

   A roll-up is held out of the ordinary rank on purpose: it aggregates rows
   that are already in the table, so counting it would push every real
   institution down a place. `rank_*_if_single_entity` answers the other
   question — where the aggregate would sit if it were one institution
   inserted into that ranking — counted against the non-roll-up population
   only. The two are different measurements and are never presented as one.

   The pipeline also emits the as-single column on rows that are not roll-ups
   at the institution-department grain (MISCELLANEOUS departments, and rows
   with no department code), where that reading does not hold. It is therefore
   taken only where the row is genuinely a roll-up. */
function trendStanding(e, y, metric) {
  const r = e.years[y];
  if (!r) return null;
  const peer = r['rank_' + metric];
  if (peer != null) return { rank: Number(peer), asSingle: false, n: r.n_ranked };
  if (r.is_rollup === 1) {
    const solo = r['rank_' + metric + '_if_single_entity'];
    if (solo != null) return { rank: Number(solo), asSingle: true, n: r.n_ranked };
  }
  return null;
}

const trendRank = (e, y, metric) => {
  const s = trendStanding(e, y, metric);
  return s ? s.rank : null;
};
const trendValue = (e, y, metric) => {
  const r = e.years[y];
  const v = r ? r[metric] : null;
  return v == null ? null : Number(v);
};

/* The pool a preset draws from: the department filter, then a cut on FY2025
   standing so "biggest riser" does not just surface churn at the tail of a
   3,500-row table. */
function trendPool(ix) {
  let list = ix.list;
  if (TR.grain === 'pairs' && TR.dept.size) {
    list = list.filter(e => TR.dept.has(e.nih_org_dept));
  }
  return list;
}

function applyTrendPreset(kind) {
  if (kind === 'clear') { TR.sel.clear(); return; }
  if (!state.trend[TR.grain]) { toast('Still loading the per-year file.'); return; }
  const ix = trendIndex(TR.grain);
  const m = TR.metric;
  const last = TREND_YEARS[TREND_YEARS.length - 1];
  const first = TREND_YEARS[0];
  let list = trendPool(ix).filter(e => trendRank(e, last, m) != null);
  if (TR.pool) list = list.filter(e => trendRank(e, last, m) <= TR.pool);

  let picked;
  if (kind === 'top') {
    picked = list.slice().sort((a, b) => trendRank(a, last, m) - trendRank(b, last, m));
  } else {
    const moved = list.filter(e => trendRank(e, first, m) != null).map(e => ({
      e, d: trendRank(e, first, m) - trendRank(e, last, m),
    }));
    moved.sort((a, b) => (kind === 'risers' ? b.d - a.d : a.d - b.d));
    picked = moved
      .filter(x => (kind === 'risers' ? x.d > 0 : x.d < 0))
      .map(x => x.e);
  }
  TR.sel = new Set(picked.slice(0, TR.n).map(e => e.key));
  if (!TR.sel.size) toast('Nothing in that pool moved that way. Widen the pool.');
}

/* One drawable series per selected entity, ordered by final-year standing so
   the colour assignment and the legend agree with the chart top to bottom. */
function trendSelected() {
  const ix = trendIndex(TR.grain);
  const m = TR.metric;
  const last = TREND_YEARS[TREND_YEARS.length - 1];
  const out = [];
  TR.sel.forEach(k => { const e = ix.map.get(k); if (e) out.push(e); });
  out.sort((a, b) => {
    const ra = trendRank(a, last, m), rb = trendRank(b, last, m);
    if (ra != null && rb != null && ra !== rb) return ra - rb;
    if (ra != null && rb == null) return -1;
    if (ra == null && rb != null) return 1;
    const va = trendValue(a, last, m) || 0, vb = trendValue(b, last, m) || 0;
    if (va !== vb) return vb - va;
    return a.label.localeCompare(b.label);
  });

  // Brand colour first. But six of the ten best-funded US institutions brand
  // themselves in near-identical navy, and six navy lines crossing each other
  // is not a chart. Where two would collide the second is lightened or
  // darkened until it separates, which keeps the identity and the legibility.
  const used = [];
  const take = hex => {
    const rgb = hexToRgb(hex);
    if (!rgb) return null;
    if (!used.some(u => colorGap(u, rgb) < COLOR_MIN_GAP)) { used.push(rgb); return hex; }
    const tries = [0.3, -0.35, 0.5];
    for (let i = 0; i < tries.length; i++) {
      const c = shade(rgb, tries[i]);
      if (!used.some(u => colorGap(u, c) < COLOR_MIN_GAP)) { used.push(c); return rgbToHex(c); }
    }
    return null;
  };
  out.forEach((e, i) => {
    const brand = (state.core && state.core.colors && state.core.colors[e.canonical_org_id]) || null;
    let c = brand ? take(brand) : null;
    let h = i;
    for (let j = 0; j < e.key.length; j++) h = (h * 31 + e.key.charCodeAt(j)) >>> 0;
    for (let k = 0; k < SERIES_RAMP.length && !c; k++) {
      c = take(SERIES_RAMP[(h + k) % SERIES_RAMP.length]);
    }
    e._color = c || brand || SERIES_RAMP[h % SERIES_RAMP.length];
  });
  return out;
}

/* Row objects for the table and the export. Both the short display keys and
   long self-describing keys are carried, so a downloaded CSV says
   rank_2021 rather than r_2021 without the table needing to know. */
function trendTableRows(entities) {
  const m = TR.metric;
  return entities.map(e => {
    const row = {
      canonical_org_id: e.canonical_org_id,
      nih_org_dept: e.nih_org_dept,
      label: e.label,
      metric: m,
      _color: e._color,
    };
    TREND_YEARS.forEach(y => {
      const st = trendStanding(e, y, m);
      const rk = st ? st.rank : null;
      const v = trendValue(e, y, m);
      const r = e.years[y];
      row['r_' + y] = rk;
      row['s_' + y] = !!(st && st.asSingle);
      row['v_' + y] = v;
      row['rank_' + y] = rk;
      row['rank_basis_' + y] = rk == null ? '' : (st.asSingle ? 'as_single_entity' : 'peer_set');
      row[m + '_' + y] = v;
      row['n_ranked_' + y] = r ? r.n_ranked : null;
    });
    const a = row.r_2021, b = row.r_2025;
    row.d_rank = (a == null || b == null) ? null : a - b;
    row.d_asif = row.d_rank != null && (row.s_2021 || row.s_2025);
    row.rank_change_2021_2025 = row.d_rank;
    return row;
  });
}

function trendChipsHTML() {
  const ix = trendIndex(TR.grain);
  const out = [];
  const add = (kind, val, label) =>
    out.push(`<button type="button" class="chip" data-kind="${kind}" data-v="${esc(val)}">`
      + `${esc(label)}<span class="x" aria-hidden="true">×</span>`
      + `<span class="vis-hidden"> — stop following this</span></button>`);
  TR.dept.forEach(d => add('dept', d, 'Dept: ' + d));
  TR.sel.forEach(k => {
    const e = ix.map.get(k);
    add('sel', k, e ? e.label : k);
  });
  if (!out.length) return '<span class="chip-none">Nothing followed yet. Try a preset.</span>';
  return out.join('')
    + '<button type="button" class="chip clear" id="tr-chip-clear">Clear all</button>';
}

let trBusy = false;

async function renderTrends() {
  if (trBusy) return;
  const grain = TR.grain;
  document.getElementById('tfb-dept').hidden = grain !== 'pairs';
  document.getElementById('tfb-inst').className = 'fbox ' + (grain === 'pairs' ? 'span2' : 'span4');
  document.getElementById('tr-inst-t').textContent =
    grain === 'pairs' ? 'Follow these institution–department pairs' : 'Follow these institutions';

  if (!state.trend[grain]) {
    document.getElementById('ch-trend').innerHTML =
      '<p class="emptystate">Loading the per-year file…</p>';
    const t = document.getElementById('t-trend');
    t.className = 'loading';
    t.textContent = 'Loading the per-year file…';
  }

  trBusy = true;
  try {
    await loadTrend(grain);
  } catch (err) {
    document.getElementById('ch-trend').innerHTML =
      `<p class="emptystate">Could not load ${esc(TREND_FILES[grain])}.</p>`;
    trBusy = false;
    return;
  }
  trBusy = false;
  if (TR.grain !== grain) { renderTrends(); return; }

  const ix = trendIndex(grain);
  const m = TR.metric;

  if (grain === 'pairs') {
    const counts = new Map();
    ix.list.forEach(e => counts.set(e.nih_org_dept, (counts.get(e.nih_org_dept) || 0) + 1));
    const dopts = Array.from(counts.keys()).sort()
      .map(v => ({ value: v, label: v, n: counts.get(v) }));
    document.getElementById('n-tr-dept').textContent =
      TR.dept.size ? `${TR.dept.size} of ${dopts.length}` : `${dopts.length} available`;
    multiSelect('ms-tr-dept', {
      options: dopts, selected: TR.dept, placeholder: 'Filter departments…',
      onChange: renderTrends,
    });
  }

  // Ordered by final-year standing rather than alphabetically: a list of 5,007
  // institutions in A–Z order opens on noise, and the filter box is there for
  // anyone who knows the name they want.
  const finalYear = TREND_YEARS[TREND_YEARS.length - 1];
  const iopts = trendPool(ix).map(e => {
    const rk = trendRank(e, finalYear, m);
    return { value: e.key, label: e.label, n: rk, rk };
  }).sort((a, b) => {
    if (a.rk != null && b.rk != null && a.rk !== b.rk) return a.rk - b.rk;
    if (a.rk != null && b.rk == null) return -1;
    if (a.rk == null && b.rk != null) return 1;
    return a.label.localeCompare(b.label);
  });
  document.getElementById('n-tr-inst').textContent =
    TR.sel.size ? `${TR.sel.size} followed of ${int(iopts.length)}` : `${int(iopts.length)} available`;
  multiSelect('ms-tr-inst', {
    options: iopts, selected: TR.sel, cap: 250,
    placeholder: grain === 'pairs' ? 'Filter pairs…' : 'Filter institutions…',
    onChange: renderTrends,
  });

  document.getElementById('tr-chips').innerHTML = trendChipsHTML();

  const entities = trendSelected();
  const rows = trendTableRows(entities);
  const label = (METRIC_LABEL[m] || m);
  const f = fmtFn(fmtFor(m));
  const byRank = TR.axis === 'rank';

  // Two different absences. An entity that resolves to nothing in any year has
  // no standing of either kind and genuinely cannot be drawn on a rank axis; a
  // roll-up resolves to its as-a-single-institution position and is drawn.
  const unranked = entities.filter(e =>
    TREND_YEARS.every(y => trendStanding(e, y, m) == null)).length;
  const asSingle = entities.filter(e =>
    TREND_YEARS.some(y => { const s = trendStanding(e, y, m); return s && s.asSingle; })).length;

  const phrase = TREND_PHRASE[m] || label.toLowerCase();
  document.getElementById('tr-ch-title').textContent = byRank
    ? `Rank by ${phrase}, FY2021 to FY2025`
    : `${label}, FY2021 to FY2025`;
  document.getElementById('tr-ch-sub').textContent =
    `${entities.length} ${grain === 'pairs' ? 'institution–department pair' : 'institution'}`
    + `${entities.length === 1 ? '' : 's'} followed · `
    + (byRank
      ? 'rank 1 at the top, so a line rising on the page is an institution moving up the table'
      : 'the figure itself, so a line rising on the page is an institution growing')
    + (byRank && asSingle
      ? ` · ${asSingle} roll-up${asSingle === 1 ? '' : 's'} drawn with hollow markers at the `
        + 'position it would take as a single institution, which is not a peer-set rank'
      : '')
    + (unranked
      ? ` · ${unranked} of them carries no rank of either kind and is drawn only on the value axis`
      : '')
    + ' · hover a point for the year, the rank, the denominator and the figure';

  const noteEl = document.getElementById('tr-note-rank');
  if (noteEl) {
    const parts = [];
    if (byRank && asSingle) parts.push(AS_SINGLE_NOTE);
    if (unranked) {
      parts.push('A row NIH holds outside the ranked table — no department code, or a residual '
        + 'category such as MISCELLANEOUS — has no rank of either kind. It carries a figure, so '
        + 'it appears on the value axis and breaks on the rank axis.');
    }
    noteEl.textContent = parts.join(' ');
  }

  RMGBCharts.bumpChart(document.getElementById('ch-trend'), entities.map(e => ({
    key: e.key,
    label: e.label,
    color: e._color,
    dashed: isRecon(e),
    points: TREND_YEARS.map(y => {
      const st = trendStanding(e, y, m);
      const rk = st ? st.rank : null;
      const v = trendValue(e, y, m);
      const r = e.years[y];
      const n = r && r.n_ranked != null ? r.n_ranked : ix.denom[y];
      const basis = st && st.asSingle ? ' — as a single institution' : ' ranked';
      const tip = `<strong>${esc(e.label)}</strong><br>FY${y}`
        + (rk != null
          ? `<br><span class="tipv">#${int(rk)}</span> of ${int(n)}${basis}`
          : (r
            ? '<br><em>No rank of either kind this year — NIH holds this row outside the ranked '
              + 'table</em>'
            : '<br><em>No figure at all this year</em>'))
        + (v != null ? `<br>${esc(label)}: <span class="tipv">${esc(f(v))}</span>` : '')
        + (st && st.asSingle
          ? '<br><em>A roll-up is held out of the peer-set rank because it aggregates rows already '
            + 'in the table. This is where it would sit inserted as one institution.</em>' : '')
        + (isRecon(e) ? '<br><em>Reconstructed from publication affiliations — a lower bound</em>' : '');
      return {
        x: y,
        y: byRank ? rk : v,
        hollow: byRank && !!(st && st.asSingle),
        tip,
        aria: rk != null
          ? `rank ${rk} of ${n}${st.asSingle ? ' as a single institution' : ''}, ${f(v)}`
          : `not ranked, ${v == null ? 'no figure' : f(v)}`,
      };
    }),
  })), {
    years: TREND_YEARS,
    invert: byRank,
    fmt: f,
    denoms: byRank ? ix.denom : null,
    yTitle: byRank ? 'Rank (1 at the top)' : label,
    legendMount: document.getElementById('lg-trend'),
    labelW: grain === 'pairs' ? 280 : 220,
    empty: 'Nothing followed yet. Tick an institution above, or take a preset.',
    emptyValues: byRank
      ? 'None of these carries a rank on this measure in any year. NIH ranks only rows it can '
        + 'attribute; switch the y axis to the metric itself to see the figures.'
      : 'None of these carries a figure on this measure.',
  });

  const spec = [{ key: '__rank', label: '#', sortable: false },
    { key: 'label', label: grain === 'pairs' ? 'Institution — department' : 'Institution', fmt: 'name' }];
  TREND_YEARS.forEach(y => spec.push({
    key: 'r_' + y, label: '#FY' + String(y).slice(2), fmt: 'rank', asifKey: 's_' + y,
  }));
  spec.push({ key: 'd_rank', label: 'Δ rank 21→25', fmt: 'delta', asifKey: 'd_asif' });
  TREND_YEARS.forEach(y => spec.push({
    key: 'v_' + y, label: 'FY' + String(y).slice(2), fmt: fmtFor(m),
  }));

  // Untouched, the table keeps the chart's order: best FY2025 standing first.
  // A header click takes over from there.
  const keys = state.sort['t-trend'];
  const sorted = (keys && keys.length) ? applySort(rows, 't-trend', 'r_2025') : rows;
  document.getElementById('tr-count').innerHTML =
    `<strong>${int(rows.length)}</strong> followed · measured on ${esc(phrase)} · `
    + '#FY columns carry the rank, FY columns the figure'
    + (asSingle ? ` · <span class="warnink">${AS_SINGLE_MARK}</span> marks a position taken as a `
      + 'single institution, not a peer-set rank' : '');

  state.view.trend = {
    rows: sorted, grain: 'trend_' + grain, period: 'FY2021_FY2025',
    keys: ['canonical_org_id'].concat(grain === 'pairs' ? ['nih_org_dept'] : [])
      .concat(['label', 'metric'])
      .concat(TREND_YEARS.map(y => 'rank_' + y))
      .concat(TREND_YEARS.map(y => 'rank_basis_' + y))
      .concat(['rank_change_2021_2025'])
      .concat(TREND_YEARS.map(y => m + '_' + y))
      .concat(TREND_YEARS.map(y => 'n_ranked_' + y)),
  };

  renderTable('t-trend', sorted, spec, {
    flagFn: r => (isRecon(r) ? 'recon' : null),
    colorFn: r => r._color || BAR_DEFAULT,
    onSort: renderTrends,
    emptyTitle: 'Nothing followed yet.',
    emptyHint: 'Tick an institution in the panel above, or take one of the presets.',
  });

  writeHash();
}

function initTrendControls() {
  const on = (id, fn) => {
    const e = document.getElementById(id);
    if (e) e.addEventListener('change', fn);
  };
  on('tr-grain', e => {
    TR.grain = e.target.value;
    TR.sel.clear();
    TR.dept.clear();
    state.sort['t-trend'] = [];
    renderTrends();
  });
  on('tr-metric', e => { TR.metric = e.target.value; renderTrends(); });
  on('tr-axis', e => { TR.axis = e.target.value; renderTrends(); });
  on('tr-pool', e => { TR.pool = Number(e.target.value) || 0; writeHash(); });
  on('tr-n', e => { TR.n = Number(e.target.value) || 10; writeHash(); });

  document.querySelectorAll('#tr-panel [data-preset]').forEach(b =>
    b.addEventListener('click', () => {
      applyTrendPreset(b.dataset.preset);
      renderTrends();
    }));

  document.getElementById('tr-chips').addEventListener('click', e => {
    const c = e.target.closest('.chip');
    if (!c) return;
    if (c.id === 'tr-chip-clear') { TR.sel.clear(); TR.dept.clear(); }
    else if (c.dataset.kind === 'dept') TR.dept.delete(c.dataset.v);
    else TR.sel.delete(c.dataset.v);
    renderTrends();
  });
}

/* --- export -------------------------------------------------------------- */

function csvCell(v) {
  if (v == null) return '';
  const s = String(v);
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

/* Exports carry the reconstruction flag and its explanation whether or not the
   column is on screen, so a downloaded file can never present a lower bound as
   a measured figure. */
function exportCols(grain) {
  const cols = EX.cols || defaultCols(grain);
  const keys = columnCatalogue(grain).filter(c => cols.has(c.key)).map(c => c.key);
  if (grain !== 'dept' && !keys.includes('canonical_org_id')) keys.unshift('canonical_org_id');
  return keys;
}

function viewToRecords(rows, keys) {
  return rows.map(r => {
    const o = {};
    keys.forEach(k => { o[k] = r[k] === undefined ? null : r[k]; });
    o.is_reconstructed = isRecon(r) ? 1 : 0;
    o.evidence_note = isRecon(r) ? LOWER_BOUND_NOTE : '';
    return o;
  });
}

function stamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '').slice(0, 13);
}

function download(text, mime, filename) {
  const blob = new Blob([text], { type: mime + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function exportView(kind, which) {
  const v = state.view[which || 'explorer'] || state.view.explorer;
  if (!v || !v.rows.length) { toast('Nothing to export.'); return; }
  const keys = v.keys || exportCols(v.grain);
  const recs = viewToRecords(v.rows, keys);
  const head = Object.keys(recs[0]);
  const name = `nih_${which || 'explorer'}_${v.grain || 'view'}_${v.period}_${stamp()}`;

  if (kind === 'json') {
    download(JSON.stringify({
      generated_at: new Date().toISOString(),
      source: 'NIH ExPORTER FY2021-FY2025 reproducible census',
      grain: v.grain, period: v.period,
      filters: hashParams().toString(),
      row_count: recs.length,
      note: 'Rows with is_reconstructed = 1 are publication-derived lower bounds and are '
        + 'not like-for-like with NIH department-coded rows.',
      rows: recs,
    }, null, 2), 'application/json', name + '.json');
    toast(`${int(recs.length)} rows exported as JSON.`);
    return;
  }

  const sep = kind === 'copy' ? '\t' : ',';
  const cell = kind === 'copy' ? (x => (x == null ? '' : String(x).replace(/[\t\n]/g, ' '))) : csvCell;
  const text = [head.join(sep)].concat(
    recs.map(r => head.map(k => cell(r[k])).join(sep))).join('\n');

  if (kind === 'copy') {
    const done = () => toast(`${int(recs.length)} rows copied.`);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => toast('The browser blocked the clipboard.'));
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed.'); }
      ta.remove();
    }
    return;
  }
  download(text, 'text/csv', name + '.csv');
  toast(`${int(recs.length)} rows exported as CSV.`);
}

/* --- URL state ----------------------------------------------------------- */

function hashParams() {
  const p = new URLSearchParams();
  const j = s => Array.from(s).join('~');
  if (EX.grain !== 'pairs') p.set('g', EX.grain);
  if (EX.period !== 'FY2021_FY2025') p.set('p', EX.period);
  if (EX.metric !== 'total_funding') p.set('m', EX.metric);
  if (EX.country !== 'UNITED STATES') p.set('c', EX.country);
  if (EX.q) p.set('q', EX.q);
  if (EX.dept.size) p.set('dept', j(EX.dept));
  if (EX.spec.size) p.set('spec', j(EX.spec));
  if (EX.inst.size) p.set('inst', j(EX.inst));
  if (EX.mech.size) { p.set('mech', j(EX.mech)); p.set('mm', EX.mechMode); }
  if (EX.recon !== 'all') p.set('rec', EX.recon);
  if (EX.hideRollup) p.set('noroll', '1');
  if (EX.mgb !== 'combined') p.set('mgb', EX.mgb);
  if (EX.uncoded) p.set('unc', '1');
  if (EX.floor !== 5) p.set('fl', String(EX.floor));
  if (EX.ranges.length) {
    p.set('rg', EX.ranges.map(g =>
      `${g.key}:${g.min == null ? '' : g.min}:${g.max == null ? '' : g.max}`).join('~'));
  }
  const s = state.sort['t-explorer'];
  if (s && s.length) p.set('s', s.map(x => x.key + ':' + x.dir[0]).join('~'));
  if (EX.pageSize !== 100) p.set('ps', String(EX.pageSize));
  if (EX.page !== 1) p.set('pg', String(EX.page));
  if (EX.cols) p.set('cols', Array.from(EX.cols).join('~'));
  if (EX.topn !== 20) p.set('tn', String(EX.topn));
  if (EX.scale !== 'lin') p.set('sc', EX.scale);
  if (TR.grain !== 'inst') p.set('tg', TR.grain);
  if (TR.metric !== 'total_funding') p.set('tm', TR.metric);
  if (TR.axis !== 'rank') p.set('ty', TR.axis);
  if (TR.pool !== 250) p.set('tp', String(TR.pool));
  if (TR.n !== 10) p.set('tk', String(TR.n));
  if (TR.dept.size) p.set('td', j(TR.dept));
  if (TR.sel.size) p.set('tsel', j(TR.sel));
  const ts = state.sort['t-trend'];
  if (ts && ts.length) p.set('tso', ts.map(x => x.key + ':' + x.dir[0]).join('~'));
  const sp = document.getElementById('s-period');
  if (sp && sp.value !== 'FY2021_FY2025') p.set('sp', sp.value);
  const ss = document.getElementById('s-split');
  if (ss && ss.value !== 'combined') p.set('ss', ss.value);
  const sm = document.getElementById('s-metric');
  if (sm && sm.value !== 'total_funding') p.set('sm', sm.value);
  const dp = document.getElementById('d-period');
  if (dp && dp.value !== 'FY2021_FY2025') p.set('dp', dp.value);
  return p;
}

let hashTimer;
let activeTab = 'explorer';
function writeHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const params = hashParams();
    if (state.focus) params.set('pin', state.focus);
    const q = params.toString();
    const h = '#' + activeTab + (q ? '?' + q : '');
    if (location.hash !== h) history.replaceState(null, '', h);
  }, 120);
}

function readHash() {
  const raw = location.hash.replace(/^#/, '');
  const i = raw.indexOf('?');
  const tab = (i === -1 ? raw : raw.slice(0, i)) || 'explorer';
  const p = new URLSearchParams(i === -1 ? '' : raw.slice(i + 1));
  const sset = (k, target) => {
    const v = p.get(k);
    if (v) v.split('~').filter(Boolean).forEach(x => target.add(x));
  };
  if (p.get('g')) EX.grain = p.get('g');
  if (p.get('p')) EX.period = p.get('p');
  if (p.get('m')) EX.metric = p.get('m');
  if (p.get('c')) EX.country = p.get('c');
  if (p.get('q')) EX.q = p.get('q');
  sset('dept', EX.dept); sset('spec', EX.spec); sset('inst', EX.inst); sset('mech', EX.mech);
  if (p.get('mm')) EX.mechMode = p.get('mm');
  if (p.get('rec')) EX.recon = p.get('rec');
  EX.hideRollup = p.get('noroll') === '1';
  if (p.get('mgb') === 'split') EX.mgb = 'split';
  EX.uncoded = p.get('unc') === '1';
  if (p.get('fl')) EX.floor = Number(p.get('fl')) || 5;
  if (p.get('pin')) state.focus = p.get('pin');
  if (p.get('rg')) {
    EX.ranges = p.get('rg').split('~').filter(Boolean).map(t => {
      const b = t.split(':');
      return { key: b[0], min: b[1] === '' ? null : Number(b[1]), max: b[2] === '' ? null : Number(b[2]) };
    }).filter(g => g.key);
  }
  if (p.get('s')) {
    state.sort['t-explorer'] = p.get('s').split('~').filter(Boolean).map(t => {
      const b = t.split(':');
      return { key: b[0], dir: b[1] === 'a' ? 'asc' : 'desc' };
    });
  }
  if (p.get('ps') != null) EX.pageSize = Number(p.get('ps'));
  if (p.get('pg')) EX.page = Number(p.get('pg')) || 1;
  if (p.get('cols')) EX.cols = new Set(p.get('cols').split('~').filter(Boolean));
  if (p.get('tn')) EX.topn = Number(p.get('tn')) || 20;
  if (p.get('sc')) EX.scale = p.get('sc');
  if (p.get('tg')) TR.grain = p.get('tg') === 'pairs' ? 'pairs' : 'inst';
  if (TREND_METRICS.indexOf(p.get('tm')) > -1) TR.metric = p.get('tm');
  if (p.get('ty')) TR.axis = p.get('ty') === 'value' ? 'value' : 'rank';
  if (p.get('tp') != null) TR.pool = Number(p.get('tp')) || 0;
  if (p.get('tk')) TR.n = Number(p.get('tk')) || 10;
  sset('td', TR.dept); sset('tsel', TR.sel);
  if (p.get('tso')) {
    state.sort['t-trend'] = p.get('tso').split('~').filter(Boolean).map(t => {
      const b = t.split(':');
      return { key: b[0], dir: b[1] === 'a' ? 'asc' : 'desc' };
    });
  }
  const put = (id, v) => { const e = document.getElementById(id); if (e && v) e.value = v; };
  put('s-period', p.get('sp')); put('s-split', p.get('ss'));
  put('s-metric', p.get('sm')); put('d-period', p.get('dp'));
  return tab;
}

/* --- departments of surgery --------------------------------------------- */

/* No positional '#' column here. The standing that matters is the one the
   pipeline assigned, and printing a row counter beside it invited exactly the
   misreading this table has to avoid: a roll-up sitting in row 1 while the
   department actually ranked first sits in row 2. */
const SURGERY_SPEC = [
  { key: 'peer_rank', label: 'Rank', fmt: 'rank', asifKey: 'rank_is_as_single' },
  { key: 'display_name', label: 'Institution', fmt: 'name' },
  { key: '__bar', label: '', sortable: false },
  { key: 'total_funding', label: 'Total funding', fmt: 'money' },
  { key: 'award_years', label: 'Award-years', fmt: 'int' },
  { key: 'distinct_projects', label: 'Projects', fmt: 'int' },
  { key: 'r01_funding', label: 'R01 funding', fmt: 'money' },
  { key: 'r01_award_years', label: 'R01 award-years', fmt: 'int' },
  { key: 'funded_investigators', label: 'Investigators', fmt: 'int' },
];

/* A roll-up is held out of the peer rank on purpose. Rather than leave its
   Rank cell empty, the position it would hold as a single department is shown
   there and flagged, so the two readings are never confused for each other. */
function withPeerRank(r) {
  const asSingle = r.rank == null && r.is_rollup === 1 && r.rank_if_single_entity != null;
  return Object.assign({}, r, {
    peer_rank: r.rank != null ? Number(r.rank)
      : (asSingle ? Number(r.rank_if_single_entity) : null),
    rank_is_as_single: asSingle ? 1 : 0,
  });
}

function renderSurgery() {
  const period = document.getElementById('s-period').value;
  const floor = SURG_FLOOR;
  const metric = document.getElementById('s-metric').value;
  const rows = applySort(
    surgView(unpack(state.core[`mgb_${period}_${floor}`])).map(withPeerRank),
    't-surgery', metric);

  state.view.surgery = {
    rows, grain: 'surgery', period,
    keys: ['canonical_org_id', 'display_name', 'peer_rank', 'rank_is_as_single',
      'total_funding', 'award_years',
      'distinct_projects', 'r01_funding', 'r01_award_years', 'funded_investigators',
      'evidence_basis'],
  };

  const nRanked = rows.length ? (rows[0].n_ranked != null
    ? Number(rows[0].n_ranked) : rows.filter(r => r.is_ranked !== 0).length) : 0;
  const nRoll = rows.filter(r => r.is_rollup === 1).length;
  // Combined view hides the member hospitals but not their ranks, so the Rank
  // column has gaps in it. Say why, rather than leave a reader to wonder
  // whether the numbering is broken.
  const hidden = nRanked - rows.filter(r => r.is_ranked !== 0).length;
  document.getElementById('s-count').innerHTML =
    `<strong>${int(nRanked)}</strong> ranked departments`
    + (nRoll ? ` · ${int(nRoll)} roll-up row${nRoll > 1 ? 's' : ''} shown at the position `
      + `${nRoll > 1 ? 'they would hold' : 'it would hold'} as one department `
      + `<span class="warnink">${AS_SINGLE_MARK}</span>, not ranked` : '')
    + (hidden > 0 ? ` · ${int(hidden)} member hospital${hidden > 1 ? 's are' : ' is'} inside `
      + `${nRoll > 1 ? 'those roll-ups' : 'that roll-up'} and hidden here, which is why the rank `
      + 'column skips numbers — switch to Split to see them' : '');
  const spec = SURGERY_SPEC.map(c => Object.assign({}, c));
  const bar = spec.find(c => c.key === '__bar');
  const mi = spec.findIndex(c => c.key === metric);
  if (mi > -1 && bar) { spec.splice(mi, 1); spec.splice(3, 0, spec.find(c => c.key === metric) || {}); }
  renderTable('t-surgery', rows, SURGERY_SPEC.map(c => Object.assign({}, c)), {
    barKey: metric,
    keyFn: r => r.canonical_org_id,
    flagFn: r => (isRecon(r) ? 'recon' : null),
    colorFn: r => colorFor(r.canonical_org_id),
    onSort: renderSurgery,
  });

  renderFocusStrip(rows, metric);
}

/* The strip above the table pins one institution so a reader can find their own
   without scrolling. Any institution in the table can be pinned; the site does
   not privilege one. The choice persists in the URL like every other control. */
function renderFocusStrip(rows, metric) {
  const sel = document.getElementById('s-focus');
  const ranked = rows.slice().sort((a, b) => b.total_funding - a.total_funding);
  const current = (state.focus && ranked.some(r => r.canonical_org_id === state.focus))
    ? state.focus : (ranked[0] || {}).canonical_org_id;
  state.focus = current;

  if (sel) {
    const want = ranked.map(r => `${r.canonical_org_id}\u0000${r.display_name}`).join('|');
    if (sel.dataset.built !== want) {
      sel.innerHTML = ranked.map(r =>
        `<option value="${esc(r.canonical_org_id)}">${esc(r.display_name)}</option>`).join('');
      sel.dataset.built = want;
    }
    sel.value = current;
  }

  const r = ranked.find(x => x.canonical_org_id === current);
  const strip = document.getElementById('focus-stats');
  if (!r) { strip.innerHTML = ''; return; }

  /* The standing comes from the pipeline, not from this row's position in the
     list on screen. Counting positions numbered roll-ups as if they were
     departments and moved with whatever happened to be filtered out. */
  const denom = r.n_ranked != null ? Number(r.n_ranked)
    : ranked.filter(x => x.is_ranked !== 0).length;
  let standing;
  if (r.rank != null) {
    standing = `#${int(r.rank)} of ${int(denom)}`;
  } else if (r.is_rollup === 1 && r.rank_if_single_entity != null) {
    standing = `#${int(r.rank_if_single_entity)}${asifMark()} of ${int(denom)}`;
  } else {
    standing = '—';
  }
  const f = fmtFor(metric);
  const show = f === 'money' ? money : f === 'pct' ? pct : f === 'ratio' ? ratio : int;
  const recon = isRecon(r);
  const note = [recon ? 'lower bound' : '', r.is_rollup === 1 ? 'roll-up, not ranked' : '']
    .filter(Boolean).join(' · ');
  strip.innerHTML = [
    `<div class="stat ${recon ? 'recon' : ''}">
       <div class="v">${standing}</div>
       <div class="k">${esc(r.display_name)}${note ? ' · ' + note : ''}</div>
     </div>`,
    `<div class="stat"><div class="v">${money(r.total_funding)}</div>
       <div class="k">Total NIH funding</div></div>`,
    `<div class="stat"><div class="v">${int(r.award_years)}</div>
       <div class="k">Award-years · ${int(r.distinct_projects)} projects</div></div>`,
    `<div class="stat"><div class="v">${int(r.r01_award_years)}</div>
       <div class="k">R01 award-years · ${money(r.r01_funding)}</div></div>`,
    `<div class="stat"><div class="v">${show(r[metric])}</div>
       <div class="k">${esc(METRIC_LABEL[metric] || metric)}</div></div>`,
  ].join('');
}

/* --- pooled departments ------------------------------------------------- */

function renderDept() {
  const period = document.getElementById('d-period').value;
  let rows = unpack(state.core[`dept_${period}`]).filter(r => !NON_DEPT.has(r.specialty));
  rows = applySort(rows, 't-dept', 'total_funding');
  state.view.dept = {
    rows, grain: 'dept', period,
    keys: ['nih_org_dept', 'specialty', 'total_funding', 'award_years', 'distinct_projects',
      'r01_funding', 'funded_investigators', 'funding_per_investigator', 'r01_share_of_funding'],
  };
  renderTable('t-dept', rows, [
    { key: '__rank', label: '#', sortable: false },
    { key: 'nih_org_dept', label: 'Department', fmt: 'dept' },
    { key: '__bar', label: '', sortable: false },
    { key: 'total_funding', label: 'Total funding', fmt: 'money' },
    { key: 'award_years', label: 'Award-years', fmt: 'int' },
    { key: 'distinct_projects', label: 'Projects', fmt: 'int' },
    { key: 'r01_funding', label: 'R01 funding', fmt: 'money' },
    { key: 'funded_investigators', label: 'Investigators', fmt: 'int' },
    { key: 'funding_per_investigator', label: '$ / investigator', fmt: 'money' },
    { key: 'r01_share_of_funding', label: 'R01 share', fmt: 'pct' },
  ], { barKey: 'total_funding', onSort: renderDept });
}

/* --- interactive charts -------------------------------------------------- */

const PERIOD_LABEL = {
  FY2025: 'FY2025', FY2024_FY2025: 'FY2024–FY2025', FY2021_FY2025: 'FY2021–FY2025',
};

function renderSurgeryChart() {
  const period = document.getElementById('s-period').value;
  const floor = SURG_FLOOR;
  const metric = document.getElementById('s-metric').value;
  const topn = Number(document.getElementById('s-topn').value) || 0;
  const scale = document.getElementById('s-scale').value;
  let rows = surgView(unpack(state.core[`mgb_${period}_${floor}`]))
    .filter(r => r[metric] != null)
    .sort((a, b) => b[metric] - a[metric]);
  if (topn) rows = rows.slice(0, topn);

  const fmt = fmtFn(fmtFor(metric));

  document.getElementById('ch1-title').textContent =
    'Departments of surgery by ' + (METRIC_LABEL[metric] || metric).toLowerCase();
  // Hatching marks the evidence tier, not one institution. Naming only MGB here
  // was accurate when it was the only reconstructed row and became a false
  // statement the moment its uncoded peers were reconstructed too.
  const nRecon = rows.filter(isRecon).length;
  const nRollShown = rows.filter(r => r.is_rollup === 1).length;
  document.getElementById('ch1-sub').textContent =
    `${PERIOD_LABEL[period]} · ${nRecon} of these ${rows.length} bars are hatched: NIH codes no `
    + `department for that recipient, so it is reconstructed from publication affiliations and is `
    + `a lower bound`
    // This chart carries no rank labels, so the roll-up caveat has to be stated
    // rather than encoded in a marker the way the table does it.
    + (nRollShown ? ` · ${nRollShown === 1 ? 'one bar is' : nRollShown + ' bars are'} a roll-up `
      + `over two or more hospitals, drawn in order of size but holding no rank` : '')
    + ` · hover any bar for detail`;

  RMGBCharts.barChart(document.getElementById('ch-surgery'), rows.map(r => ({
    label: r.display_name || r.canonical_name,
    value: r[metric],
    color: colorFor(r.canonical_org_id),
    hatched: isRecon(r),
    sub: r.evidence_basis,
    detail: `${int(r.award_years)} award-years · ${int(r.distinct_projects)} projects · `
      + `${money(r.r01_funding)} R01`,
  })), { fmt, labelW: 230, scale: scale === 'log' ? 'log' : 'lin' });
}

function renderMechChart() {
  const period = document.getElementById('s-period').value;
  const rows = unpack(state.core[`dept_${period}`]);
  // Mechanism mix is only carried at the pooled-department grain in core.json;
  // the surgery-department mix comes from the pairs file once it is loaded.
  const pairs = state.pairs[period];
  const src = pairs
    ? pairs.filter(r => r.nih_org_dept === 'SURGERY' && r.org_country === 'UNITED STATES'
        && !mgbHidden(r, surgSplit()))
      .sort((a, b) => b.total_funding - a.total_funding).slice(0, 14)
    : rows.filter(r => r.nih_org_dept === 'SURGERY');
  document.getElementById('ch2-sub').textContent =
    `${PERIOD_LABEL[period]} · departments NIH department-codes · hover a segment for its share`;
  RMGBCharts.stackedChart(document.getElementById('ch-mech'), src.map(r => ({
    label: r.display_name || r.canonical_name || r.nih_org_dept,
    hatched: isRecon(r),
    parts: {
      R01: r.funding_R01, R_OTHER: r.funding_R_OTHER, U: r.funding_U, P: r.funding_P,
      K: r.funding_K, T: r.funding_T, F: r.funding_F, OTHER: r.funding_OTHER,
    },
  })), { fmt: money, labelW: 230, legendMount: document.getElementById('lg-mech') });
}

function renderPeriodChart() {
  const floor = SURG_FLOOR;
  const byPeriod = {};
  ['FY2025', 'FY2024_FY2025', 'FY2021_FY2025'].forEach(p => {
    byPeriod[p] = {};
    surgView(unpack(state.core[`mgb_${p}_${floor}`])).forEach(r => {
      byPeriod[p][r.canonical_org_id] = r;
    });
  });
  const base = surgView(unpack(state.core[`mgb_FY2021_FY2025_${floor}`]))
    .sort((a, b) => b.total_funding - a.total_funding).slice(0, 12);

  RMGBCharts.groupedChart(document.getElementById('ch-periods'), base.map(r => ({
    label: r.display_name || r.canonical_name,
    hatched: isRecon(r),
    values: {
      FY2025: (byPeriod.FY2025[r.canonical_org_id] || {}).total_funding || 0,
      FY2024_FY2025: (byPeriod.FY2024_FY2025[r.canonical_org_id] || {}).total_funding || 0,
      FY2021_FY2025: (byPeriod.FY2021_FY2025[r.canonical_org_id] || {}).total_funding || 0,
    },
  })), [
    { key: 'FY2025', label: 'FY2025', color: '#7fbfd8' },
    { key: 'FY2024_FY2025', label: 'FY2024–FY2025', color: '#2e6b9e' },
    { key: 'FY2021_FY2025', label: 'FY2021–FY2025', color: '#1b3a5c' },
  ], { fmt: money, labelW: 230, legendMount: document.getElementById('lg-periods') });
}

/* FY2024 → FY2025 movement. NIH publishes cumulative windows, not a bare
   FY2024 table, so FY2024 is the two-year figure minus FY2025 — the same
   subtraction the published matplotlib figure does. */
function renderChangeChart() {
  const floor = SURG_FLOOR;
  const metric = document.getElementById('ch4-metric').value;
  const topn = Number(document.getElementById('ch4-topn').value) || 0;

  const one = {}, two = {};
  surgView(unpack(state.core[`mgb_FY2025_${floor}`]))
    .forEach(r => { one[r.canonical_org_id] = r; });
  surgView(unpack(state.core[`mgb_FY2024_FY2025_${floor}`]))
    .forEach(r => { two[r.canonical_org_id] = r; });

  const ids = Array.from(new Set(Object.keys(one).concat(Object.keys(two))));
  let rows = ids.map(id => {
    const a = one[id] || {}, b = two[id] || {};
    const fy25 = Number(a[metric]) || 0;
    const fy24 = (Number(b[metric]) || 0) - fy25;
    const ref = a.canonical_org_id ? a : b;
    return {
      id,
      label: ref.display_name || ref.canonical_name || id,
      value: fy25 - fy24,
      fy24, fy25,
      hatched: isRecon(ref),
      color: colorFor(id),
    };
  }).filter(r => r.value !== 0);

  rows.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  if (topn) rows = rows.slice(0, topn);

  const f = fmtFn(fmtFor(metric));
  const up = rows.filter(r => r.value > 0).length;
  document.getElementById('ch4-title').textContent =
    'Change in ' + (METRIC_LABEL[metric] || metric).toLowerCase() + ', FY2024 to FY2025';
  document.getElementById('ch4-sub').textContent =
    `${rows.length} departments, largest absolute movement first · ${up} up, ${rows.length - up} down`
    + ' · hover any bar for both years';

  RMGBCharts.divergingChart(document.getElementById('ch-change'), rows.map(r => ({
    label: r.label,
    value: r.value,
    color: r.value < 0 ? '#b8352c' : colorFor(r.id),
    hatched: r.hatched,
    detail: `FY2024 ${f(r.fy24)} → FY2025 ${f(r.fy25)}`,
  })), { fmt: f, labelW: 230 });
}

/* The agreement tables are produced by a stage that may not have run yet, so
   this renders a stated "not computed" panel rather than an empty box. */
function renderMethodChart() {
  const mount = document.getElementById('ch-method');
  const legend = document.getElementById('lg-method');
  if (!mount) return;
  const rows = unpack(state.core.agreement_by_institution);
  if (!rows.length) {
    RMGBCharts.groupedChart(mount, [], [], {
      legendMount: legend,
      empty: 'Not yet computed. The surgery agreement stage populates this; until it has run '
        + 'there is nothing to compare, and nothing is shown in its place.',
    });
    document.getElementById('ch5-sub').textContent =
      'Publication-derived beside NIH ORG_DEPT, for the institutions where both exist.';
    return;
  }

  // Prefer dollars if the stage carries them; fall back to award-years.
  const hasMoney = rows[0].nih_surgical_funding != null;
  const nihKey = hasMoney ? 'nih_surgical_funding' : 'nih_surgical';
  const pubKey = hasMoney ? 'pub_surgical_funding' : 'pub_surgical';
  const sortKey = document.getElementById('ch5-sort').value;
  const topn = Number(document.getElementById('ch5-topn').value) || 0;

  let src = rows.slice().sort((a, b) => {
    const k = sortKey === 'pub_surgical' ? pubKey : sortKey === 'nih_surgical' ? nihKey : sortKey;
    return (Number(b[k]) || 0) - (Number(a[k]) || 0);
  });
  if (topn) src = src.slice(0, topn);

  document.getElementById('ch5-sub').textContent =
    `Publication-derived beside NIH ORG_DEPT · ${src.length} institutions where both exist · `
    + (hasMoney ? 'surgical funding' : 'surgical award-years')
    + ' · institutions NIH does not department-code cannot appear here at all, which is the finding';

  RMGBCharts.groupedChart(mount, src.map(r => ({
    label: r.display_name || r.canonical_name,
    hatched: isRecon(r),
    values: { pub: Number(r[pubKey]) || 0, nih: Number(r[nihKey]) || 0 },
  })), [
    { key: 'pub', label: 'Publication-derived (this site)', color: '#7a1f5c' },
    { key: 'nih', label: 'NIH ORG_DEPT', color: '#2e6b9e' },
  ], {
    fmt: hasMoney ? money : int,
    labelW: 260,
    legendMount: legend,
  });
}

const TIER_SERIES = [
  { key: 'UNRESOLVED', label: 'No department evidence in NIH data', color: '#b8352c' },
  { key: 'B_NIH_LINKED', label: "Inferred from the same PI's NIH department elsewhere", color: '#7fbfd8' },
  { key: 'A_NIH_ORG_DEPT', label: 'NIH supplies a department code', color: '#1b3a5c' },
];

function renderCoverageChart() {
  const mount = document.getElementById('ch-coverage');
  if (!mount) return;
  const measure = document.getElementById('ch6-measure').value;
  const rows = unpack(state.core.coverage);
  const years = Array.from(new Set(rows.map(r => r.fiscal_year))).sort();
  const groups = years.map(y => {
    const values = {};
    rows.filter(r => r.fiscal_year === y).forEach(r => { values[r.dept_tier] = r[measure]; });
    return { label: 'FY' + y, values };
  });

  const last = groups[groups.length - 1];
  const lastTotal = last ? TIER_SERIES.reduce((s, t) => s + (Number(last.values[t.key]) || 0), 0) : 0;
  const lastGap = last && lastTotal ? ((Number(last.values.UNRESOLVED) || 0) / lastTotal) * 100 : 0;
  document.getElementById('ch6-sub').textContent =
    (measure === 'funding' ? 'NIH funding' : 'Award-years')
    + ` by department-evidence tier · in ${last ? last.label : 'the latest year'} `
    + `${lastGap.toFixed(0)}% carries no department evidence at all · hover a band for its share`;

  RMGBCharts.columnStackChart(mount, groups, TIER_SERIES, {
    fmt: measure === 'funding' ? money : int,
    height: 340,
    legendMount: document.getElementById('lg-coverage'),
  });
}

async function renderPairsChart() {
  const period = document.getElementById('d-period').value;
  const topn = Number(document.getElementById('p-topn').value) || 25;
  const rows = (await loadPairs(period))
    .filter(r => !NON_DEPT.has(r.specialty) && r.org_country === 'UNITED STATES'
      && !mgbHidden(r, EX.mgb === 'split'))
    .sort((a, b) => b.total_funding - a.total_funding)
    .slice(0, topn);
  document.getElementById('ch3-sub').textContent =
    `${PERIOD_LABEL[period]} · surgery departments highlighted · click a bar to open it in the `
    + `Explorer · hover for detail`;
  RMGBCharts.barChart(document.getElementById('ch-pairs'), rows.map(r => ({
    label: `${r.display_name || r.canonical_name} — ${r.nih_org_dept}`,
    value: r.total_funding,
    color: r.nih_org_dept === 'SURGERY' ? '#7a1f5c' : colorFor(r.canonical_org_id),
    hatched: isRecon(r),
    sub: r.nih_org_dept,
    detail: `${int(r.award_years)} award-years · ${int(r.funded_investigators)} investigators`,
    _row: r,
  })), {
    fmt: money,
    labelW: 330,
    onSelect: d => {
      const r = d._row;
      EX.grain = 'pairs';
      EX.period = period;
      EX.inst = new Set([r.canonical_org_id]);
      EX.dept = new Set();
      EX.page = 1;
      syncControls();
      showTab('explorer');
      renderExplorer();
    },
  });
}

/* --- static panels ------------------------------------------------------ */

function renderCoverage() {
  const label = {
    A_NIH_ORG_DEPT: 'NIH supplies a department code',
    B_NIH_LINKED: "Inferred from the same PI's NIH department elsewhere",
    UNRESOLVED: 'No department evidence in NIH data',
  };
  const rows = unpack(state.core.coverage).map(r =>
    Object.assign({}, r, { tier: label[r.dept_tier] || r.dept_tier }));
  renderTable('t-coverage', rows, [
    { key: 'fiscal_year', label: 'Fiscal year', sortable: false },
    { key: 'tier', label: 'Evidence tier', sortable: false },
    { key: 'award_years', label: 'Award-years', fmt: 'int', sortable: false },
    { key: 'funding', label: 'Funding', fmt: 'money', sortable: false },
    { key: 'pct_funding', label: '% of funding', sortable: false },
  ], {});
}

function renderValidation() {
  const rows = unpack(state.core.validation);
  const mount = document.getElementById('t-validation');
  mount.className = '';
  mount.innerHTML = '<table><thead><tr><th class="nosort">Check</th><th class="nosort">Result</th>'
    + '<th class="nosort">Value</th><th class="nosort">What it tests</th></tr></thead><tbody>'
    + rows.map(r => `<tr>
        <td><code>${esc(r.check)}</code></td>
        <td class="${r.passed ? 'pass' : 'fail'}">${r.passed ? 'PASS' : 'FAIL'}</td>
        <td class="num">${esc(r.value)}</td>
        <td>${esc(r.detail)}</td></tr>`).join('')
    + '</tbody></table>';
}

function renderSources() {
  const mount = document.getElementById('t-sources');
  mount.className = '';
  mount.innerHTML = '<table><thead><tr><th class="nosort">File</th><th class="nosort">FY</th>'
    + '<th class="nosort">Bytes</th><th class="nosort">SHA-256</th><th class="nosort">Downloaded</th>'
    + '</tr></thead><tbody>'
    + (state.core.sources || []).map(s => `<tr>
        <td><a href="${esc(s.source_url)}">${esc(s.file)}</a></td>
        <td class="num">${esc(s.fiscal_year)}</td>
        <td class="num">${int(s.bytes)}</td>
        <td class="num" title="${esc(s.sha256)}">${esc(String(s.sha256).slice(0, 16))}…</td>
        <td class="num">${esc(String(s.downloaded_at).slice(0, 10))}</td></tr>`).join('')
    + '</tbody></table>';
}

const DOWNLOADS = [
  'rank_institution_FY2025.csv', 'rank_institution_FY2024_FY2025.csv', 'rank_institution_FY2021_FY2025.csv',
  'rank_department_FY2025.csv', 'rank_department_FY2024_FY2025.csv', 'rank_department_FY2021_FY2025.csv',
  'rank_institution_department_FY2025.csv', 'rank_institution_department_FY2024_FY2025.csv',
  'rank_institution_department_FY2021_FY2025.csv',
  'surgery_ranking_with_mgb_FY2025_corroborated.csv',
  'surgery_ranking_with_mgb_FY2021_FY2025_corroborated.csv',
  'mgb_surgery_summary.csv', 'mgb_departments_all.csv',
  // The audit trail for the reconstructed rows, produced by the rule that
  // ships. The file that used to sit here was the output of a retired
  // per-award matcher: it covered about a third of the award-years published
  // and listed two hundred that are not in the total at all.
  'reconstructed_department_evidence.csv',
  'coverage_department_evidence.csv', 'sensitivity_surgical_definition.csv', 'validation_report.csv',
];

function renderAgreement() {
  const mk = (mountId, key, spec, empty) => {
    const mount = document.getElementById(mountId);
    if (!mount) return;
    const rows = unpack(state.core[key]);
    if (!rows.length) { mount.className = 'loading'; mount.textContent = empty; return; }
    renderTable(mountId, rows, spec, { onSort: renderAgreement });
  };
  mk('t-agree-overall', 'agreement_overall', [
    { key: 'scope', label: 'Scope', sortable: false },
    { key: 'comparable_award_years', label: 'Comparable award-years', fmt: 'int', sortable: false },
    { key: 'nih_surgical', label: 'NIH says surgical', fmt: 'int', sortable: false },
    { key: 'pub_surgical', label: 'Publications say surgical', fmt: 'int', sortable: false },
    { key: 'sensitivity_pct', label: 'Sensitivity', fmt: 'pct', sortable: false },
    { key: 'precision_pct', label: 'Precision', fmt: 'pct', sortable: false },
    { key: 'cohens_kappa', label: "Cohen's κ", fmt: 'ratio', sortable: false },
  ], 'Run the surgery stage to populate this.');

  mk('t-agree-inst', 'agreement_by_institution', [
    { key: '__rank', label: '#', sortable: false },
    { key: 'display_name', label: 'Institution', fmt: 'name' },
    { key: 'nih_surgical', label: 'NIH surgical', fmt: 'int' },
    { key: 'pub_surgical', label: 'Publication surgical', fmt: 'int' },
    { key: 'both', label: 'Both', fmt: 'int' },
    { key: 'nih_only', label: 'NIH only', fmt: 'int' },
    { key: 'publication_only', label: 'Publication only', fmt: 'int' },
    { key: 'sensitivity_pct', label: 'Sensitivity', fmt: 'pct' },
    { key: 'precision_pct', label: 'Precision', fmt: 'pct' },
    { key: 'cohens_kappa', label: 'κ', fmt: 'ratio' },
  ], 'Run the surgery stage to populate this.');

  mk('t-agree-none', 'agreement_uncomparable', [
    { key: 'display_name', label: 'Institution', fmt: 'name', sortable: false },
    { key: 'award_years', label: 'Contact-PI award-years', fmt: 'int', sortable: false },
    { key: 'publication_surgical', label: 'Publication says surgical', fmt: 'int', sortable: false },
    { key: 'note', label: 'Why', sortable: false },
  ], 'Run the surgery stage to populate this.');
}

function renderDownloads() {
  document.getElementById('dl-list').innerHTML =
    DOWNLOADS.map(f => `<a href="tables/${f}" download>${f}</a>`).join('');
}

function renderSurgeryCharts() {
  renderSurgeryChart();
  renderMechChart();
  renderPeriodChart();
  renderChangeChart();
}

/* --- tabs and boot ------------------------------------------------------ */

function showTab(id) {
  const buttons = Array.from(document.querySelectorAll('nav button'));
  const b = buttons.find(x => x.dataset.tab === id) || buttons[0];
  buttons.forEach(x => {
    const on = x === b;
    x.setAttribute('aria-selected', String(on));
    x.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('main section').forEach(s =>
    s.classList.toggle('active', s.id === b.dataset.tab));
  activeTab = b.dataset.tab;
  writeHash();
  // A chart drawn inside a display:none section measures zero, so redraw the
  // charts of a tab the first time it is actually visible.
  if (activeTab === 'surgery') renderSurgeryCharts();
  if (activeTab === 'specialties') renderPairsChart();
  if (activeTab === 'agreement') renderMethodChart();
  if (activeTab === 'coverage') renderCoverageChart();
  if (activeTab === 'trends') openTrends();
  if (activeTab === 'explorer' && state.view.explorer) renderExplorerChart(state.view.explorer.rows);
}

/* An empty bump chart teaches nobody anything, so the first time the tab is
   opened with nothing followed it starts on the default preset. A set that
   arrived in the URL is left exactly as it came. */
let trendOpened = false;
async function openTrends() {
  const first = !trendOpened;
  trendOpened = true;
  await renderTrends();
  if (first && !TR.sel.size && state.trend[TR.grain]) {
    applyTrendPreset('top');
    await renderTrends();
  }
}

function initTabs() {
  const buttons = Array.from(document.querySelectorAll('nav button'));
  buttons.forEach((b, i) => {
    b.addEventListener('click', () => { showTab(b.dataset.tab); window.scrollTo({ top: 0 }); });
    b.addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1
        : e.key === 'Home' ? -999 : e.key === 'End' ? 999 : 0;
      if (!d) return;
      e.preventDefault();
      const n = d === -999 ? 0 : d === 999 ? buttons.length - 1
        : (i + d + buttons.length) % buttons.length;
      buttons[n].focus();
      buttons[n].click();
    });
  });
}

function initExplorerControls() {
  const rerender = () => { EX.page = 1; renderExplorer(); };
  document.getElementById('c-grain').addEventListener('change', e => {
    EX.grain = e.target.value;
    EX.cols = null;
    state.sort['t-explorer'] = [];
    rerender();
  });
  document.getElementById('c-period').addEventListener('change', e => { EX.period = e.target.value; rerender(); });
  document.getElementById('c-metric').addEventListener('change', e => { EX.metric = e.target.value; rerender(); });
  document.getElementById('c-country').addEventListener('change', e => { EX.country = e.target.value; rerender(); });
  document.getElementById('c-floor').addEventListener('change', e => { EX.floor = Number(e.target.value); rerender(); });
  document.getElementById('c-pagesize').addEventListener('change', e => {
    EX.pageSize = Number(e.target.value); EX.page = 1; renderExplorer();
  });
  document.getElementById('c-rollup').addEventListener('change', e => { EX.hideRollup = e.target.checked; rerender(); });
  document.getElementById('c-mgb').addEventListener('change', e => {
    EX.mgb = e.target.value === 'split' ? 'split' : 'combined';
    // The row set changes, so a page deep into the old one is meaningless.
    EX.page = 1;
    rerender();
  });
  document.getElementById('c-uncoded').addEventListener('change', e => { EX.uncoded = e.target.checked; rerender(); });
  document.querySelectorAll('input[name="recon"]').forEach(r =>
    r.addEventListener('change', () => { if (r.checked) { EX.recon = r.value; rerender(); } }));
  document.querySelectorAll('input[name="mechmode"]').forEach(r =>
    r.addEventListener('change', () => { if (r.checked) { EX.mechMode = r.value; rerender(); } }));
  document.getElementById('ex-topn').addEventListener('change', e => {
    EX.topn = Number(e.target.value);
    if (state.view.explorer) renderExplorerChart(state.view.explorer.rows);
    writeHash();
  });
  document.getElementById('ex-scale').addEventListener('change', e => {
    EX.scale = e.target.value;
    if (state.view.explorer) renderExplorerChart(state.view.explorer.rows);
    writeHash();
  });

  let t;
  document.getElementById('c-search').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { EX.q = e.target.value; EX.page = 1; renderExplorer(); }, 180);
  });

  document.getElementById('rg-add').addEventListener('click', () => {
    const key = document.getElementById('rg-metric').value;
    const lo = parseNum(document.getElementById('rg-min').value);
    const hi = parseNum(document.getElementById('rg-max').value);
    if (Number.isNaN(lo) || Number.isNaN(hi)) { toast('Could not read that number.'); return; }
    if (lo == null && hi == null) { toast('Give at least one bound.'); return; }
    EX.ranges.push({ key, min: lo, max: hi });
    document.getElementById('rg-min').value = '';
    document.getElementById('rg-max').value = '';
    rerender();
  });
  ['rg-min', 'rg-max'].forEach(id => document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); document.getElementById('rg-add').click(); }
  }));

  const toggle = document.getElementById('ex-toggle');
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!open));
    toggle.textContent = open ? 'Show filters' : 'Hide filters';
    document.getElementById('ex-filters').hidden = open;
  });

  document.getElementById('ex-chips').addEventListener('click', e => {
    const c = e.target.closest('.chip');
    if (!c) return;
    if (c.id === 'chip-clear') { clearAllFilters(); return; }
    const k = c.dataset.kind, v = c.dataset.v;
    if (k === 'dept') EX.dept.delete(v);
    else if (k === 'spec') EX.spec.delete(v);
    else if (k === 'inst') EX.inst.delete(v);
    else if (k === 'mech') EX.mech.clear();
    else if (k === 'recon') EX.recon = 'all';
    else if (k === 'rollup') EX.hideRollup = false;
    else if (k === 'uncoded') EX.uncoded = false;
    else if (k === 'country') EX.country = 'UNITED STATES';
    else if (k === 'q') EX.q = '';
    else if (k === 'floor') EX.floor = 1;
    else if (k === 'range') EX.ranges.splice(Number(v), 1);
    syncControls();
    EX.page = 1;
    renderExplorer();
  });
}

function initGlobalButtons() {
  document.addEventListener('click', e => {
    const ex = e.target.closest('[data-export]');
    if (ex) { exportView(ex.dataset.export, ex.dataset.table || 'explorer'); return; }
    const ch = e.target.closest('[data-chart]');
    if (ch) {
      const mount = document.getElementById(ch.dataset.chart);
      if (!mount) return;
      const name = (ch.dataset.name || 'chart') + '_' + stamp();
      if (ch.dataset.fmt === 'png') RMGBCharts.exportPNG(mount, name);
      else RMGBCharts.exportSVG(mount, name);
      toast('Chart exported as ' + ch.dataset.fmt.toUpperCase() + '.');
    }
  });
}

async function boot() {
  ['t-explorer', 't-surgery', 't-dept', 't-coverage', 't-validation', 't-sources']
    .forEach(id => skeleton(id, 8));

  const tab = readHash();
  syncControls();

  // Fetch the index and the default pair file together rather than in series.
  const corePromise = fetch('data/core.json').then(r => r.json());
  const pairPromise = fetch(`data/pairs_${EX.period}.json`).then(r => r.json())
    .then(j => { state.pairs[EX.period] = unpack(j); })
    .catch(() => { /* renderExplorer will retry through loadPairs */ });
  state.core = await corePromise;

  initExplorerControls();
  initTrendControls();
  initGlobalButtons();
  initTabs();

  ['s-period', 's-split', 's-metric', 's-topn', 's-scale'].forEach(id => {
    const n = document.getElementById(id);
    if (n) n.addEventListener('change', () => { renderSurgery(); renderSurgeryCharts(); writeHash(); });
  });
  // The pinned institution is read from the control rather than recomputed, so
  // selecting one has to set state.focus before the strip re-renders.
  const focusSel = document.getElementById('s-focus');
  if (focusSel) focusSel.addEventListener('change', () => {
    state.focus = focusSel.value;
    renderSurgery(); renderSurgeryCharts(); writeHash();
  });
  document.getElementById('d-period').addEventListener('change', () => {
    renderDept(); renderPairsChart(); writeHash();
  });
  document.getElementById('p-topn').addEventListener('change', () => { renderPairsChart(); writeHash(); });
  ['ch4-metric', 'ch4-topn'].forEach(id =>
    document.getElementById(id).addEventListener('change', renderChangeChart));
  ['ch5-sort', 'ch5-topn'].forEach(id =>
    document.getElementById(id).addEventListener('change', renderMethodChart));
  document.getElementById('ch6-measure').addEventListener('change', renderCoverageChart);

  let rt;
  window.addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(() => {
      if (activeTab === 'surgery') renderSurgeryCharts();
      if (activeTab === 'specialties') renderPairsChart();
      if (activeTab === 'agreement') renderMethodChart();
      if (activeTab === 'coverage') renderCoverageChart();
      if (activeTab === 'trends') renderTrends();
      if (activeTab === 'explorer' && state.view.explorer) renderExplorerChart(state.view.explorer.rows);
    }, 200);
  });

  renderSurgery();
  renderDept();
  renderCoverage();
  renderValidation();
  renderSources();
  renderAgreement();
  renderDownloads();

  await pairPromise;
  showTab(tab);
  await renderExplorer();
  if (activeTab === 'surgery') renderSurgeryCharts();
  if (activeTab === 'specialties') await renderPairsChart();
  if (activeTab === 'agreement') renderMethodChart();
  if (activeTab === 'coverage') renderCoverageChart();
  if (activeTab === 'trends') await openTrends();

  document.getElementById('gen').textContent =
    ' Generated ' + String(state.core.generated_at).slice(0, 10) + '.';
}

boot().catch(err => {
  document.querySelectorAll('.loading, .skel').forEach(el => {
    el.className = 'loading';
    el.textContent = 'Could not load the data files. ' + err;
  });
});
