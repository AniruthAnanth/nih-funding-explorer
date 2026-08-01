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
// Reported only as the combined Mass General Brigham entity, never split.
const SPLIT_OUT = new Set(['MGH', 'BWH', 'MGB_SYSTEM']);
const BAR_DEFAULT = '#9ba7b4';
const LOWER_BOUND_NOTE =
  'Reconstructed from dated PubMed author affiliations. A lower bound, not '
  + 'like-for-like with an NIH department-coded row.';

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

const state = { core: null, pairs: {}, sort: {}, expanded: {}, view: {} };

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
  uncoded: false,
  ranges: [],
  page: 1,
  pageSize: 100,
  cols: null,
  topn: 20,
  scale: 'lin',
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
      c.fmt === 'money' || c.fmt === 'int' || c.fmt === 'pct' || c.fmt === 'ratio' ? 'r' : ''].filter(Boolean).join(' ');
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
  // MGH and BWH are reported as the single combined Mass General Brigham
  // entity. The separate rows remain in the downloadable CSVs for audit.
  return rows.filter(r => !SPLIT_OUT.has(r.canonical_org_id));
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
  chk('c-rollup', EX.hideRollup);
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
  const v = which === 'surgery' ? state.view.surgery
    : which === 'dept' ? state.view.dept : state.view.explorer;
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
  const sp = document.getElementById('s-period');
  if (sp && sp.value !== 'FY2021_FY2025') p.set('sp', sp.value);
  const sf = document.getElementById('s-floor');
  if (sf && sf.value !== 'corroborated') p.set('sf', sf.value);
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
  const put = (id, v) => { const e = document.getElementById(id); if (e && v) e.value = v; };
  put('s-period', p.get('sp')); put('s-floor', p.get('sf'));
  put('s-metric', p.get('sm')); put('d-period', p.get('dp'));
  return tab;
}

/* --- departments of surgery --------------------------------------------- */

const SURGERY_SPEC = [
  { key: '__rank', label: '#', sortable: false },
  { key: 'display_name', label: 'Institution', fmt: 'name' },
  { key: '__bar', label: '', sortable: false },
  { key: 'total_funding', label: 'Total funding', fmt: 'money' },
  { key: 'award_years', label: 'Award-years', fmt: 'int' },
  { key: 'distinct_projects', label: 'Projects', fmt: 'int' },
  { key: 'r01_funding', label: 'R01 funding', fmt: 'money' },
  { key: 'r01_award_years', label: 'R01 award-years', fmt: 'int' },
  { key: 'funded_investigators', label: 'Investigators', fmt: 'int' },
];

function renderSurgery() {
  const period = document.getElementById('s-period').value;
  const floor = document.getElementById('s-floor').value;
  const metric = document.getElementById('s-metric').value;
  const rows = applySort(
    unpack(state.core[`mgb_${period}_${floor}`]).filter(r => !SPLIT_OUT.has(r.canonical_org_id)),
    't-surgery', metric);

  state.view.surgery = {
    rows, grain: 'surgery', period,
    keys: ['canonical_org_id', 'display_name', 'total_funding', 'award_years',
      'distinct_projects', 'r01_funding', 'r01_award_years', 'funded_investigators',
      'evidence_basis'],
  };

  document.getElementById('s-count').textContent = `${rows.length} departments`;
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
  const rank = ranked.findIndex(x => x.canonical_org_id === current) + 1;
  const f = fmtFor(metric);
  const show = f === 'money' ? money : f === 'pct' ? pct : f === 'ratio' ? ratio : int;
  const recon = isRecon(r);
  strip.innerHTML = [
    `<div class="stat ${recon ? 'recon' : ''}">
       <div class="v">#${rank} of ${ranked.length}</div>
       <div class="k">${esc(r.display_name)}${recon ? ' · lower bound' : ''}</div>
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
  const floor = document.getElementById('s-floor').value;
  const metric = document.getElementById('s-metric').value;
  const topn = Number(document.getElementById('s-topn').value) || 0;
  const scale = document.getElementById('s-scale').value;
  let rows = unpack(state.core[`mgb_${period}_${floor}`])
    .filter(r => !SPLIT_OUT.has(r.canonical_org_id) && r[metric] != null)
    .sort((a, b) => b[metric] - a[metric]);
  if (topn) rows = rows.slice(0, topn);

  const fmt = fmtFn(fmtFor(metric));

  document.getElementById('ch1-title').textContent =
    'Departments of surgery by ' + (METRIC_LABEL[metric] || metric).toLowerCase();
  document.getElementById('ch1-sub').textContent =
    `${PERIOD_LABEL[period]} · Mass General Brigham is hatched: reconstructed from publication `
    + `affiliations, a lower bound · hover any bar for detail`;

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
        && !SPLIT_OUT.has(r.canonical_org_id))
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
  const floor = document.getElementById('s-floor').value;
  const byPeriod = {};
  ['FY2025', 'FY2024_FY2025', 'FY2021_FY2025'].forEach(p => {
    byPeriod[p] = {};
    unpack(state.core[`mgb_${p}_${floor}`]).filter(r => !SPLIT_OUT.has(r.canonical_org_id)).forEach(r => {
      byPeriod[p][r.canonical_org_id] = r;
    });
  });
  const base = unpack(state.core[`mgb_FY2021_FY2025_${floor}`])
    .filter(r => !SPLIT_OUT.has(r.canonical_org_id))
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
  const floor = document.getElementById('s-floor').value;
  const metric = document.getElementById('ch4-metric').value;
  const topn = Number(document.getElementById('ch4-topn').value) || 0;

  const one = {}, two = {};
  unpack(state.core[`mgb_FY2025_${floor}`])
    .filter(r => !SPLIT_OUT.has(r.canonical_org_id)).forEach(r => { one[r.canonical_org_id] = r; });
  unpack(state.core[`mgb_FY2024_FY2025_${floor}`])
    .filter(r => !SPLIT_OUT.has(r.canonical_org_id)).forEach(r => { two[r.canonical_org_id] = r; });

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
      && !SPLIT_OUT.has(r.canonical_org_id))
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
  'mgb_surgery_summary.csv', 'mgb_surgical_award_years_evidence.csv',
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
  if (activeTab === 'explorer' && state.view.explorer) renderExplorerChart(state.view.explorer.rows);
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
  initGlobalButtons();
  initTabs();

  ['s-period', 's-floor', 's-metric', 's-topn', 's-scale'].forEach(id => {
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

  document.getElementById('gen').textContent =
    ' Generated ' + String(state.core.generated_at).slice(0, 10) + '.';
}

boot().catch(err => {
  document.querySelectorAll('.loading, .skel').forEach(el => {
    el.className = 'loading';
    el.textContent = 'Could not load the data files. ' + err;
  });
});
