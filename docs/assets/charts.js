/* Interactive charts, drawn as inline SVG.

   No chart library: these are horizontal bars, stacked bars and a scatter over
   at most a few dozen rows, which is less code to draw directly than to
   configure. Every chart reads the same JSON the tables read, so a chart can
   never disagree with the table beside it.

   House rules, carried over from the published matplotlib figures:
     - no gridlines, only quiet tick labels
     - values printed at the end of the bar rather than read off an axis
     - reconstructed (publication-derived) series are hatched, always */

'use strict';

const SVGNS = 'http://www.w3.org/2000/svg';
const el = (name, attrs, text) => {
  const n = document.createElementNS(SVGNS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
};

const MECH_ORDER = ['R01', 'R_OTHER', 'U', 'P', 'K', 'T', 'F', 'OTHER'];
const MECH_COLORS = {
  R01: '#1b3a5c', R_OTHER: '#2e6b9e', U: '#4e9ac4', P: '#7fbfd8',
  K: '#a8cba0', T: '#d9b36a', F: '#c97c5d', OTHER: '#b9bdc2',
};
const MECH_LABEL = {
  R01: 'R01 (incl. R37)', R_OTHER: 'Other R', U: 'U (cooperative agreements)',
  P: 'P (centers)', K: 'K (career development)', T: 'T (training)',
  F: 'F (fellowships)', OTHER: 'Other mechanisms',
};

const escText = s => String(s == null ? '' : s)
  .replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/* --- shared tooltip ----------------------------------------------------- */

let tip;
function showTip(html, evt) {
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'charttip';
    tip.setAttribute('role', 'status');
    document.body.appendChild(tip);
  }
  tip.innerHTML = html;
  tip.style.display = 'block';
  const pad = 14;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = Math.max(4, x) + 'px';
  tip.style.top = Math.max(4, y) + 'px';
}
function hideTip() { if (tip) tip.style.display = 'none'; }
document.addEventListener('scroll', hideTip, true);

/* Keyboard focus has no pointer coordinates, so anchor the tooltip to the
   focused element instead of the cursor. */
function tipAtElement(html, node) {
  const b = node.getBoundingClientRect();
  showTip(html, { clientX: b.left + Math.min(b.width, 260), clientY: b.top + b.height / 2 });
}

/* --- hatch pattern ------------------------------------------------------ */

let hatchSeq = 0;
function addHatch(svg) {
  const id = 'hatch-' + (++hatchSeq);
  const defs = el('defs');
  const pat = el('pattern', {
    id, width: 7, height: 7, patternTransform: 'rotate(45)',
    patternUnits: 'userSpaceOnUse',
  });
  pat.appendChild(el('rect', { width: 3, height: 7, fill: 'rgba(255,255,255,.6)' }));
  defs.appendChild(pat);
  svg.insertBefore(defs, svg.firstChild);
  return `url(#${id})`;
}

/* --- scales -------------------------------------------------------------- */

/* Log is offered because funding across 400 departments spans four orders of
   magnitude and a linear axis flattens everything below the leaders. Zero and
   negative values have no place on a log axis, so they are pinned to the
   baseline rather than dropped silently. */
function makeScale(max, plotW, mode, min) {
  if (mode === 'log') {
    const lo = Math.max(min || 0, max / 1e4) || 1;
    const a = Math.log10(lo), b = Math.log10(Math.max(max, lo * 10));
    return {
      x: v => (v <= lo ? 0 : ((Math.log10(v) - a) / (b - a)) * plotW),
      ticks: (() => {
        const out = [];
        for (let e = Math.ceil(a); e <= Math.floor(b); e++) out.push(Math.pow(10, e));
        if (out.length < 2) out.push(max);
        return out;
      })(),
    };
  }
  return {
    x: v => (max > 0 ? (Math.max(v, 0) / max) * plotW : 0),
    ticks: [0, max / 4, max / 2, (max * 3) / 4, max],
  };
}

/* --- horizontal ranked bars --------------------------------------------- */

/* rows: [{label, value, id, color, hatched, sub, detail}] already sorted */
function barChart(mount, rows, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!rows.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty
      || 'Nothing to draw. Widen the filters above.')}</p>`;
    return;
  }
  const fmt = opts.fmt || (v => v);
  const rowH = 26, padT = 10, padB = 34;
  const full = Math.max(mount.clientWidth || 900, 560);
  // Long institution-department labels need more room than a bare institution
  // name, and a narrow window needs less, so the label gutter is proportional.
  const labelW = Math.min(opts.labelW || 250, Math.round(full * 0.42));
  const padR = 92;
  const plotW = Math.max(full - labelW - padR, 120);
  const h = rows.length * rowH + padT + padB;
  const values = rows.map(r => Number(r.value) || 0);
  const max = Math.max.apply(null, values) || 1;
  const positive = values.filter(v => v > 0);
  const sc = makeScale(max, plotW, opts.scale, positive.length ? Math.min.apply(null, positive) : 1);

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h,
    role: 'img', 'aria-label': opts.title || 'ranked bar chart',
  });
  const hatchFill = addHatch(svg);

  sc.ticks.forEach(v => {
    const gx = labelW + sc.x(v);
    svg.appendChild(el('line', {
      x1: gx, x2: gx, y1: padT, y2: h - padB, stroke: '#dfe4e8', 'stroke-width': 1,
    }));
    svg.appendChild(el('text', {
      x: gx, y: h - padB + 15, 'text-anchor': 'middle', class: 'axis',
    }, fmt(v)));
  });

  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const bw = Math.max(sc.x(Number(r.value) || 0), 1.5);
    const interactive = !!opts.onSelect;

    const g = el('g', {
      class: 'barrow' + (interactive ? ' clickable' : '') + (r.selected ? ' selected' : ''),
      tabindex: '0', role: interactive ? 'button' : 'img',
      'aria-label': `${r.label}, ${fmt(r.value)}`,
    });

    g.appendChild(el('rect', {
      x: 0, y, width: full, height: rowH, fill: 'transparent', class: 'hitrow',
    }));
    g.appendChild(el('text', {
      x: labelW - 10, y: y + rowH / 2 + 4, 'text-anchor': 'end',
      class: 'barlabel' + (r.hatched ? ' recon' : ''),
    }, clip(r.label, Math.floor(labelW / 6.1))));

    g.appendChild(el('rect', {
      x: labelW, y: y + 5, width: bw, height: rowH - 11, fill: r.color || '#9ba7b4',
    }));
    if (r.hatched) {
      g.appendChild(el('rect', {
        x: labelW, y: y + 5, width: bw, height: rowH - 11, fill: hatchFill,
      }));
    }
    g.appendChild(el('text', {
      x: labelW + bw + 7, y: y + rowH / 2 + 4, class: 'barvalue',
    }, fmt(r.value)));

    const detail = r.detail || '';
    const html = `<strong>${escText(r.label)}</strong>${r.sub ? '<br>' + escText(r.sub) : ''}`
      + `<br><span class="tipv">${escText(fmt(r.value))}</span>`
      + `${detail ? '<br>' + escText(detail) : ''}`
      + (r.hatched ? '<br><em>Reconstructed from publication affiliations — a lower bound</em>' : '')
      + (interactive ? '<br><em>Click to filter the table</em>' : '');
    g.addEventListener('mousemove', evt => showTip(html, evt));
    g.addEventListener('mouseleave', hideTip);
    g.addEventListener('focus', () => tipAtElement(html, g));
    g.addEventListener('blur', hideTip);
    if (interactive) {
      g.addEventListener('click', () => opts.onSelect(r));
      g.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); opts.onSelect(r); }
      });
    }
    svg.appendChild(g);
  });

  mount.appendChild(svg);
}

function clip(s, n) {
  s = String(s == null ? '' : s);
  return s.length > n ? s.slice(0, Math.max(n - 1, 1)) + '…' : s;
}

/* --- stacked bars by mechanism family ----------------------------------- */

function stackedChart(mount, rows, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!rows.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty || 'Nothing to draw.')}</p>`;
    if (opts.legendMount) opts.legendMount.innerHTML = '';
    return;
  }
  const fmt = opts.fmt || (v => v);
  const rowH = 26, padT = 10, padB = 34;
  const full = Math.max(mount.clientWidth || 900, 560);
  const labelW = Math.min(opts.labelW || 250, Math.round(full * 0.42));
  const padR = 90;
  const plotW = Math.max(full - labelW - padR, 120);
  const h = rows.length * rowH + padT + padB;
  const totals = rows.map(r => MECH_ORDER.reduce((s, m) => s + (r.parts[m] || 0), 0));
  const max = Math.max.apply(null, totals) || 1;

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h, role: 'img',
    'aria-label': opts.title || 'funding composition by mechanism family',
  });
  const hatchFill = addHatch(svg);
  for (let i = 0; i <= 4; i++) {
    const gx = labelW + (plotW / 4) * i;
    svg.appendChild(el('line', { x1: gx, x2: gx, y1: padT, y2: h - padB, stroke: '#dfe4e8' }));
    svg.appendChild(el('text', {
      x: gx, y: h - padB + 15, 'text-anchor': 'middle', class: 'axis',
    }, fmt((max / 4) * i)));
  }

  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    let cursor = labelW;
    svg.appendChild(el('text', {
      x: labelW - 10, y: y + rowH / 2 + 4, 'text-anchor': 'end',
      class: 'barlabel' + (r.hatched ? ' recon' : ''),
    }, clip(r.label, Math.floor(labelW / 6.1))));
    MECH_ORDER.forEach(m => {
      const v = r.parts[m] || 0;
      if (v <= 0) return;
      const bw = (v / max) * plotW;
      const seg = el('rect', {
        x: cursor, y: y + 5, width: bw, height: rowH - 11, fill: MECH_COLORS[m],
      });
      const share = totals[i] ? (v / totals[i]) * 100 : 0;
      const html = `<strong>${escText(r.label)}</strong><br>${MECH_LABEL[m]}`
        + `<br><span class="tipv">${escText(fmt(v))}</span> · ${share.toFixed(1)}% of the department`
        + (r.hatched ? '<br><em>Reconstructed lower bound</em>' : '');
      seg.addEventListener('mousemove', evt => showTip(html, evt));
      seg.addEventListener('mouseleave', hideTip);
      svg.appendChild(seg);
      if (r.hatched) {
        const over = el('rect', {
          x: cursor, y: y + 5, width: bw, height: rowH - 11, fill: hatchFill,
          'pointer-events': 'none',
        });
        svg.appendChild(over);
      }
      cursor += bw;
    });
    svg.appendChild(el('text', {
      x: cursor + 7, y: y + rowH / 2 + 4, class: 'barvalue',
    }, fmt(totals[i])));
  });
  mount.appendChild(svg);

  if (opts.legendMount) {
    opts.legendMount.innerHTML = MECH_ORDER.map(m =>
      `<span class="lgd"><i style="background:${MECH_COLORS[m]}"></i>${MECH_LABEL[m]}</span>`).join('');
  }
}

/* --- grouped bars: one group per entity, one bar per period -------------- */

function groupedChart(mount, rows, series, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!rows.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty || 'Nothing to draw.')}</p>`;
    if (opts.legendMount) opts.legendMount.innerHTML = '';
    return;
  }
  const fmt = opts.fmt || (v => v);
  const barH = 13, gap = 5, groupPad = 12;
  const groupH = series.length * (barH + gap) + groupPad;
  const padT = 10, padB = 34;
  const full = Math.max(mount.clientWidth || 900, 560);
  const labelW = Math.min(opts.labelW || 250, Math.round(full * 0.42));
  const padR = 90;
  const plotW = Math.max(full - labelW - padR, 120);
  const h = rows.length * groupH + padT + padB;
  let max = 0;
  rows.forEach(r => series.forEach(s => { max = Math.max(max, r.values[s.key] || 0); }));
  max = max || 1;

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h, role: 'img',
    'aria-label': opts.title || 'grouped bar chart',
  });
  const hatchFill = addHatch(svg);
  for (let i = 0; i <= 4; i++) {
    const gx = labelW + (plotW / 4) * i;
    svg.appendChild(el('line', { x1: gx, x2: gx, y1: padT, y2: h - padB, stroke: '#dfe4e8' }));
    svg.appendChild(el('text', {
      x: gx, y: h - padB + 15, 'text-anchor': 'middle', class: 'axis',
    }, fmt((max / 4) * i)));
  }

  rows.forEach((r, i) => {
    const gy = padT + i * groupH;
    svg.appendChild(el('text', {
      x: labelW - 10, y: gy + groupH / 2, 'text-anchor': 'end',
      class: 'barlabel' + (r.hatched ? ' recon' : ''),
    }, clip(r.label, Math.floor(labelW / 6.1))));
    series.forEach((s, j) => {
      const v = r.values[s.key] || 0;
      const y = gy + j * (barH + gap) + 3;
      const bw = Math.max((v / max) * plotW, 1);
      const rect = el('rect', { x: labelW, y, width: bw, height: barH, fill: s.color });
      const html = `<strong>${escText(r.label)}</strong><br>${escText(s.label)}`
        + `<br><span class="tipv">${escText(fmt(v))}</span>`
        + (r.hatched ? '<br><em>Reconstructed lower bound</em>' : '');
      rect.addEventListener('mousemove', evt => showTip(html, evt));
      rect.addEventListener('mouseleave', hideTip);
      svg.appendChild(rect);
      if (r.hatched) {
        svg.appendChild(el('rect', {
          x: labelW, y, width: bw, height: barH, fill: hatchFill, 'pointer-events': 'none',
        }));
      }
      svg.appendChild(el('text', {
        x: labelW + bw + 6, y: y + barH - 2, class: 'barvalue sm',
      }, fmt(v)));
    });
  });
  mount.appendChild(svg);

  if (opts.legendMount) {
    opts.legendMount.innerHTML = series.map(s =>
      `<span class="lgd"><i style="background:${s.color}"></i>${s.label}</span>`).join('');
  }
}

/* --- diverging bars: signed change either side of a zero line ------------ */

/* rows: [{label, value (signed), color, hatched, sub, detail}]
   The zero line is drawn as a <rect>, not a <line>, because the stylesheet
   makes every <line> in a chart transparent to enforce the no-gridlines rule
   and the zero line is structure rather than grid. */
function divergingChart(mount, rows, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!rows.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty || 'Nothing to draw.')}</p>`;
    if (opts.legendMount) opts.legendMount.innerHTML = '';
    return;
  }
  const fmt = opts.fmt || (v => v);
  const rowH = 26, padT = 10, padB = 34;
  const full = Math.max(mount.clientWidth || 900, 560);
  const labelW = Math.min(opts.labelW || 250, Math.round(full * 0.42));
  const padR = 24;
  const plotW = Math.max(full - labelW - padR, 160);
  const h = rows.length * rowH + padT + padB;

  const vals = rows.map(r => Number(r.value) || 0);
  const maxPos = Math.max(0, Math.max.apply(null, vals));
  const maxNeg = Math.max(0, -Math.min.apply(null, vals));
  const span = maxPos + maxNeg || 1;
  // Reserve room at each end for the printed value so it never runs off.
  const gutter = 76;
  const usable = Math.max(plotW - gutter * 2, 80);
  const negW = (maxNeg / span) * usable;
  const zeroX = labelW + gutter + negW;

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h, role: 'img',
    'aria-label': opts.title || 'change chart',
  });
  const hatchFill = addHatch(svg);

  svg.appendChild(el('rect', {
    x: zeroX - 0.5, y: padT, width: 1, height: h - padT - padB, class: 'axisrule',
  }));
  [[-maxNeg, 'left'], [maxPos, 'right']].forEach(([v]) => {
    if (!v) return;
    const x = zeroX + (v / span) * usable;
    svg.appendChild(el('text', {
      x, y: h - padB + 15, 'text-anchor': 'middle', class: 'axis',
    }, fmt(v)));
  });
  svg.appendChild(el('text', {
    x: zeroX, y: h - padB + 15, 'text-anchor': 'middle', class: 'axis',
  }, fmt(0)));

  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const v = Number(r.value) || 0;
    const w = Math.max(Math.abs(v) / span * usable, 1.5);
    const x = v < 0 ? zeroX - w : zeroX;

    const g = el('g', {
      class: 'barrow', tabindex: '0', role: 'img',
      'aria-label': `${r.label}, ${fmt(v)}`,
    });
    g.appendChild(el('rect', { x: 0, y, width: full, height: rowH, fill: 'transparent', class: 'hitrow' }));
    g.appendChild(el('text', {
      x: labelW - 10, y: y + rowH / 2 + 4, 'text-anchor': 'end',
      class: 'barlabel' + (r.hatched ? ' recon' : ''),
    }, clip(r.label, Math.floor(labelW / 6.1))));
    g.appendChild(el('rect', {
      x, y: y + 5, width: w, height: rowH - 11, fill: r.color || '#9ba7b4',
    }));
    if (r.hatched) {
      g.appendChild(el('rect', { x, y: y + 5, width: w, height: rowH - 11, fill: hatchFill }));
    }
    g.appendChild(el('text', {
      x: v < 0 ? x - 7 : x + w + 7, y: y + rowH / 2 + 4,
      'text-anchor': v < 0 ? 'end' : 'start',
      class: 'barvalue' + (v < 0 ? ' neg' : ''),
    }, (v > 0 ? '+' : '') + fmt(v)));

    const html = `<strong>${escText(r.label)}</strong>${r.sub ? '<br>' + escText(r.sub) : ''}`
      + `<br><span class="tipv">${escText((v > 0 ? '+' : '') + fmt(v))}</span>`
      + (r.detail ? '<br>' + escText(r.detail) : '')
      + (r.hatched ? '<br><em>Reconstructed from publication affiliations — a lower bound</em>' : '');
    g.addEventListener('mousemove', evt => showTip(html, evt));
    g.addEventListener('mouseleave', hideTip);
    g.addEventListener('focus', () => tipAtElement(html, g));
    g.addEventListener('blur', hideTip);
    svg.appendChild(g);
  });

  mount.appendChild(svg);
}

/* --- vertical stacked columns, one column per category ------------------- */

/* groups: [{label, values:{seriesKey: number}, note}]
   series: [{key, label, color}] drawn bottom-to-top in the order given. */
function columnStackChart(mount, groups, series, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!groups.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty || 'Nothing to draw.')}</p>`;
    if (opts.legendMount) opts.legendMount.innerHTML = '';
    return;
  }
  const fmt = opts.fmt || (v => v);
  const full = Math.max(mount.clientWidth || 900, 480);
  const h = opts.height || 360;
  const padL = 74, padR = 18, padT = 16, padB = 42;
  const plotW = Math.max(full - padL - padR, 160);
  const plotH = h - padT - padB;
  const totals = groups.map(g => series.reduce((s, x) => s + (Number(g.values[x.key]) || 0), 0));
  const max = Math.max.apply(null, totals) || 1;
  const step = plotW / groups.length;
  const barW = Math.min(step * 0.62, 84);

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h, role: 'img',
    'aria-label': opts.title || 'stacked column chart',
  });

  svg.appendChild(el('rect', {
    x: padL, y: padT + plotH, width: plotW, height: 1, class: 'axisrule',
  }));
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    svg.appendChild(el('text', {
      x: padL - 9, y: padT + plotH - (v / max) * plotH + 3.5,
      'text-anchor': 'end', class: 'axis',
    }, fmt(v)));
  }

  groups.forEach((grp, i) => {
    const cx = padL + step * i + step / 2;
    let yCursor = padT + plotH;
    series.forEach(s => {
      const v = Number(grp.values[s.key]) || 0;
      if (v <= 0) return;
      const bh = (v / max) * plotH;
      yCursor -= bh;
      const rect = el('rect', {
        x: cx - barW / 2, y: yCursor, width: barW, height: bh, fill: s.color,
      });
      const share = totals[i] ? (v / totals[i]) * 100 : 0;
      const html = `<strong>${escText(grp.label)}</strong><br>${escText(s.label)}`
        + `<br><span class="tipv">${escText(fmt(v))}</span> · ${share.toFixed(1)}% of the year`;
      rect.addEventListener('mousemove', evt => showTip(html, evt));
      rect.addEventListener('mouseleave', hideTip);
      svg.appendChild(rect);
      // Print the share inside the band when there is room for it.
      if (bh > 17 && barW > 34) {
        svg.appendChild(el('text', {
          x: cx, y: yCursor + bh / 2 + 3.5, 'text-anchor': 'middle', class: 'barvalue sm',
          fill: s.ink || '#ffffff', 'pointer-events': 'none',
        }, share.toFixed(0) + '%'));
      }
    });
    svg.appendChild(el('text', {
      x: cx, y: padT + plotH + 17, 'text-anchor': 'middle', class: 'axis',
    }, grp.label));
    if (grp.note) {
      svg.appendChild(el('text', {
        x: cx, y: padT + plotH + 31, 'text-anchor': 'middle', class: 'axis',
      }, grp.note));
    }
  });

  mount.appendChild(svg);

  if (opts.legendMount) {
    opts.legendMount.innerHTML = series.slice().reverse().map(s =>
      `<span class="lgd"><i style="background:${s.color}"></i>${escText(s.label)}</span>`).join('');
  }
}

/* --- scatter ------------------------------------------------------------- */

/* points: [{label, x, y, color, hatched, sub, detail, id}] */
function scatterChart(mount, points, opts) {
  opts = opts || {};
  mount.innerHTML = '';
  if (!points.length) {
    mount.innerHTML = `<p class="emptystate">${escText(opts.empty
      || 'No rows have both measures. Widen the filters above.')}</p>`;
    return;
  }
  const fx = opts.fmtX || (v => v), fy = opts.fmtY || (v => v);
  const full = Math.max(mount.clientWidth || 900, 560);
  const h = opts.height || 420;
  const padL = 74, padR = 22, padT = 14, padB = 46;
  const plotW = Math.max(full - padL - padR, 120);
  const plotH = h - padT - padB;

  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const xMax = Math.max.apply(null, xs) || 1;
  const yMax = Math.max.apply(null, ys) || 1;
  const logX = opts.scaleX === 'log', logY = opts.scaleY === 'log';
  const posX = xs.filter(v => v > 0), posY = ys.filter(v => v > 0);
  const xLo = posX.length ? Math.min.apply(null, posX) : 1;
  const yLo = posY.length ? Math.min.apply(null, posY) : 1;
  const sx = makeScale(xMax, plotW, logX ? 'log' : 'lin', xLo);
  const sy = makeScale(yMax, plotH, logY ? 'log' : 'lin', yLo);
  const X = v => padL + sx.x(v);
  const Y = v => padT + plotH - sy.x(v);

  const svg = el('svg', {
    viewBox: `0 0 ${full} ${h}`, width: '100%', height: h, role: 'img',
    'aria-label': opts.title || 'scatter plot',
  });

  // Axis lines only. No grid, per the house style.
  svg.appendChild(el('line', {
    x1: padL, x2: padL + plotW, y1: padT + plotH, y2: padT + plotH,
    stroke: '#c9d0d6', 'stroke-width': 1,
  }));
  svg.appendChild(el('line', {
    x1: padL, x2: padL, y1: padT, y2: padT + plotH, stroke: '#c9d0d6', 'stroke-width': 1,
  }));
  sx.ticks.forEach(v => svg.appendChild(el('text', {
    x: X(v), y: padT + plotH + 16, 'text-anchor': 'middle', class: 'axis',
  }, fx(v))));
  sy.ticks.forEach(v => svg.appendChild(el('text', {
    x: padL - 8, y: Y(v) + 3.5, 'text-anchor': 'end', class: 'axis',
  }, fy(v))));
  svg.appendChild(el('text', {
    x: padL + plotW / 2, y: h - 6, 'text-anchor': 'middle', class: 'axistitle',
  }, opts.xLabel || ''));
  svg.appendChild(el('text', {
    x: 14, y: padT + plotH / 2, 'text-anchor': 'middle', class: 'axistitle',
    transform: `rotate(-90 14 ${padT + plotH / 2})`,
  }, opts.yLabel || ''));

  points.forEach(p => {
    const cx = X(p.x), cy = Y(p.y);
    const g = el('g', {
      class: 'dot' + (p.selected ? ' selected' : ''), tabindex: '0',
      role: opts.onSelect ? 'button' : 'img',
      'aria-label': `${p.label}: ${fx(p.x)} by ${fy(p.y)}`,
    });
    g.appendChild(el('circle', { cx, cy, r: 11, fill: 'transparent', class: 'hitdot' }));
    g.appendChild(el('circle', {
      cx, cy, r: p.hatched ? 6.5 : 4.5, fill: p.color || '#9ba7b4',
      stroke: p.hatched ? '#16202a' : 'rgba(255,255,255,.85)',
      'stroke-width': p.hatched ? 1.6 : 1,
      'stroke-dasharray': p.hatched ? '2 1.6' : '',
    }));
    const html = `<strong>${escText(p.label)}</strong>${p.sub ? '<br>' + escText(p.sub) : ''}`
      + `<br>${escText(opts.xLabel || 'x')}: <span class="tipv">${escText(fx(p.x))}</span>`
      + `<br>${escText(opts.yLabel || 'y')}: <span class="tipv">${escText(fy(p.y))}</span>`
      + (p.detail ? '<br>' + escText(p.detail) : '')
      + (p.hatched ? '<br><em>Reconstructed lower bound</em>' : '');
    g.addEventListener('mousemove', evt => showTip(html, evt));
    g.addEventListener('mouseleave', hideTip);
    g.addEventListener('focus', () => tipAtElement(html, g));
    g.addEventListener('blur', hideTip);
    if (opts.onSelect) {
      g.addEventListener('click', () => opts.onSelect(p));
      g.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); opts.onSelect(p); }
      });
    }
    svg.appendChild(g);
  });

  // Label only the points a reader would look for, so the field stays legible.
  (opts.labelled || []).forEach(id => {
    const p = points.find(q => q.id === id);
    if (!p) return;
    const cx = X(p.x), cy = Y(p.y);
    const right = cx < padL + plotW * 0.75;
    svg.appendChild(el('text', {
      x: cx + (right ? 10 : -10), y: cy - 8,
      'text-anchor': right ? 'start' : 'end', class: 'dotlabel',
    }, clip(p.label, 26)));
  });

  mount.appendChild(svg);
}

/* --- export -------------------------------------------------------------- */

/* The charts take their typography and colour from the stylesheet, so a raw
   serialisation would export unstyled black Times. Copy the computed values
   onto the clone before writing it out. */
const EXPORT_PROPS = [
  'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-dasharray', 'opacity',
  'font-family', 'font-size', 'font-weight', 'text-anchor', 'letter-spacing',
];

function inlineStyles(src, clone) {
  const a = src.querySelectorAll('*'), b = clone.querySelectorAll('*');
  for (let i = 0; i < a.length; i++) {
    const cs = getComputedStyle(a[i]);
    let decl = '';
    EXPORT_PROPS.forEach(p => {
      const v = cs.getPropertyValue(p);
      if (v) decl += `${p}:${v};`;
    });
    b[i].setAttribute('style', decl);
    b[i].removeAttribute('class');
    b[i].removeAttribute('tabindex');
  }
}

function serialise(mount) {
  const src = mount.querySelector('svg');
  if (!src) return null;
  const clone = src.cloneNode(true);
  inlineStyles(src, clone);
  clone.setAttribute('xmlns', SVGNS);
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
  const vb = (src.getAttribute('viewBox') || '0 0 900 400').split(/\s+/).map(Number);
  clone.setAttribute('width', vb[2]);
  clone.setAttribute('height', vb[3]);
  const bg = el('rect', { x: 0, y: 0, width: vb[2], height: vb[3], fill: '#ffffff' });
  clone.insertBefore(bg, clone.firstChild);
  return {
    text: '<?xml version="1.0" encoding="UTF-8"?>\n'
      + new XMLSerializer().serializeToString(clone),
    w: vb[2], h: vb[3],
  };
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function exportSVG(mount, filename) {
  const s = serialise(mount);
  if (!s) return;
  saveBlob(new Blob([s.text], { type: 'image/svg+xml;charset=utf-8' }), filename + '.svg');
}

function exportPNG(mount, filename, scale) {
  const s = serialise(mount);
  if (!s) return;
  const k = scale || 2;
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas');
    c.width = s.w * k;
    c.height = s.h * k;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    c.toBlob(b => { if (b) saveBlob(b, filename + '.png'); }, 'image/png');
  };
  img.src = 'data:image/svg+xml;base64,'
    + btoa(unescape(encodeURIComponent(s.text)));
}

window.RMGBCharts = {
  barChart, stackedChart, groupedChart, scatterChart, divergingChart, columnStackChart,
  exportSVG, exportPNG, MECH_ORDER, MECH_COLORS, MECH_LABEL,
};
