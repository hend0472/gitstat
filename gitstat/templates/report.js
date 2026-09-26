(() => {
'use strict';
const R = JSON.parse(document.getElementById('data').textContent);
const M = Object.fromEntries(R.metrics.map(m => [m.key, m]));
const DEVS = R.developers;
const LOGINS = Object.keys(DEVS);
const TEAM = R.team.median;
const WEEKS = R.periods;  // weeks or sprints, depending on --sprint-*
const PKIND = R.period.kind;
const periodTitle = p => (PKIND === 'sprint' ? `Sprint ${p.label}` : `Week of ${p.start.slice(0, 10)}`) + (p.in_progress ? ' (in progress)' : '');
const SECTIONS = ['Authoring', 'Reviewing', 'Commits'];
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { a: null, b: null, stack: 'commits', leader: 'prs_merged', leaderN: 15,
                sx: 'prs_merged', sy: 'time_to_merge_median', strip: 'hours_to_merge', matrix: 'pct' };

// ------------------------------------------------------------------ formatting
const fmtH = h => h == null ? '–' : h < 1/60 ? '<1m' : h < 1 ? Math.round(h*60)+'m' : h < 48 ? h.toFixed(1)+'h' : (h/24).toFixed(1)+'d';
const fmtN = v => v == null ? '–' : Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {maximumFractionDigits: 1});
const fmtP = v => v == null ? '–' : Math.round(v*100)+'%';
const fmt = (k, v) => { const kind = M[k].kind; return kind === 'hours' ? fmtH(v) : kind === 'pct' ? fmtP(v) : fmtN(v); };
const _ctx = document.createElement('canvas').getContext('2d');
const textW = (s, px = 11, bold = false) => {
  _ctx.font = `${bold ? 600 : 400} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  return _ctx.measureText(String(s)).width;
};
// Fit a label into maxW: drop a trailing "(Org)" first, then truncate with an ellipsis.
function fitLabel(s, maxW, px = 11, bold = false) {
  s = String(s);
  if (textW(s, px, bold) <= maxW) return s;
  const base = s.replace(/\s*\([^)]*\)\s*$/, '') || s;
  if (textW(base, px, bold) <= maxW) return base;
  let out = base;
  while (out.length > 1 && textW(out + '…', px, bold) > maxW) out = out.slice(0, -1);
  return out.trimEnd() + '…';
}
// <text> content that shows a fitted label and the full name on native hover.
const labelText = (full, fitted) => fitted === full ? esc(full) : `${esc(fitted)}<title>${esc(full)}</title>`;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// ------------------------------------------------------------------ helpers
const median = xs => { const s = xs.filter(x => x != null).sort((a, b) => a - b); if (!s.length) return null;
  const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m-1] + s[m]) / 2; };
const sum = xs => xs.reduce((a, b) => a + b, 0);
const isActive = (login, section) => DEVS[login].active ? DEVS[login].active[section] : true;
const peers = key => LOGINS.filter(l => isActive(l, M[key].section) && DEVS[l].metrics[key] != null);
function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
const linear = (d0, d1, r0, r1) => v => r0 + (r1 - r0) * ((v - d0) / ((d1 - d0) || 1));
const HOUR_TICKS = [[1/6,'10m'],[1,'1h'],[4,'4h'],[24,'1d'],[72,'3d'],[168,'1w'],[672,'4w'],[2160,'3mo']];
function logScale(lo, hi, r0, r1) {
  const a = Math.log10(lo), b = Math.log10(hi);
  const f = v => r0 + (r1 - r0) * ((Math.log10(Math.max(lo, v)) - a) / ((b - a) || 1));
  f.ticks = HOUR_TICKS.filter(([t]) => t >= lo * 0.999 && t <= hi * 1.001);
  return f;
}
function logDomain(vals) {
  const pos = vals.filter(v => v != null && v > 0);
  if (!pos.length) return [1, 24];
  let lo = Math.min(...pos), hi = Math.max(...pos);
  lo = [...HOUR_TICKS].reverse().find(([t]) => t <= lo)?.[0] ?? HOUR_TICKS[0][0];
  hi = HOUR_TICKS.find(([t]) => t >= hi)?.[0] ?? hi;
  if (hi <= lo) hi = lo * 10;
  return [lo, hi];
}
function hashJitter(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) | 0; return ((h >>> 0) % 1000) / 1000 - 0.5; }
const COLORS = ['var(--c1)','var(--c2)','var(--c3)','var(--c4)','var(--c5)','var(--c6)','var(--c7)','var(--c8)'];
const COLOR_A = 'var(--c1)', COLOR_B = 'var(--c2)';

// ------------------------------------------------------------------ tooltip
// Each chart root keeps its tooltip payloads in el._tips; marks carry data-tip="<index>".
const tip = $('#tip');
function tipRender(p) {
  tip.replaceChildren();
  if (p.title) { const t = document.createElement('div'); t.className = 't'; t.textContent = p.title; tip.append(t); }
  for (const row of p.rows || []) {
    const r = document.createElement('div'); r.className = 'r';
    const i = document.createElement('i');
    if (row.dash) i.className = 'dash'; else i.style.background = row.color || 'var(--c1)';
    const b = document.createElement('b'); b.textContent = row.value;
    const s = document.createElement('span'); s.textContent = row.label ?? '';
    r.append(i, b, s); tip.append(r);
  }
  tip.style.opacity = 1;
}
function tipMove(x, y) {
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + 14, top = y - h - 10;
  if (left + w > innerWidth - 8) left = x - w - 14;
  if (top < 8) top = y + 16;
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}
const tipHide = () => { tip.style.opacity = 0; };
function payload(el) {
  const m = el.closest('[data-tip]'); if (!m) return null;
  const root = m.closest('.chart'); return root && root._tips ? root._tips[+m.dataset.tip] : null;
}
document.addEventListener('pointermove', e => {
  if (e.target.classList && e.target.classList.contains('overlay')) return;  // line charts run their own crosshair
  const p = payload(e.target);
  if (p) { tipRender(p); tipMove(e.clientX, e.clientY); highlight(e.target); } else if (!e.target.closest('.xhair')) { tipHide(); highlight(null); }
});
document.addEventListener('focusin', e => {
  const p = payload(e.target); if (!p) return;
  const r = e.target.getBoundingClientRect(); tipRender(p); tipMove(r.left + r.width / 2, r.top); highlight(e.target);
});
document.addEventListener('focusout', () => { tipHide(); highlight(null); });
addEventListener('scroll', () => { tipHide(); highlight(null); }, { passive: true });
let lit = [];
function highlight(el) {
  lit.forEach(n => n.classList.remove('on')); lit = [];
  document.querySelectorAll('.chart.hovering').forEach(c => c.classList.remove('hovering'));
  const m = el && el.closest('[data-key]'); if (!m) return;
  const root = m.closest('.chart'); root.classList.add('hovering');
  lit = [...root.querySelectorAll(`[data-key="${CSS.escape(m.dataset.key)}"]`)];
  lit.forEach(n => n.classList.add('on'));
}
function tipper(root) { root._tips = []; return p => { root._tips.push(p); return root._tips.length - 1; }; }

// ------------------------------------------------------------------ shared svg pieces
const svg = (w, h, body, label) => `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body}</svg>`;
function yGrid(y, ticks, x0, x1, f = fmtN) {
  return ticks.map((v, i) => `<line x1="${x0}" x2="${x1}" y1="${y(v)}" y2="${y(v)}" stroke="var(${i ? '--grid' : '--axis'})"/>` +
    `<text x="${x0 - 6}" y="${y(v) + 4}" text-anchor="end">${f(v)}</text>`).join('');
}
function weekAxis(x, y0, bw) {
  const n = WEEKS.length, step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(bw * n / 56))));
  return WEEKS.map((w, i) => i % step ? '' : `<text x="${x(i) + bw / 2}" y="${y0 + 16}" text-anchor="middle">${w.short}</text>`).join('');
}
function legendHTML(items) {
  return items.map(it => `<span${it.dim ? ' style="opacity:.4" title="No activity in this view"' : ''}><i class="${it.kind || ''}" style="${it.kind === 'dash' ? '' : `background:${it.color}`}"></i>${esc(it.label)}</span>`).join('');
}
const roundTop = (x, y, w, h, r) => {
  r = Math.min(r, w / 2, h); if (h <= 0) return '';
  return `M${x},${y + h} V${y + r} q0,-${r} ${r},-${r} H${x + w - r} q${r},0 ${r},${r} V${y + h} Z`;
};
const roundRight = (x, y, w, h, r) => {
  r = Math.min(r, h / 2, w); if (w <= 0) return '';
  return `M${x},${y} H${x + w - r} q${r},0 ${r},${r} V${y + h - r} q0,${r} -${r},${r} H${x} Z`;
};
function sparkline(values, w = 90, h = 22, color = 'var(--c1)') {
  const max = Math.max(1, ...values), n = values.length;
  const x = i => n > 1 ? 1 + i * (w - 2) / (n - 1) : w / 2, y = v => h - 2 - (h - 4) * v / max;
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function segmented(el, options, current, onPick) {
  el.innerHTML = options.map(([v, l]) => `<button type="button" data-v="${esc(v)}" aria-pressed="${v == current}">${esc(l)}</button>`).join('');
  el.onclick = e => { const b = e.target.closest('button'); if (!b) return;
    el.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); onPick(b.dataset.v); };
}
function metricOptions(sel, current, filter = () => true) {
  sel.innerHTML = SECTIONS.map(s => `<optgroup label="${s}">` + R.metrics.filter(m => m.section === s && filter(m))
    .map(m => `<option value="${m.key}" ${m.key === current ? 'selected' : ''}>${esc(m.label)}</option>`).join('') + '</optgroup>').join('');
}
const widthOf = el => Math.max(280, el.querySelector('.plot').clientWidth);

// ------------------------------------------------------------------ header & tiles
$('#title').textContent = R.repo;
$('#window').textContent = `${R.since.slice(0, 10)} → ${R.until.slice(0, 10)} · ${WEEKS.length} ${PKIND}s${PKIND === 'sprint' ? ` of ${R.period.days} days` : ''} · ${LOGINS.length} contributors`;
const periodTotal = key => WEEKS.map((_, i) => sum(LOGINS.map(l => DEVS[l].series[key][i])));
function renderTiles() {
  const S = R.repo_summary;
  const tiles = [
    ['PRs opened', fmtN(S.prs_opened), periodTotal('prs_opened')],
    ['PRs merged', fmtN(S.prs_merged), periodTotal('prs_merged')],
    ['Reviews', fmtN(S.reviews), periodTotal('reviews')],
    ['Commits', fmtN(S.commits), periodTotal('commits')],
    ['Median time to merge', fmtH(S.time_to_merge_median)],
    ['Median wait for feedback', fmtH(S.time_to_first_review_median)],
  ];
  $('#tiles').innerHTML = tiles.map(([l, v, s]) => `<div class="card tile"><div class="v">${v}</div><div class="l">${l}</div>` +
    (s ? sparkline(s, 120, 24) : '') + '</div>').join('');
}

// ------------------------------------------------------------------ stacked activity by developer
const STACK_SERIES = [['commits', 'Commits'], ['prs_opened', 'PRs opened'], ['prs_merged', 'PRs merged'], ['reviews', 'Reviews']];
// The 7 most active people overall get a fixed color for the whole page, so switching
// between commits / PRs / reviews never repaints anyone. Activity is each person's share
// of the team total per series, summed, so high-volume commits don't drown out reviews.
const FEATURED = (() => {
  const keys = STACK_SERIES.map(([k]) => k);
  const teamTotal = Object.fromEntries(keys.map(k => [k, sum(LOGINS.map(l => sum(DEVS[l].series[k]))) || 1]));
  return LOGINS.map(l => [l, sum(keys.map(k => sum(DEVS[l].series[k]) / teamTotal[k]))])
    .filter(([, score]) => score > 0).sort((a, b) => b[1] - a[1]).slice(0, 7).map(([l]) => l);
})();
const DEV_COLOR = Object.fromEntries(FEATURED.map((l, i) => [l, COLORS[i]]));

function renderStacked() {
  const root = $('#c-stacked'), T = tipper(root), key = state.stack;
  const rest = LOGINS.filter(l => !DEV_COLOR[l] && sum(DEVS[l].series[key]) > 0);
  const series = FEATURED.map(l => ({ key: l, label: l, color: DEV_COLOR[l], vals: DEVS[l].series[key] }));
  if (rest.length) series.push({ key: '__other', label: `Other (${rest.length})`, color: 'var(--other)',
    vals: WEEKS.map((_, i) => sum(rest.map(l => DEVS[l].series[key][i]))) });
  // Everyone keeps their legend slot; people with nothing in this view are dimmed rather than removed.
  root.querySelector('.legend').innerHTML = legendHTML(series.map(s => ({ ...s, dim: !sum(s.vals) })));
  renderPies(root, series, key, T);
  const plot = root.querySelector('.plot');
  if (!series.some(s => sum(s.vals) > 0)) { plot.innerHTML = '<div class="empty">No activity of this kind in the window.</div>'; return; }
  const W = widthOf(root), H = 260, P = { l: 40, r: 8, t: 10, b: 26 };
  const colTotals = WEEKS.map((_, i) => sum(series.map(s => s.vals[i])));
  const max = niceMax(Math.max(...colTotals)), n = WEEKS.length;
  const bw = (W - P.l - P.r) / n, x = i => P.l + i * bw, y = linear(0, max, H - P.b, P.t);
  let g = yGrid(y, [0, max / 2, max], P.l, W - P.r);
  WEEKS.forEach((w, i) => {
    let base = 0; const barW = Math.max(2, bw * 0.72), bx = x(i) + (bw - barW) / 2;
    const nonzero = series.filter(s => s.vals[i] > 0);
    nonzero.forEach((s, j) => {
      const y0 = y(base), y1 = y(base + s.vals[i]); base += s.vals[i];
      const h = y0 - y1, top = j === nonzero.length - 1;
      const d = top ? roundTop(bx, y1, barW, h, 3) : `M${bx},${y1} h${barW} v${h} h-${barW} Z`;
      g += `<path class="mark dimmable" data-key="${esc(s.key)}" d="${d}" fill="${s.color}" stroke="var(--surface)" stroke-width="1"/>`;
    });
    const rows = series.filter(s => s.vals[i] > 0).sort((a, b) => b.vals[i] - a.vals[i])
      .map(s => ({ color: s.color, value: fmtN(s.vals[i]), label: s.label }));
    rows.unshift({ color: 'var(--ink)', value: fmtN(colTotals[i]), label: 'Total' });
    g += `<rect class="hit" tabindex="0" x="${x(i)}" y="${P.t}" width="${bw}" height="${H - P.t - P.b}" data-tip="${T({ title: periodTitle(w), rows })}"/>`;
  });
  g += weekAxis(x, H - P.b, bw);
  plot.innerHTML = svg(W, H, g, `Activity per ${PKIND} by developer`);
}

// ------------------------------------------------------------------ share per period (donuts)
// Same people, colors and toggle as the stacked bars; hovering a slice highlights that
// person in every donut and in the bars, because they share one chart root.
const UNIT = { commits: ['commit', 'commits'], prs_opened: ['PR opened', 'PRs opened'], prs_merged: ['PR merged', 'PRs merged'], reviews: ['review', 'reviews'] };
function arcPath(cx, cy, r0, r1, a0, a1) {
  const p = (r, a) => `${(cx + r * Math.sin(a)).toFixed(2)},${(cy - r * Math.cos(a)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${p(r1, a0)} A${r1},${r1} 0 ${large} 1 ${p(r1, a1)} L${p(r0, a1)} A${r0},${r0} 0 ${large} 0 ${p(r0, a0)} Z`;
}
function renderPies(root, series, key, T) {
  const label = STACK_SERIES.find(([k]) => k === key)[1].toLowerCase();
  root.querySelector('.pies-title').textContent = `Share of ${label} per ${PKIND}`;
  const [one, many] = UNIT[key];
  const S = 140, cx = S / 2, cy = S / 2, r1 = 62, r0 = 40;
  root.querySelector('.pies').innerHTML = WEEKS.map((w, i) => {
    const slices = series.map(s => ({ ...s, v: s.vals[i] })).filter(s => s.v > 0);
    const total = sum(slices.map(s => s.v));
    let g = '';
    if (!total) {
      g += `<circle cx="${cx}" cy="${cy}" r="${(r0 + r1) / 2}" fill="none" stroke="var(--wash)" stroke-width="${r1 - r0}"/>`;
    } else if (slices.length === 1) {
      const s0 = slices[0];
      g += `<circle class="mark dimmable" data-key="${esc(s0.key)}" cx="${cx}" cy="${cy}" r="${(r0 + r1) / 2}" fill="none" stroke="${s0.color}" stroke-width="${r1 - r0}"
        data-tip="${T({ title: periodTitle(w), rows: [{ color: s0.color, value: '100%', label: `${s0.label} · ${fmtN(s0.v)} ${s0.v === 1 ? one : many}` }] })}"/>`;
    } else {
      let a = 0;
      slices.forEach(s => {
        const a1 = a + (s.v / total) * Math.PI * 2;
        const pct = Math.round(s.v / total * 100);
        g += `<path class="mark dimmable" data-key="${esc(s.key)}" d="${arcPath(cx, cy, r0, r1, a, a1)}" fill="${s.color}" stroke="var(--surface)" stroke-width="2" stroke-linejoin="round"
          data-tip="${T({ title: periodTitle(w), rows: [{ color: s.color, value: `${pct}%`, label: `${s.label} · ${fmtN(s.v)} ${s.v === 1 ? one : many}` }] })}"/>`;
        a = a1;
      });
    }
    g += `<text x="${cx}" y="${cy + 2}" text-anchor="middle" class="ink b" style="font-size:18px">${fmtN(total)}</text>`;
    g += `<text x="${cx}" y="${cy + 18}" text-anchor="middle">${total === 1 ? one : many}</text>`;
    const top = slices.slice().sort((p, q) => q.v - p.v)[0];
    const lead = !total ? 'No activity'
      : top.key === '__other' ? `Mostly others · ${Math.round(top.v / total * 100)}%`
      : `Top: ${esc(fitLabel(top.label, 118))} · ${Math.round(top.v / total * 100)}%`;
    return `<figure class="pie${w.in_progress ? ' live' : ''}">${svg(S, S, g, `${periodTitle(w)}: share of ${label}`)}
      <figcaption><b>${esc(PKIND === 'sprint' ? w.label : 'Week of ' + w.short)}</b><br><span class="muted">${w.in_progress ? 'In progress · ' : ''}${lead}</span></figcaption></figure>`;
  }).join('');
}

// ------------------------------------------------------------------ histograms
const BUCKETS = [[1, '<1h'], [4, '1–4h'], [24, '4–24h'], [72, '1–3d'], [168, '3–7d'], [336, '1–2w'], [Infinity, '>2w']];
function renderHistogram(rootSel, field) {
  const root = $(rootSel), T = tipper(root), plot = root.querySelector('.plot');
  const vals = LOGINS.flatMap(l => DEVS[l].prs.map(p => p[field])).filter(v => v != null);
  if (!vals.length) { plot.innerHTML = '<div class="empty">No data in this window.</div>'; return; }
  const counts = BUCKETS.map(() => 0);
  vals.forEach(v => { counts[BUCKETS.findIndex(([hi]) => v < hi)]++; });
  const W = widthOf(root), H = 200, P = { l: 8, r: 8, t: 22, b: 24 };
  const max = Math.max(...counts), n = BUCKETS.length, bw = (W - P.l - P.r) / n, y = linear(0, max, H - P.b, P.t);
  let g = `<line x1="${P.l}" x2="${W - P.r}" y1="${H - P.b}" y2="${H - P.b}" stroke="var(--axis)"/>`;
  counts.forEach((c, i) => {
    const bx = P.l + i * bw + bw * 0.14, w = bw * 0.72;
    if (c) g += `<path class="mark" d="${roundTop(bx, y(c), w, H - P.b - y(c), 4)}" fill="var(--c1)"/>`;
    g += `<text class="ink2" x="${bx + w / 2}" y="${y(c) - 6}" text-anchor="middle">${c ? `${c} · ${Math.round(c / vals.length * 100)}%` : ''}</text>`;
    g += `<text x="${bx + w / 2}" y="${H - 6}" text-anchor="middle">${BUCKETS[i][1]}</text>`;
    g += `<rect class="hit" tabindex="0" x="${P.l + i * bw}" y="${P.t - 16}" width="${bw}" height="${H - P.t - P.b + 16}" data-tip="${T({ title: BUCKETS[i][1], rows: [{ value: fmtN(c), label: `PRs (${Math.round(c / vals.length * 100)}%)` }] })}"/>`;
  });
  plot.innerHTML = svg(W, H, g, 'Distribution');
}

// ------------------------------------------------------------------ leaderboard
function renderLeader() {
  const root = $('#c-leader'), T = tipper(root), key = state.leader, m = M[key];
  const all = peers(key).map(l => [l, DEVS[l].metrics[key]]);
  all.sort((a, b) => m.better === 'lower' ? a[1] - b[1] : b[1] - a[1]);
  const total = all.length, rankOf = new Map(all.map(([l], i) => [l, i + 1]));
  let rows = all;
  if (state.leaderN) {
    const keep = all.slice(0, state.leaderN);
    for (const l of [state.a, state.b]) if (l && !keep.find(r => r[0] === l)) { const r = all.find(r => r[0] === l); if (r) keep.push(r); }
    rows = keep;
  }
  $('#leader-title').textContent = m.label;
  $('#leader-sub').textContent = `${m.help} ${m.better ? (m.better === 'lower' ? 'Lower is better.' : 'Higher is better.') : ''} Showing ${rows.length} of ${total} active developers.`;
  const plot = root.querySelector('.plot');
  if (!rows.length) { plot.innerHTML = '<div class="empty">Nobody has a value for this metric.</div>'; return; }
  const W = widthOf(root), labelW = clamp(Math.max(...rows.map(r => textW(r[0], 11, true))) + 48, 120, W * 0.3), valW = 64;
  const rowH = 24, P = { t: 22, b: 8 }, H = P.t + rows.length * rowH + P.b;
  const max = Math.max(...rows.map(r => r[1]), TEAM[key] ?? 0) || 1;
  const x = linear(0, max, labelW, W - valW);
  let g = '';
  rows.forEach(([l, v], i) => {
    const yy = P.t + i * rowH, sel = l === state.a ? 'a' : l === state.b ? 'b' : '';
    if (sel) g += `<rect x="0" y="${yy}" width="${W}" height="${rowH}" rx="6" fill="var(--band)"/>`;
    g += `<text x="4" y="${yy + 16}" class="${sel ? 'ink b' : 'ink2'}">${rankOf.get(l)}.</text>`;
    g += `<text x="34" y="${yy + 16}" class="${sel ? 'ink b' : 'ink2'}">${labelText(l, fitLabel(l, labelW - 44, 11, !!sel))}</text>`;
    const color = sel === 'b' ? COLOR_B : COLOR_A;
    g += `<path class="mark dimmable" data-key="${esc(l)}" d="${roundRight(labelW, yy + 5, Math.max(1.5, x(v) - labelW), rowH - 10, 3)}" fill="${color}"/>`;
    g += `<text x="${Math.max(labelW, x(v)) + 6}" y="${yy + 16}" class="ink">${fmt(key, v)}</text>`;
    g += `<rect class="hit" tabindex="0" data-key="${esc(l)}" x="0" y="${yy}" width="${W}" height="${rowH}" data-tip="${T({ title: l, rows: [
      { color, value: fmt(key, v), label: m.label }, { dash: true, value: fmt(key, TEAM[key]), label: 'team median' }] })}"/>`;
  });
  if (TEAM[key] != null) {
    const mx = x(TEAM[key]);
    g += `<line x1="${mx}" x2="${mx}" y1="${P.t - 4}" y2="${H - P.b}" stroke="var(--ink-2)" stroke-dasharray="3 3"/>`;
    g += `<text x="${mx}" y="${P.t - 8}" text-anchor="middle" class="ink2">team median ${fmt(key, TEAM[key])}</text>`;
  }
  plot.innerHTML = svg(W, H, g, `${m.label} by developer`);
  plot.querySelectorAll('.hit').forEach(h => h.addEventListener('click', () => selectA(h.dataset.key)));
}

// ------------------------------------------------------------------ scatter
function renderScatter() {
  const root = $('#c-scatter'), T = tipper(root), kx = state.sx, ky = state.sy, plot = root.querySelector('.plot');
  const pts = LOGINS.map(l => ({ l, x: DEVS[l].metrics[kx], y: DEVS[l].metrics[ky] }))
    .filter(p => p.x != null && p.y != null && isActive(p.l, M[kx].section) && isActive(p.l, M[ky].section));
  if (pts.length < 2) { plot.innerHTML = '<div class="empty">Not enough developers have both values.</div>'; return; }
  const W = widthOf(root), H = clamp(W * 0.62, 300, 460), P = { l: 48, r: 16, t: 14, b: 40 };
  const scale = (key, vals, r0, r1) => {
    if (M[key].kind === 'hours') { const [lo, hi] = logDomain(vals); const f = logScale(lo, hi, r0, r1); f.log = true; return f; }
    const hi = niceMax(Math.max(...vals)), med = median(vals.filter(v => v > 0));
    if (M[key].kind !== 'pct' && med && Math.max(...vals) > 8 * med) {
      // Skewed counts (one prolific contributor): compress with log(1 + v) so the rest don't pile up at zero.
      const t = v => Math.log10(1 + Math.max(0, v)), s = linear(0, t(hi), r0, r1), f = v => s(t(v));
      f.ticks = [0, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000].filter(v => v <= hi).map(v => [v, fmtN(v)]);
      f.log = true; return f;
    }
    const f = linear(0, hi, r0, r1);
    f.ticks = [0, hi / 2, hi].map(v => [v, fmt(key, v)]); return f;
  };
  const x = scale(kx, pts.map(p => p.x), P.l, W - P.r), y = scale(ky, pts.map(p => p.y), H - P.b, P.t);
  let g = '';
  y.ticks.forEach(([v, lab], i) => g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(${i ? '--grid' : '--axis'})"/><text x="${P.l - 6}" y="${y(v) + 4}" text-anchor="end">${lab}</text>`);
  x.ticks.forEach(([v, lab]) => g += `<line x1="${x(v)}" x2="${x(v)}" y1="${P.t}" y2="${H - P.b}" stroke="var(--grid)"/><text x="${x(v)}" y="${H - P.b + 16}" text-anchor="middle">${lab}</text>`);
  g += `<text x="${(P.l + W - P.r) / 2}" y="${H - 4}" text-anchor="middle" class="ink2">${esc(M[kx].label)}${x.log ? ' (log scale)' : ''} →</text>`;
  g += `<text transform="translate(12 ${(P.t + H - P.b) / 2}) rotate(-90)" text-anchor="middle" class="ink2">${esc(M[ky].label)}${y.log ? ' (log scale)' : ''} →</text>`;
  if (TEAM[kx] != null) g += `<line x1="${x(TEAM[kx])}" x2="${x(TEAM[kx])}" y1="${P.t}" y2="${H - P.b}" stroke="var(--muted)" stroke-dasharray="3 3"/>`;
  if (TEAM[ky] != null) g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y(TEAM[ky])}" y2="${y(TEAM[ky])}" stroke="var(--muted)" stroke-dasharray="3 3"/>`;
  // Draw selected developers last so they sit on top.
  const order = [...pts].sort((p, q) => (p.l === state.a || p.l === state.b) - (q.l === state.a || q.l === state.b));
  const labels = [];
  const tryLabel = (px, py, full, cls) => {
    const text = fitLabel(full, Math.min(170, (W - P.l - P.r) * 0.4), 11, cls.includes(' b'));
    const w = textW(text, 11, cls.includes(' b')) + 4, box = [px + 8, py - 12, px + 8 + w, py + 2];
    if (box[2] > W - P.r) { box[0] = px - 8 - w; box[2] = px - 8; }
    if (box[0] < P.l || box[1] < 0) return '';
    if (labels.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) return '';
    labels.push(box); return `<text x="${box[0]}" y="${py - 1}" class="${cls}">${labelText(full, text)}</text>`;
  };
  let labelSvg = '';
  const byActivity = [...pts].sort((p, q) => q.x - p.x);
  [state.a, state.b].forEach(l => { const p = pts.find(p => p.l === l); if (p) labelSvg += tryLabel(x(p.x), y(p.y), l, 'ink b'); });
  byActivity.slice(0, 6).forEach(p => { if (p.l !== state.a && p.l !== state.b) labelSvg += tryLabel(x(p.x), y(p.y), p.l, 'ink2'); });
  order.forEach(p => {
    const sel = p.l === state.a ? COLOR_A : p.l === state.b ? COLOR_B : null, cx = x(p.x), cy = y(p.y);
    g += `<circle class="mark dimmable" data-key="${esc(p.l)}" cx="${cx}" cy="${cy}" r="${sel ? 6 : 4.5}" fill="${sel || 'var(--c1)'}" fill-opacity="${sel ? 1 : 0.55}" stroke="var(--surface)" stroke-width="2"/>`;
    g += `<circle class="hit" tabindex="0" data-key="${esc(p.l)}" cx="${cx}" cy="${cy}" r="12" data-tip="${T({ title: p.l, rows: [
      { color: sel || 'var(--c1)', value: fmt(kx, p.x), label: M[kx].label }, { color: sel || 'var(--c1)', value: fmt(ky, p.y), label: M[ky].label }] })}"/>`;
  });
  plot.innerHTML = svg(W, H, g + labelSvg, `${M[kx].label} vs ${M[ky].label}`);
  plot.querySelectorAll('.hit').forEach(h => h.addEventListener('click', () => selectA(h.dataset.key)));
}

// ------------------------------------------------------------------ review matrix
const MATRIX_MODES = {
  pct: { label: '% of author\'s PRs', sub: 'Share of each author\'s PRs (open during the window) that the reviewer reviewed or commented on' },
  prs: { label: 'PRs reviewed', sub: 'Distinct PRs of each author that the reviewer reviewed or commented on' },
  events: { label: 'Reviews & comments', sub: 'Every review and comment each reviewer left on each author\'s PRs' },
};
function matrixValue(r, c) {
  if (r === c) return null;
  const prs = (DEVS[r].review_prs || {})[c] || 0;
  if (state.matrix === 'events') return (DEVS[r].interactions || {})[c] || 0;
  if (state.matrix === 'prs') return prs;
  const total = DEVS[c].prs_open_in_window || 0;
  return total ? prs / total : null;
}
function renderMatrix() {
  const root = $('#c-matrix'), T = tipper(root), plot = root.querySelector('.plot'), mode = state.matrix;
  $('#matrix-sub').textContent = MATRIX_MODES[mode].sub;
  const given = l => sum(Object.values(DEVS[l].interactions || {}));
  const received = l => sum(LOGINS.map(o => (DEVS[o].interactions || {})[l] || 0));
  const people = LOGINS.map(l => [l, given(l) + received(l)]).filter(([, t]) => t > 0).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([l]) => l);
  for (const l of [state.a, state.b]) if (l && !people.includes(l) && given(l) + received(l) > 0) people.push(l);
  if (people.length < 2) { plot.innerHTML = '<div class="empty">No cross-developer reviews or comments in this window.</div>'; return; }
  const W = widthOf(root), n = people.length, AX = 20;  // AX: gutter for the "Reviewer" axis title
  const labelW = AX + clamp(Math.max(...people.map(l => textW(l, 11, true))) + 12, 60, W * 0.28);
  const rowLabel = l => fitLabel(l, labelW - AX - 12, 11, true);
  // Column labels are rotated -50°: height = len·sin50, rightward overhang = len·cos50.
  const colMax = clamp(Math.max(...people.map(l => textW(l, 11, true))), 30, 150);
  const colLabel = l => fitLabel(l, colMax, 11, true);
  const colLen = Math.max(...people.map(l => textW(colLabel(l), 11, true)));
  const top = 22 + colLen * 0.77 + 12, overhang = colLen * 0.64;
  const cell = clamp((W - labelW - overhang - 4) / n, 12, 46);
  const footer = mode === 'events' ? 0 : 24, gridH = n * cell, H = top + gridH + footer + 4;
  const vals = people.flatMap(r => people.map(c => matrixValue(r, c))).filter(v => v != null);
  const max = mode === 'pct' ? 1 : Math.max(1, ...vals);
  const show = v => mode === 'pct' ? `${Math.round(v * 100)}%` : fmtN(v);
  const gridMid = labelW + n * cell / 2;
  let g = `<text x="${gridMid}" y="12" text-anchor="middle" class="ink2 b">PR author →</text>`;
  g += `<text transform="translate(12 ${top + gridH / 2}) rotate(-90)" text-anchor="middle" class="ink2 b">Reviewer →</text>`;
  people.forEach((c, j) => {
    const cx = labelW + j * cell + cell / 2, sel = c === state.a || c === state.b;
    g += `<text transform="translate(${cx + 3} ${top - 6}) rotate(-50)" class="${sel ? 'ink b' : ''}">${labelText(c, colLabel(c))}</text>`;
    if (footer) g += `<text x="${cx}" y="${top + gridH + 16}" text-anchor="middle">${fmtN(DEVS[c].prs_open_in_window || 0)}</text>`;
  });
  if (footer) g += `<text x="${labelW - 8}" y="${top + gridH + 16}" text-anchor="end" class="ink2">PRs open</text>`;
  people.forEach((r, i) => {
    const yy = top + i * cell, sel = r === state.a || r === state.b;
    g += `<text x="${labelW - 8}" y="${yy + cell / 2 + 4}" text-anchor="end" class="${sel ? 'ink b' : 'ink2'}">${labelText(r, rowLabel(r))}</text>`;
    people.forEach((c, j) => {
      const v = matrixValue(r, c), xx = labelW + j * cell;
      const op = v ? 0.16 + 0.84 * (mode === 'pct' ? v : Math.sqrt(v / max)) : 0;
      g += `<rect x="${xx + 1}" y="${yy + 1}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="${v ? 'var(--c1)' : 'var(--wash)'}" fill-opacity="${v ? op.toFixed(2) : 1}"/>`;
      if (v && cell >= (mode === 'pct' ? 30 : 22)) g += `<text x="${xx + cell / 2}" y="${yy + cell / 2 + 4}" text-anchor="middle" style="fill:${op > 0.6 ? '#fff' : 'var(--ink)'};font-size:${mode === 'pct' ? 10 : 11}px">${show(v)}</text>`;
      if (r === c) return;
      const prs = (DEVS[r].review_prs || {})[c] || 0, total = DEVS[c].prs_open_in_window || 0;
      const rows = [
        { value: total ? `${Math.round(prs / total * 100)}%` : '–', label: `of ${c}'s PRs` },
        { value: `${fmtN(prs)} of ${fmtN(total)}`, label: 'PRs reviewed or commented on' },
        { value: fmtN((DEVS[r].interactions || {})[c] || 0), label: 'reviews & comments' },
      ];
      g += `<rect class="hit" x="${xx}" y="${yy}" width="${cell}" height="${cell}" data-tip="${T({ title: `${r} reviewing ${c}`, rows })}"/>`;
    });
  });
  plot.innerHTML = svg(W, H, g, 'Who reviews whom');
}

// ------------------------------------------------------------------ strip plot of cycle times
function renderStrip() {
  const root = $('#c-strip'), T = tipper(root), field = state.strip, plot = root.querySelector('.plot');
  let rows = LOGINS.map(l => ({ l, prs: DEVS[l].prs.filter(p => p[field] != null) })).filter(r => r.prs.length >= 2)
    .sort((a, b) => b.prs.length - a.prs.length);
  const topRows = rows.slice(0, 12);
  for (const l of [state.a, state.b]) { const r = rows.find(r => r.l === l); if (r && !topRows.includes(r)) topRows.push(r); }
  rows = topRows;
  if (!rows.length) { plot.innerHTML = '<div class="empty">No developer has two or more PRs with this measure.</div>'; return; }
  const W = widthOf(root), labelW = clamp(Math.max(...rows.map(r => textW(r.l, 11, true))) + 16, 90, W * 0.28), rightW = 118;
  const rowH = 30, P = { t: 8, b: 26 }, H = P.t + rows.length * rowH + P.b;
  const [lo, hi] = logDomain(rows.flatMap(r => r.prs.map(p => p[field])));
  const x = logScale(lo, hi, labelW, W - rightW);
  let g = '';
  x.ticks.forEach(([v, lab]) => g += `<line x1="${x(v)}" x2="${x(v)}" y1="${P.t}" y2="${H - P.b}" stroke="var(--grid)"/><text x="${x(v)}" y="${H - 8}" text-anchor="middle">${lab}</text>`);
  rows.forEach((r, i) => {
    const yy = P.t + i * rowH, mid = yy + rowH / 2, sel = r.l === state.a ? COLOR_A : r.l === state.b ? COLOR_B : null;
    if (sel) g += `<rect x="0" y="${yy}" width="${W}" height="${rowH}" rx="6" fill="var(--band)"/>`;
    g += `<line x1="${labelW}" x2="${W - rightW}" y1="${mid}" y2="${mid}" stroke="var(--grid)"/>`;
    g += `<text x="${labelW - 10}" y="${mid + 4}" text-anchor="end" class="${sel ? 'ink b' : 'ink2'}">${labelText(r.l, fitLabel(r.l, labelW - 16, 11, !!sel))}</text>`;
    r.prs.forEach(p => {
      const cx = x(p[field]), cy = mid + hashJitter(p.number) * (rowH - 14);
      g += `<circle class="mark" cx="${cx}" cy="${cy}" r="4" fill="${sel || 'var(--c1)'}" fill-opacity=".6" stroke="var(--surface)" stroke-width="1.5"/>`;
      g += `<circle class="hit" cx="${cx}" cy="${cy}" r="9" data-tip="${T({ title: `#${p.number} ${p.title}`, rows: [{ color: sel || 'var(--c1)', value: fmtH(p[field]), label: r.l }] })}"/>`;
    });
    const md = median(r.prs.map(p => p[field]));
    g += `<line x1="${x(md)}" x2="${x(md)}" y1="${mid - 10}" y2="${mid + 10}" stroke="var(--ink)" stroke-width="2.5" stroke-linecap="round"/>`;
    g += `<text x="${W - rightW + 12}" y="${mid + 4}" class="ink2">${fmtH(md)} <tspan class="muted" style="fill:var(--muted)">· ${r.prs.length} PRs</tspan></text>`;
  });
  plot.innerHTML = svg(W, H, g, 'Cycle time spread by developer');
}

// ------------------------------------------------------------------ developer table
const COLS = ['prs_opened','prs_merged','time_to_merge_median','time_to_first_review_median','pr_size_median',
  'prs_kept_current','reviews_given','approvals_given','review_comments_given','review_turnaround_median','commits','active_days'];
const SHORT = { prs_opened: 'PRs', prs_merged: 'Merged', time_to_merge_median: 'Time to merge', time_to_first_review_median: 'Wait for feedback',
  pr_size_median: 'PR size', prs_kept_current: 'Kept up to date', reviews_given: 'Reviews', approvals_given: 'Approvals', review_comments_given: 'Review comments',
  review_turnaround_median: 'Review turnaround', commits: 'Commits', active_days: 'Active days' };
let sortKey = null, sortDir = 'desc';
const activity = l => WEEKS.map((_, i) => ['commits', 'prs_opened', 'reviews'].reduce((a, k) => a + DEVS[l].series[k][i], 0));
function renderTable() {
  $('#devtable thead').innerHTML = `<tr><th data-k="login">Developer</th><th>Activity per ${PKIND}</th>` +
    COLS.map(k => `<th data-k="${k}" title="${esc(M[k].help)}">${SHORT[k]}</th>`).join('') + '</tr>';
  const q = $('#filter').value.trim().toLowerCase();
  let rows = LOGINS.filter(l => l.toLowerCase().includes(q) || (ghLogin(l) || '').toLowerCase().includes(q));
  if (sortKey) rows.sort((a, b) => {
    const va = sortKey === 'login' ? a.toLowerCase() : DEVS[a].metrics[sortKey], vb = sortKey === 'login' ? b.toLowerCase() : DEVS[b].metrics[sortKey];
    if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1;
    return (va < vb ? -1 : va > vb ? 1 : 0) * (sortDir === 'asc' ? 1 : -1);
  });
  $('#devtable tbody').innerHTML = rows.map(l => `<tr data-login="${esc(l)}" class="${l === state.a ? 'sel-a' : l === state.b ? 'sel-b' : ''}"><td title="${ghLogin(l) ? '@' + esc(ghLogin(l)) : ''}">${esc(l)}</td>` +
    `<td>${sparkline(activity(l), 90, 20, l === state.b ? COLOR_B : COLOR_A)}</td>` +
    COLS.map(k => `<td>${fmt(k, DEVS[l].metrics[k])}</td>`).join('') + '</tr>').join('') +
    `<tr class="team"><td>team median</td><td></td>${COLS.map(k => `<td>${fmt(k, TEAM[k])}</td>`).join('')}</tr>`;
  $('#count').textContent = `${rows.length} of ${LOGINS.length} developers`;
  document.querySelectorAll('#devtable th').forEach(th => th.dataset.dir = th.dataset.k && th.dataset.k === sortKey ? sortDir : '');
}
$('#devtable thead').addEventListener('click', e => {
  const k = e.target.closest('th')?.dataset.k; if (!k) return;
  sortDir = sortKey === k && sortDir === 'desc' ? 'asc' : 'desc'; sortKey = k; renderTable();
});
$('#devtable tbody').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-login]'); if (!tr) return;
  selectA(tr.dataset.login); $('#detail').scrollIntoView();
});
$('#filter').addEventListener('input', renderTable);

// ------------------------------------------------------------------ developer detail
function percentile(key, v) {
  const m = M[key], ps = peers(key).map(l => DEVS[l].metrics[key]);
  if (v == null || ps.length < 3 || !m.better) return null;
  const below = ps.filter(p => p < v).length, eq = ps.filter(p => p === v).length;
  let f = (below + eq / 2) / ps.length;
  if (m.better === 'lower') f = 1 - f;
  return Math.round(f * 100);
}
function renderProfile(root) {
  const T = tipper(root), plot = root.querySelector('.plot'), A = state.a, B = state.b;
  const who = [A, B].filter(Boolean);
  const rows = [];
  SECTIONS.forEach(s => {
    const ms = R.metrics.filter(m => m.section === s && m.better && who.some(l => isActive(l, s) && percentile(m.key, DEVS[l].metrics[m.key]) != null));
    if (ms.length) { rows.push({ section: s }); ms.forEach(m => rows.push({ m })); }
  });
  if (!rows.length) { plot.innerHTML = '<div class="empty">Not enough comparable peers for a percentile profile.</div>'; return; }
  const W = widthOf(root), labelW = Math.min(220, W * 0.34), valW = B ? 150 : 80, rowH = 24, P = { t: 26, b: 8 };
  const H = P.t + rows.reduce((a, r) => a + (r.section ? 26 : rowH), 0) + P.b;
  const x = linear(0, 100, labelW, W - valW);
  let g = '';
  [0, 25, 50, 75, 100].forEach(v => g += `<line x1="${x(v)}" x2="${x(v)}" y1="${P.t - 4}" y2="${H - P.b}" stroke="var(${v === 50 ? '--axis' : '--grid'})" ${v === 50 ? 'stroke-width="1.5"' : ''}/>` +
    `<text x="${x(v)}" y="${P.t - 10}" text-anchor="middle" class="${v === 50 ? 'ink2' : ''}">${v === 50 ? 'team median' : v === 0 ? 'bottom' : v === 100 ? 'top' : v}</text>`);
  let yy = P.t;
  rows.forEach(r => {
    if (r.section) { g += `<text x="0" y="${yy + 18}" class="ink b" style="font-size:12px;text-transform:uppercase;letter-spacing:.05em">${r.section}</text>`; yy += 26; return; }
    const key = r.m.key, mid = yy + rowH / 2;
    const pa = A && isActive(A, r.m.section) ? percentile(key, DEVS[A].metrics[key]) : null;
    const pb = B && isActive(B, r.m.section) ? percentile(key, DEVS[B].metrics[key]) : null;
    g += `<text x="0" y="${mid + 4}" class="ink2">${esc(r.m.label)}</text>`;
    g += `<line x1="${x(0)}" x2="${x(100)}" y1="${mid}" y2="${mid}" stroke="var(--grid)"/>`;
    if (pa != null && pb != null) g += `<line x1="${x(pa)}" x2="${x(pb)}" y1="${mid}" y2="${mid}" stroke="var(--ink-2)" stroke-width="2"/>`;
    if (pb != null) g += `<circle cx="${x(pb)}" cy="${mid}" r="6" fill="${COLOR_B}" stroke="var(--surface)" stroke-width="2"/>`;
    if (pa != null) g += `<circle cx="${x(pa)}" cy="${mid}" r="6" fill="${COLOR_A}" stroke="var(--surface)" stroke-width="2"/>`;
    const va = A ? fmt(key, DEVS[A].metrics[key]) : '', vb = B ? fmt(key, DEVS[B].metrics[key]) : '';
    g += `<text x="${W - valW + 12}" y="${mid + 4}" class="ink">${va}${B ? `<tspan style="fill:var(--muted)">  vs  </tspan>${vb}` : ''}</text>`;
    const tipRows = [];
    if (A) tipRows.push({ color: COLOR_A, value: pa == null ? '–' : `${pa}th pct`, label: `${A} · ${va}` });
    if (B) tipRows.push({ color: COLOR_B, value: pb == null ? '–' : `${pb}th pct`, label: `${B} · ${vb}` });
    tipRows.push({ dash: true, value: fmt(key, TEAM[key]), label: 'team median' });
    g += `<rect class="hit" x="0" y="${yy}" width="${W}" height="${rowH}" data-tip="${T({ title: r.m.label, rows: tipRows })}"/>`;
    yy += rowH;
  });
  plot.innerHTML = svg(W, H, g, 'Percentile profile');
}

const SERIES_SECTION = { commits: 'Commits', prs_opened: 'Authoring', reviews: 'Reviewing' };
function renderWeeklyLines(root, key, label) {
  const T = tipper(root), plot = root.querySelector('.plot');
  const act = LOGINS.filter(l => isActive(l, SERIES_SECTION[key]));
  const avg = WEEKS.map((_, i) => act.length ? sum(act.map(l => DEVS[l].series[key][i])) / act.length : 0);
  const series = [{ label: state.a, color: COLOR_A, vals: DEVS[state.a].series[key] }];
  if (state.b) series.push({ label: state.b, color: COLOR_B, vals: DEVS[state.b].series[key] });
  series.push({ label: 'team average', dash: true, vals: avg });
  root.querySelector('.legend').innerHTML = legendHTML(series.map(s => ({ label: s.label, color: s.color, kind: s.dash ? 'dash' : 'line' })));
  const W = widthOf(root), H = 170, P = { l: 34, r: 10, t: 10, b: 24 }, n = WEEKS.length;
  const max = niceMax(Math.max(1, ...series.flatMap(s => s.vals)));
  const step = n > 1 ? (W - P.l - P.r) / (n - 1) : 0, x = i => P.l + i * step, y = linear(0, max, H - P.b, P.t);
  let g = yGrid(y, [0, max / 2, max], P.l, W - P.r, v => fmtN(Math.round(v * 10) / 10));
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - P.l) / 60))));
  WEEKS.forEach((w, i) => {
    if (i % every) return;
    const anchor = n > 1 && i === 0 ? 'start' : n > 1 && i === n - 1 ? 'end' : 'middle';  // keep edge labels inside the chart
    g += `<text x="${x(i)}" y="${H - 6}" text-anchor="${anchor}">${w.short}</text>`;
  });
  [...series].reverse().forEach(s => {
    const pts = s.vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    g += `<polyline points="${pts}" fill="none" stroke="${s.dash ? 'var(--muted)' : s.color}" stroke-width="2" ${s.dash ? 'stroke-dasharray="4 3"' : ''} stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  g += `<g class="xhair" style="display:none"><line y1="${P.t}" y2="${H - P.b}" stroke="var(--axis)"/>` +
    series.filter(s => !s.dash).map((s, j) => `<circle r="4.5" data-j="${j}" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`).join('') + '</g>';
  g += `<rect class="overlay" x="${P.l - step / 2}" y="${P.t}" width="${W - P.l - P.r + step}" height="${H - P.t - P.b}" fill="transparent"/>`;
  plot.innerHTML = svg(W, H, g, `${label} per ${PKIND}`);
  const svgEl = plot.querySelector('svg'), xh = svgEl.querySelector('.xhair'), ov = svgEl.querySelector('.overlay');
  ov.addEventListener('pointermove', e => {
    const r = svgEl.getBoundingClientRect(), px = (e.clientX - r.left) * (W / r.width);
    const i = Math.max(0, Math.min(n - 1, Math.round((px - P.l) / (step || 1))));
    xh.style.display = ''; xh.querySelector('line').setAttribute('x1', x(i)); xh.querySelector('line').setAttribute('x2', x(i));
    series.filter(s => !s.dash).forEach((s, j) => { const c = xh.querySelector(`[data-j="${j}"]`); c.setAttribute('cx', x(i)); c.setAttribute('cy', y(s.vals[i])); });
    tipRender({ title: periodTitle(WEEKS[i]), rows: series.map(s => ({ color: s.color, dash: s.dash, value: fmtN(Math.round(s.vals[i] * 10) / 10), label: s.label })) });
    tipMove(e.clientX, e.clientY);
  });
  ov.addEventListener('pointerleave', () => { xh.style.display = 'none'; tipHide(); });
}

function heatmapSVG(root, h, color, T, who) {
  const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], L = 32, TOP = 16;
  const W = widthOf(root) / (state.b ? 2 : 1) - (state.b ? 12 : 0);
  const cell = Math.max(9, Math.min(22, (W - L) / 24 - 2)), gap = 2;
  const max = Math.max(1, ...h.flat());
  let g = '';
  for (let hr = 0; hr < 24; hr += 3) g += `<text x="${L + hr * (cell + gap) + cell / 2}" y="11" text-anchor="middle">${hr}</text>`;
  h.forEach((row, di) => {
    g += `<text x="${L - 6}" y="${TOP + di * (cell + gap) + cell / 2 + 4}" text-anchor="end">${days[di]}</text>`;
    row.forEach((v, hr) => {
      const op = v ? 0.18 + 0.82 * v / max : 1;
      g += `<rect x="${L + hr * (cell + gap)}" y="${TOP + di * (cell + gap)}" width="${cell}" height="${cell}" rx="3" fill="${v ? color : 'var(--wash)'}" fill-opacity="${op.toFixed(2)}"/>`;
      g += `<rect class="hit" x="${L + hr * (cell + gap)}" y="${TOP + di * (cell + gap)}" width="${cell + gap}" height="${cell + gap}" data-tip="${T({ title: `${who} · ${days[di]} ${String(hr).padStart(2, '0')}:00`, rows: [{ color, value: fmtN(v), label: v === 1 ? 'commit' : 'commits' }] })}"/>`;
    });
  });
  const w = L + 24 * (cell + gap), hh = TOP + 7 * (cell + gap);
  return `<div><div class="chart-sub" style="margin-bottom:4px">${esc(who)}</div>${svg(w, hh, g, `Commits by weekday and hour for ${who}`)}</div>`;
}

function compareChip(k, v) {
  const m = M[k], t = TEAM[k];
  if (v == null || t == null || !m.better || t === 0) return '';
  const r = v / t;
  if (r >= 0.8 && r <= 1.25) return '<span class="cmp same">≈ team</span>';
  const good = (r > 1) === (m.better === 'higher');
  const txt = r > 1 ? `${r >= 10 ? Math.round(r) : r.toFixed(1)}× team` : `${Math.round((1 - r) * 100)}% below`;
  return `<span class="cmp ${good ? 'good' : 'bad'}">${txt}</span>`;
}
function metricTable(section) {
  const A = DEVS[state.a], B = state.b && DEVS[state.b];
  const rows = R.metrics.filter(m => m.section === section).map(m => {
    const va = A.metrics[m.key], rank = A.ranks[m.key];
    return `<tr><td class="metric"><span class="metric-name">${esc(m.label)}<small>${esc(m.help)}</small></span></td>` +
      `<td><b>${fmt(m.key, va)}</b></td>${B ? `<td>${fmt(m.key, B.metrics[m.key])}</td>` : ''}` +
      `<td class="muted">${fmt(m.key, TEAM[m.key])}</td><td>${compareChip(m.key, va)}</td>` +
      `<td class="muted">${rank ? `${rank[0]} of ${rank[1]}` : ''}</td></tr>`;
  }).join('');
  return `<div class="card"><h3>${section}</h3><div class="tablewrap"><table><thead><tr><th>Metric</th><th>${esc(state.a)}</th>` +
    `${B ? `<th>${esc(state.b)}</th>` : ''}<th>Team median</th><th>vs. team</th><th>Rank</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderDetail() {
  const body = $('#detail-body'), A = state.a, d = DEVS[A], m = d.metrics, B = state.b;
  const insights = (l, color) => {
    const ins = DEVS[l].insights;
    return `<div class="card"><h3><span style="color:${color}">●</span> ${esc(l)} · observations</h3>` + (ins.length
      ? `<ul class="insights">${ins.map(i => `<li class="${i.kind}"><span class="k">${{ strength: 'Strength', improve: 'Improve', watch: 'Watch' }[i.kind]}</span><span>${esc(i.text)}</span></li>`).join('')}</ul>`
      : '<p class="muted" style="margin:0">Nothing stands out against the team baseline in this window.</p>') + '</div>';
  };
  const prs = d.prs.slice(0, 12).map(p => `<tr><td class="title" title="${esc(p.title)}"><a href="${esc(p.url)}" target="_blank" rel="noopener">#${p.number}</a> ${esc(p.title)}</td>` +
    `<td>${p.state.toLowerCase()}</td><td>${fmtN(p.size)}</td><td>${fmtH(p.hours_to_first_review)}</td><td>${fmtH(p.hours_to_merge)}</td></tr>`).join('');
  body.innerHTML = `
    <div class="dev-bar">
      <span class="who"><i style="background:${COLOR_A}"></i>${esc(A)}${ghLogin(A) ? ` <span class="stats">@${esc(ghLogin(A))}</span>` : ''}</span>
      <span class="stats">${fmtN(m.prs_opened)} PRs · ${fmtN(m.reviews_given)} reviews · ${fmtN(m.commits)} commits · ${fmtN(m.active_days)} active days</span>
      ${B ? `<span class="who b"><i style="background:${COLOR_B}"></i>${esc(B)}${ghLogin(B) ? ` <span class="stats">@${esc(ghLogin(B))}</span>` : ''}</span>
      <span class="stats">${fmtN(DEVS[B].metrics.prs_opened)} PRs · ${fmtN(DEVS[B].metrics.reviews_given)} reviews · ${fmtN(DEVS[B].metrics.commits)} commits</span>` : ''}
    </div>
    <div class="${B ? 'grid2' : ''}">${insights(A, COLOR_A)}${B ? insights(B, COLOR_B) : ''}</div>
    <div class="card chart" id="c-profile">
      <div class="chart-head"><div><div class="chart-title">Percentile profile</div>
        <div class="chart-sub">Where they sit among active peers on each metric · right is better · 50 = team median</div></div></div>
      ${B ? `<div class="legend">${legendHTML([{ label: A, color: COLOR_A }, { label: B, color: COLOR_B }])}</div>` : ''}
      <div class="plot"></div>
    </div>
    <div class="grid3">
      ${[['commits', 'Commits'], ['prs_opened', 'PRs opened'], ['reviews', 'Reviews given']].map(([k, l]) =>
        `<div class="card chart" id="c-w-${k}"><div class="chart-title">${l} per ${PKIND}</div><div class="legend"></div><div class="plot"></div></div>`).join('')}
    </div>
    <div class="card chart" id="c-heat"><div class="chart-head"><div><div class="chart-title">When they commit</div>
      <div class="chart-sub">Author's local time · darker = more commits</div></div></div><div class="plot" style="display:flex;gap:24px;flex-wrap:wrap"></div></div>
    <div class="card"><div class="chart-title">Recent PRs · ${esc(A)}</div><div class="chart-sub" style="margin-bottom:6px">Opened in this window</div>
      ${prs ? `<div class="tablewrap"><table><thead><tr><th>PR</th><th>State</th><th>Lines</th><th>First feedback</th><th>Merged after</th></tr></thead><tbody>${prs}</tbody></table></div>` : '<p class="muted">No PRs opened in this window.</p>'}</div>
    ${SECTIONS.map(metricTable).join('')}`;
  renderProfile($('#c-profile'));
  renderWeeklyLines($('#c-w-commits'), 'commits', 'Commits');
  renderWeeklyLines($('#c-w-prs_opened'), 'prs_opened', 'PRs opened');
  renderWeeklyLines($('#c-w-reviews'), 'reviews', 'Reviews');
  const heat = $('#c-heat'), T = tipper(heat);
  heat.querySelector('.plot').innerHTML = heatmapSVG(heat, d.heatmap, COLOR_A, T, A) + (B ? heatmapSVG(heat, DEVS[B].heatmap, COLOR_B, T, B) : '');
}

// ------------------------------------------------------------------ selection & wiring
const ghLogin = l => DEVS[l].github_login && DEVS[l].github_login !== l ? DEVS[l].github_login : null;
function pickers() {
  const opts = LOGINS.map(l => `<option value="${esc(l)}">${esc(l)}${ghLogin(l) ? ` (@${esc(ghLogin(l))})` : ''}</option>`).join('');
  $('#pick-a').innerHTML = opts; $('#pick-b').innerHTML = '<option value="">— nobody —</option>' + opts;
  $('#pick-a').value = state.a; $('#pick-b').value = state.b || '';
}
function selectA(l) { if (!DEVS[l]) return; state.a = l; if (state.b === l) state.b = null; syncPickers(); renderSelectionDependent(); }
function syncPickers() { $('#pick-a').value = state.a; $('#pick-b').value = state.b || ''; }
$('#pick-a').addEventListener('change', e => selectA(e.target.value));
$('#pick-b').addEventListener('change', e => { state.b = e.target.value && e.target.value !== state.a ? e.target.value : null; syncPickers(); renderSelectionDependent(); });

function renderSelectionDependent() {
  renderLeader(); renderScatter(); renderMatrix(); renderStrip(); renderTable(); renderDetail();
}
function renderAll() {
  renderTiles(); renderStacked();
  renderHistogram('#c-hist-merge', 'hours_to_merge'); renderHistogram('#c-hist-fb', 'hours_to_first_review');
  renderSelectionDependent();
}

segmented($('#stack-seg'), STACK_SERIES, state.stack, v => { state.stack = v; renderStacked(); });
segmented($('#leader-n'), [['15', 'Top 15'], ['0', 'All']], String(state.leaderN), v => { state.leaderN = +v; renderLeader(); });
segmented($('#matrix-seg'), Object.entries(MATRIX_MODES).map(([k, m]) => [k, m.label]), state.matrix, v => { state.matrix = v; renderMatrix(); });
segmented($('#strip-seg'), [['hours_to_merge', 'Time to merge'], ['hours_to_first_review', 'Wait for feedback']], state.strip, v => { state.strip = v; renderStrip(); });
metricOptions($('#leader-metric'), state.leader);
metricOptions($('#sc-x'), state.sx); metricOptions($('#sc-y'), state.sy);
$('#leader-metric').addEventListener('change', e => { state.leader = e.target.value; renderLeader(); });
$('#sc-x').addEventListener('change', e => { state.sx = e.target.value; renderScatter(); });
$('#sc-y').addEventListener('change', e => { state.sy = e.target.value; renderScatter(); });
$('#stack-title').textContent = `Activity per ${PKIND} by developer`;
$('#gloss').innerHTML = R.metrics.map(m => `<dt>${esc(m.label)}</dt><dd>${esc(m.help)}</dd>`).join('');

// Theme toggle: auto -> light -> dark (remembered per browser when storage is available).
const THEMES = ['auto', 'light', 'dark'];
function applyTheme(t) {
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  $('#theme').textContent = `Theme: ${t}`;
}
let theme = 'auto';
try { theme = localStorage.getItem('gitstat-theme') || 'auto'; } catch (e) {}
applyTheme(theme);
$('#theme').addEventListener('click', () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; applyTheme(theme);
  try { localStorage.setItem('gitstat-theme', theme); } catch (e) {}
});

state.a = R.initial && DEVS[R.initial] ? R.initial : LOGINS[0];
state.b = R.compare && DEVS[R.compare] && R.compare !== state.a ? R.compare : null;
if (!LOGINS.length) { document.querySelector('main').insertAdjacentHTML('beforeend', '<p class="empty">No activity found in this window.</p>'); return; }
pickers();
renderAll();
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(renderAll, 150); });
})();
