'use strict';
(() => {
  // ---- helpers ------------------------------------------------------------------------------------------------
  const $ = (s, el = document) => el.querySelector(s);
  const SVGNS = 'http://www.w3.org/2000/svg';
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style') el.style.cssText = v;   // CSSOM, allowed by the strict CSP
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function s(tag, attrs) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    return el;
  }
  // replaceChildren turns null into the text "null", so optional pieces go through this
  const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  // Censor mode (the eye button): your dollar amounts and share counts read as dots; percentages, prices and ratios
  // stay. money() is for your amounts, price() for public prices. Text from the server is covered by scrubText below.
  let CENSOR = false;
  try { CENSOR = localStorage.getItem('basis.censor') === 'on'; } catch (e) { /* no storage */ }
  const HID = '\u2022\u2022\u2022';
  const dollars = (v, dp) => {
    const d = dp != null ? dp : Math.abs(v) >= 1000 ? 0 : 2;
    return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  const money = (v, dp) => (!isNum(v) ? '-' : CENSOR ? (v < 0 ? '-$' : '$') + HID : dollars(v, dp));
  const smoney = (v, dp) => (!isNum(v) ? '-' : (v > 0 ? '+' : '') + money(v, dp));
  // a public price: never hidden (the word joiner after the $ keeps scrubText off it)
  const price = (v, dp) => (!isNum(v) ? '-' : CENSOR ? dollars(v, dp).replace('$', '$\u2060') : dollars(v, dp));
  // a dollar change with its percent; censored, the percent alone
  const chgText = (v, p, dp = 2, mdp = 0) => (CENSOR ? spct(p, dp) : `${smoney(v, mdp)} (${spct(p, dp)})`);
  const SCRUB = /\$\s?\d[\d,]*(\.\d+)?(\s?(k|K|M|million|billion|trillion)\b)?/g;
  const scrubText = (root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = n.nodeValue;
      if (t && t.indexOf('$') >= 0 && SCRUB.test(t)) { SCRUB.lastIndex = 0; n.nodeValue = t.replace(SCRUB, '$' + HID); }
      SCRUB.lastIndex = 0;
    }
  };
  const pct = (v, dp = 1) => (!isNum(v) ? '-' : (v * 100).toFixed(dp) + '%');
  const spct = (v, dp = 1) => (!isNum(v) ? '-' : (v > 0 ? '+' : '') + (v * 100).toFixed(dp) + '%');
  const qty = (v) => (!isNum(v) ? '-' : CENSOR ? HID : Number.isInteger(Math.round(v * 1e4) / 1e4) ? Math.round(v).toLocaleString('en-US') : v.toLocaleString('en-US', { maximumFractionDigits: 4 }));
  const tone = (v) => (!isNum(v) || Math.abs(v) < 1e-9 ? '' : v > 0 ? 'up' : 'down');
  const shortDate = (iso) => { if (!iso) return '-'; const [y, m, d] = iso.split('-'); return `${+m}/${+d}/${y.slice(2)}`; };
  const icon = (d) => { const v = s('svg', { viewBox: '0 0 24 24', class: 'icon', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round' }); v.append(s('path', { d })); return v; };
  const WARN_ICON = 'M12 3l10 18H2L12 3zM12 10v5M12 18v.5';

  async function api(path, opts = {}) {
    const o = { headers: { 'X-Requested-With': 'finance' }, ...opts };
    if (opts.json !== undefined) { o.method = 'POST'; o.body = JSON.stringify(opts.json); o.headers['Content-Type'] = 'application/json'; }
    const r = await fetch(path, o);
    const data = await r.json().catch(() => ({ error: 'bad response' }));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  // ---- state and routing --------------------------------------------------------------------------------------
  const ST = { model: null, page: 'home', range: 'All', mode: 'value', sort: ['value', -1], filter: '', acct: null,
               actFilter: { kind: '', symbol: '', account: '' }, menu: null, setup: null };
  const PAGES = { '/': 'home', '/positions': 'positions', '/options': 'options', '/income': 'income', '/analysis': 'analysis',
                  '/risk': 'risk', '/activity': 'activity', '/settings': 'settings', '/market': 'market', '/doom': 'doom', '/assets': 'assets', '/customize': 'customize' };
  const main = $('#main');

  function go(path, push = true) {
    if (push && location.pathname !== path) history.pushState({}, '', path);
    ST.page = PAGES[path] || 'home';
    document.querySelectorAll('[data-page]').forEach((a) => a.classList.toggle('on', a.dataset.page === ST.page));
    closeSheet();
    render();
    main.scrollTop = 0; window.scrollTo(0, 0);
  }
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href^="/"]');
    if (a && !e.metaKey && !e.ctrlKey && PAGES[a.getAttribute('href').split('?')[0]] !== undefined) { e.preventDefault(); go(a.getAttribute('href')); }
  });
  window.addEventListener('popstate', () => go(location.pathname, false));
  $('#moreBtn').addEventListener('click', openMore);

  async function load(force) {
    try {
      ST.model = await api('/api/model' + (force ? '?force=1' : ''));
      ST.error = null;
    } catch (e) { ST.error = e.message; }
    syncInfo();
  }
  function syncInfo() {
    const m = ST.model; if (!m) return;
    const sy = m.sync || {};
    const t = sy.quotes ? new Date(sy.quotes).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : 'not yet';
    fill($('#syncInfo'), h('i', { class: sy.session || '' }), h('b', { style: 'font-weight:500;color:var(--body)', text: { regular: 'Market open', pre: 'Pre-market', post: 'After hours', closed: 'Market closed' }[sy.session] || '' }),
      h('span', { text: `Prices ${t}` }));
    fill($('#searchList'), ...m.positions.map((p) => h('option', { value: p.symbol.replace('-USD', ''), label: p.name || '' })));
  }
  // top bar: jump to a symbol, hide the amounts
  $('#searchForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const box = $('#searchBox'), q = box.value.trim().toUpperCase();
    if (!q || !/^[A-Z0-9.^=-]{1,12}$/.test(q)) return;
    const held = ST.model && ST.model.positions.find((p) => p.symbol === q || p.symbol === q + '-USD');
    box.value = ''; box.blur();
    openSymbol(held ? held.symbol : q);
  });
  const censorBtn = $('#censorBtn');
  const showCensor = () => { censorBtn.classList.toggle('on', CENSOR); censorBtn.setAttribute('aria-pressed', String(CENSOR)); censorBtn.title = CENSOR ? 'Show dollar amounts' : 'Hide dollar amounts'; };
  censorBtn.addEventListener('click', () => {
    CENSOR = !CENSOR;
    try { localStorage.setItem('basis.censor', CENSOR ? 'on' : 'off'); } catch (e) { /* no storage */ }
    showCensor(); hideTip(); closeSheet(); render();
  });
  showCensor();
  // amounts inside text the server wrote (notes, reasons) are dotted out as they reach the page
  new MutationObserver((list) => {
    if (!CENSOR) return;
    for (const mu of list) {
      if (mu.type === 'characterData') { const t = mu.target.nodeValue; if (t && t.indexOf('$') >= 0 && SCRUB.test(t)) { SCRUB.lastIndex = 0; mu.target.nodeValue = t.replace(SCRUB, '$' + HID); } SCRUB.lastIndex = 0; }
      else for (const nd of mu.addedNodes) { if (nd.nodeType === 3) { const t = nd.nodeValue; if (t && t.indexOf('$') >= 0 && SCRUB.test(t)) { SCRUB.lastIndex = 0; nd.nodeValue = t.replace(SCRUB, '$' + HID); } SCRUB.lastIndex = 0; } else if (nd.nodeType === 1) scrubText(nd); }
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });

  function render() {
    const m = ST.model;
    if (!m) { fill(main, h('div', { class: 'loading', text: ST.error ? 'Could not load: ' + ST.error : 'Loading' })); return; }
    const fn = { home, positions, options, income, analysis, risk, activity, settings, market, doom: doomPage, assets: assetsPage, customize: customizePage }[ST.page] || home;
    const page = h('div', { class: 'page' + (['market', 'analysis', 'income', 'options'].includes(ST.page) ? ' mid-w' : '') });
    fill(main, page);
    desktopHooks();
    if (m.app && m.app.mode === 'desktop' && (!m.app.ready || ST.welcome) && ST.page !== 'customize') { welcomePage(page); return; }
    document.body.classList.remove('setup');
    fn(page, m);
  }

  // ---- charts -------------------------------------------------------------------------------------------------
  const tip = $('#tip');
  function showTip(x, y, title, rows) {
    fill(tip, h('div', { class: 't', text: title }), ...rows.map((r) => {
      const key = h('span'); if (r.color) { const i = h('i'); i.style.background = r.color; key.append(i); } key.append(r.label);
      return h('div', { class: 'r' }, key, h('b', { class: r.cls || '', text: r.value }));
    }));
    tip.hidden = false;
    const w = tip.offsetWidth, hh = tip.offsetHeight;
    tip.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x + 14)) + 'px';
    tip.style.top = Math.max(8, Math.min(window.innerHeight - hh - 8, y - hh - 12)) + 'px';
  }
  const hideTip = () => { tip.hidden = true; };

  function niceTicks(lo, hi, n = 4) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => span / k <= n + 0.5) || mag * 10;
    const out = []; for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.01; v += step) out.push(+v.toFixed(10));
    return out;
  }
  // fewest decimals that still tell neighbouring ticks apart
  const stepDec = (step) => { for (let d = 0; d < 4; d++) { const k = step * Math.pow(10, d); if (Math.abs(k - Math.round(k)) < 1e-6) return d; } return 4; };
  const axisMoney = (v, step, big) => {
    if (CENSOR) return '$' + HID;
    if (step == null) return Math.abs(v) >= 1000 ? (v < 0 ? '-$' : '$') + (Math.abs(v) / 1000).toFixed(Math.abs(v) >= 10000 ? 0 : 1) + 'k' : money(v, 0);
    if (step < 100 || big < 10000 || v === 0) return money(v, 0);
    return (v < 0 ? '-$' : '$') + (Math.abs(v) / 1000).toFixed(stepDec(step / 1000)) + 'k';
  };
  const axisPct = (v, step) => (v * 100).toFixed(stepDec(step * 100)) + '%';

  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthYear = (ym) => MON[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4);
  const CHAR_W = 6.8;                        // 11px IBM Plex Mono
  function weekOf(d) {                       // Monday of the date's week
    const t = new Date(d + 'T12:00:00Z'); t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); return t.toISOString().slice(0, 10);
  }
  // Date axis: ticks on the first point of each calendar period (day, week, month, quarter, year), the finest step whose
  // labels have room. January is labelled with its year.
  function dateTicks(dates, xpos) {
    const n = dates.length; if (n < 2) return [];
    const span = (new Date(dates[n - 1]) - new Date(dates[0])) / 864e5;
    const md = (d) => MON[+d.slice(5, 7) - 1] + ' ' + +d.slice(8, 10);
    const mo = (d) => (d.slice(5, 7) === '01' ? d.slice(0, 4) : MON[+d.slice(5, 7) - 1]);
    const cands = [
      span <= 16 && { key: (d) => d, keep: () => true, label: md },
      { key: weekOf, keep: () => true, label: md },
      { key: weekOf, keep: (d, k) => k % 2 === 0, label: md },
      ...[1, 2, 3, 6].map((e) => ({ key: (d) => d.slice(0, 7), keep: (d) => (+d.slice(5, 7) - 1) % e === 0, label: mo })),
      ...[1, 2, 5, 10, 25].map((e) => ({ key: (d) => d.slice(0, 4), keep: (d) => +d.slice(0, 4) % e === 0, label: (d) => d.slice(0, 4) })),
    ].filter(Boolean);
    let best = null;
    for (const c of cands) {
      const out = []; let prev = c.key(dates[0]), k = 0;
      for (let i = 1; i < n; i++) { const kk = c.key(dates[i]); if (kk !== prev) { if (c.keep(dates[i], k)) out.push([i, c.label(dates[i])]); k++; prev = kk; } }
      if (!out.length) continue;
      const need = Math.max(...out.map(([, l]) => l.length)) * CHAR_W + 18;
      let gap = Infinity; for (let j = 1; j < out.length; j++) gap = Math.min(gap, xpos(out[j][0]) - xpos(out[j - 1][0]));
      best = out;
      if (gap >= Math.max(need, 56)) return out;
    }
    return best || [];
  }
  // Clock axis (Eastern time): ticks on round minutes, the finest step with room. On the hour reads "7 PM", else "7:10".
  const clockLabel = (et) => { const d = new Date(et * 1000), hh = d.getUTCHours(), mm = d.getUTCMinutes(); return (hh % 12 || 12) + (mm ? ':' + String(mm).padStart(2, '0') : ' ' + (hh < 12 ? 'AM' : 'PM')); };
  function timeTicks(t0, t1, off, plotW, minStep = 1) {
    const span = Math.max(1, (t1 - t0) / 60);
    const steps = [1, 2, 5, 10, 15, 30, 60, 120, 180, 240, 360].filter((x) => x >= minStep);
    const step = (steps.find((x) => (x / span) * plotW >= 64) || steps[steps.length - 1]) * 60;
    const out = [];
    for (let t = Math.ceil((t0 + off) / step) * step - off; t <= t1 + 0.5; t += step) out.push([t, clockLabel(t + off)]);
    return out;
  }

  // charts that share a hover line: group -> id -> {svg, keys, tol, show(i), hide()}
  const SYNC = {};
  const nearestKey = (keys, k) => {
    if (!keys.length) return -1;
    let a = 0, b = keys.length - 1;
    while (b - a > 1) { const mid = (a + b) >> 1; if (keys[mid] < k) a = mid; else b = mid; }
    return Math.abs(keys[a] - k) <= Math.abs(keys[b] - k) ? a : b;
  };
  const dayKeys = (dates) => dates.map((d) => Date.parse(d + 'T12:00:00Z') / 86400000);
  function lineChart(el, cfg) {
    // cfg: {dates, series:[{name,color,values,dash,dot}], fmt, yfmt, height, marks:[{i, v, color}], extraTip(i)}
    // cfg.sync {group, id, keys, tol, label(i) -> [name, value]}: hovering one chart of a group shows the same moment
    // (nearest key within tol) on the others, with their value in a tag at the top of the line
    // x axis: by default one step per point with calendar ticks from dates; or cfg.x (a number per point, e.g. unix
    // seconds) over cfg.xdomain [min, max] with cfg.xticks [[value, label]]; cfg.ticks [[index, label]] overrides.
    const W = Math.max(280, el.clientWidth || 600), H = cfg.height || 260, padR = 12, padT = 10, padB = 26;
    const n = cfg.dates.length;
    // censored: a chart of your dollars is drawn as percent change from the first point (same shape, no amounts); the
    // hover gives each line's own change since the start of the range
    if (CENSOR && String((cfg.fmt || money)(1234.5)).includes(HID)) {
      const base = cfg.series[0].values.find(isNum);
      if (isNum(base) && base > 1e-9) {
        const tr = (v) => (isNum(v) ? v / base - 1 : v), raw = cfg.series.map((sr) => sr.values);
        const own = raw.map((vals) => vals.find((v) => isNum(v) && Math.abs(v) > 1e-9));
        cfg = { ...cfg, series: cfg.series.map((sr) => ({ ...sr, values: sr.values.map(tr) })), yfmt: axisPct, zero: false,
          refs: (cfg.refs || []).map((r) => ({ ...r, y: tr(r.y) })), marks: (cfg.marks || []).map((mk) => ({ ...mk, v: tr(mk.v) })),
          markers: cfg.markers ? cfg.markers.map((mk) => ({ ...mk, value: tr(mk.value) })) : null,
          tipVal: (k, i) => (isNum(raw[k][i]) && own[k] ? spct(raw[k][i] / own[k] - 1, 1) : '-') };
      } else cfg = { ...cfg, yfmt: () => '' };
    }
    const all = cfg.series.flatMap((sr) => sr.values.filter(isNum));
    if (!n || !all.length) { fill(el, h('div', { class: 'faint', text: 'No data for this range yet.' })); return; }
    let lo = Math.min(...all, ...(cfg.refs || []).map((r) => r.y)), hi = Math.max(...all, ...(cfg.refs || []).map((r) => r.y));
    if (cfg.ymax && hi > cfg.ymax) hi = cfg.ymax;
    if (cfg.zero && lo > 0) lo = 0;
    if (cfg.yrange) [lo, hi] = cfg.yrange;
    const ticks = niceTicks(lo, hi); lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
    const ystep = ticks.length > 1 ? ticks[1] - ticks[0] : 1, ybig = Math.max(...ticks.map(Math.abs));
    const ylab = ticks.map((t) => (cfg.yfmt || axisMoney)(t, ystep, ybig));
    const padL = Math.max(40, Math.ceil(Math.max(...ylab.map((l) => l.length)) * CHAR_W) + 14);
    const xv = cfg.x || null, plotW = W - padL - padR;
    const [d0, d1] = cfg.xdomain || (xv ? [xv[0], xv[n - 1]] : [0, n - 1]);
    const xs = (v) => padL + (d1 === d0 ? 0 : ((v - d0) / (d1 - d0)) * plotW);
    const x = (i) => xs(xv ? xv[i] : i);
    // a bar strip under the plot (cfg.strip: {values, colors, height, fmt}), sharing the x axis like a volume pane
    const stripH = cfg.strip ? (cfg.strip.height || 56) : 0, stripGap = cfg.strip ? 12 : 0, plotB = H - padB - stripH - stripGap;
    const y = (v) => padT + (1 - (v - lo) / (hi - lo || 1)) * (plotB - padT);
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': cfg.label || 'chart' });
    const g = s('g', { class: 'grid' }), ax = s('g', { class: 'axis' });
    for (const t of ticks) {
      g.append(s('line', { x1: padL, x2: W - padR, y1: y(t), y2: y(t) }));
      const tx = s('text', { x: padL - 8, y: y(t) + 4, 'text-anchor': 'end' }); tx.textContent = ylab[ticks.indexOf(t)]; ax.append(tx);
    }
    const xt = cfg.ticks ? cfg.ticks.map(([i, l]) => [x(i), l]) : cfg.xticks ? cfg.xticks.map(([v, l]) => [xs(v), l]) : dateTicks(cfg.dates, x).map(([i, l]) => [x(i), l]);
    const base = H - padB;
    ax.append(s('line', { class: 'base', x1: padL, x2: W - padR, y1: base, y2: base }));
    let right = -Infinity;                     // a label that would touch the one before it is left off (its tick stays)
    for (const [px, label] of xt) {
      if (px < padL - 0.5 || px > W - padR + 0.5) continue;
      ax.append(s('line', { class: 'tick', x1: px, x2: px, y1: base, y2: base + 4 }));
      const w = label.length * CHAR_W, anchor = px + w / 2 > W - 1 ? 'end' : px - w / 2 < 1 ? 'start' : 'middle';
      const tx0 = anchor === 'end' ? Math.min(px + 4, W - 1) : px, left = anchor === 'start' ? tx0 : anchor === 'end' ? tx0 - w : tx0 - w / 2;
      if (left < right + 8) continue;
      right = left + w;
      const tx = s('text', { x: tx0, y: H - 5, 'text-anchor': anchor }); tx.textContent = label; ax.append(tx);
    }
    svg.append(g, ax);
    for (const vb of cfg.vbands || []) {
      const x0 = vb.x0 != null ? xs(Math.max(d0, vb.x0)) : x(vb.i0), x1 = vb.x1 != null ? xs(Math.min(d1, vb.x1)) : x(vb.i1);
      svg.append(s('rect', { x: Math.min(x0, x1), y: padT, width: Math.max(1, Math.abs(x1 - x0)), height: plotB - padT, fill: vb.fill }));
    }
    for (const b of cfg.bands || []) {
      const y0 = y(Math.min(b.hi, hi)), y1 = y(Math.max(b.lo, lo));
      if (y1 > y0) svg.append(s('rect', { x: padL, y: y0, width: W - padL - padR, height: y1 - y0, fill: b.fill }));
    }
    for (const r of cfg.refs || []) {
      if (r.y < lo || r.y > hi) continue;
      svg.append(s('line', { x1: padL, x2: W - padR, y1: y(r.y), y2: y(r.y), stroke: r.color || 'var(--s-ref)', 'stroke-width': 1, 'stroke-dasharray': r.dash || '4 3' }));
    }
    if (lo < 0 && hi > 0) svg.append(s('line', { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: 'var(--dim)', 'stroke-width': 1 }));
    const clamp = (v) => (cfg.ymax ? Math.min(v, hi) : v);
    for (const sr of [...cfg.series].sort((a, b) => (b.dot || b.dash ? 1 : 0) - (a.dot || a.dash ? 1 : 0))) {
      let d = '', pen = false;
      sr.values.forEach((v, i) => { if (isNum(v)) { d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(clamp(v)).toFixed(1); pen = true; } else pen = false; });
      svg.append(s('path', { d, fill: 'none', stroke: sr.color, 'stroke-width': sr.width || (sr.dash ? 1.5 : sr.dot ? 2 : 2.25), 'stroke-dasharray': sr.dash ? '4 3' : sr.dot ? '1 4' : null,
        'stroke-opacity': sr.opacity || (sr.dot ? 0.7 : null), 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    }
    if (cfg.strip && cfg.strip.zero) {
      // bars up and down from a zero line (a day's change), the largest each way labeled
      const st = cfg.strip, top = plotB + stripGap, bot = H - padB - 1, fin = st.values.filter(isNum);
      if (fin.length) {
        const mx = Math.max(0, ...fin), mn = Math.min(0, ...fin), span = mx - mn || 1;
        const zy = (v) => top + ((mx - v) / span) * (bot - top), y0 = zy(0);
        const bw = Math.max(1, Math.min(14, (plotW / Math.max(n - 1, 1)) * 0.7));
        svg.append(s('line', { class: 'pane', x1: padL, x2: W - padR, y1: top - stripGap / 2, y2: top - stripGap / 2 }));
        svg.append(s('line', { class: 'pane', x1: padL, x2: W - padR, y1: y0, y2: y0 }));
        st.values.forEach((v, i) => {
          if (!isNum(v)) return;
          const x0 = Math.max(padL, x(i) - bw / 2), x1 = Math.min(W - padR, x(i) + bw / 2), yy = zy(v);
          svg.append(s('rect', { x: x0, y: Math.min(yy, y0), width: Math.max(1, x1 - x0), height: Math.max(1, Math.abs(y0 - yy)), fill: st.colors[i] }));
        });
        for (const [v, yy] of [[mx, zy(mx) + 9], [mn, zy(mn) - 1]]) {
          if (Math.abs(v) < 1e-9) continue;
          const tx = s('text', { x: padL - 8, y: Math.max(top + 9, Math.min(bot, yy)), 'text-anchor': 'end' }); tx.textContent = (st.fmt || String)(v); ax.append(tx);
        }
      }
    } else if (cfg.strip) {
      const st = cfg.strip, top = plotB + stripGap, bot = H - padB - 1, fin = st.values.filter(isNum);
      if (fin.length) {
        const smax = Math.max(...fin), smin = Math.min(...fin), span = smax - smin || Math.abs(smax) * 0.05 || 1;
        const floor = smin - span * 0.35;              // bars start below the lowest day so every bar shows; the range is labeled
        const sy = (v) => bot - ((v - floor) / (smax - floor || 1)) * (bot - top);
        const bw = Math.max(1, Math.min(14, (plotW / Math.max(n - 1, 1)) * 0.7));
        svg.append(s('line', { class: 'pane', x1: padL, x2: W - padR, y1: top - stripGap / 2, y2: top - stripGap / 2 }));
        st.values.forEach((v, i) => {
          if (!isNum(v)) return;
          const x0 = Math.max(padL, x(i) - bw / 2), x1 = Math.min(W - padR, x(i) + bw / 2), yy = sy(v);
          svg.append(s('rect', { x: x0, y: yy, width: Math.max(1, x1 - x0), height: Math.max(1, bot - yy), fill: st.colors[i] }));
        });
        // label the highest and lowest bars (the bars start below the lowest, so the base itself is not a value)
        for (const [v, yy] of sy(smin) - sy(smax) >= 14 ? [[smax, sy(smax) + 4], [smin, sy(smin) + 4]] : [[smax, sy(smax) + 4]]) {
          const tx = s('text', { x: padL - 8, y: Math.max(top + 9, yy), 'text-anchor': 'end' }); tx.textContent = (st.fmt || String)(v); ax.append(tx);
        }
      }
    }
    for (const mk of cfg.marks || []) {
      if (isNum(mk.v)) svg.append(s('circle', { cx: x(mk.i), cy: y(clamp(mk.v)), r: mk.r || 4, fill: mk.color, stroke: 'var(--panel)', 'stroke-width': 2 }));
    }
    const hair = s('line', { class: 'hair', y1: padT, y2: H - padB, visibility: 'hidden' });
    const dots = cfg.series.map((sr) => { const c = s('circle', { r: 4, fill: sr.color, stroke: 'var(--panel)', 'stroke-width': 2, visibility: 'hidden' }); return c; });
    const tag = s('g', { class: 'synctag', visibility: 'hidden' }), tagBg = s('rect', { rx: 5, height: 22 }), tagTx = s('text', {});
    tag.append(tagBg, tagTx);
    svg.append(hair, ...dots, tag);
    const hit = s('rect', { x: padL, y: 0, width: W - padL - padR, height: H, fill: 'transparent' });
    svg.append(hit);
    const point = (i) => {
      hair.setAttribute('x1', x(i)); hair.setAttribute('x2', x(i)); hair.setAttribute('visibility', 'visible');
      cfg.series.forEach((sr, k) => { const v = sr.values[i]; if (isNum(v)) { dots[k].setAttribute('cx', x(i)); dots[k].setAttribute('cy', y(clamp(v))); dots[k].setAttribute('visibility', 'visible'); } else dots[k].setAttribute('visibility', 'hidden'); });
    };
    const unpoint = () => { hair.setAttribute('visibility', 'hidden'); dots.forEach((d) => d.setAttribute('visibility', 'hidden')); tag.setAttribute('visibility', 'hidden'); };
    const syn = cfg.sync;
    const others = () => (syn ? Object.entries(SYNC[syn.group] || {}).filter(([id, o]) => id !== syn.id && o.svg.isConnected).map(([, o]) => o) : []);
    if (syn) (SYNC[syn.group] = SYNC[syn.group] || {})[syn.id] = { svg, keys: syn.keys, tol: syn.tol || 0.5, hide: unpoint, show: (i) => {
      point(i);
      const lab = syn.label ? syn.label(i) : null;
      if (!lab) return;
      tagTx.textContent = '';
      const a = s('tspan', { class: 'k' }); a.textContent = lab[0] + '  ';
      const b = s('tspan', {}); b.textContent = lab[1];
      tagTx.append(a, b);
      let tw = 0;
      try { tw = tagTx.getComputedTextLength(); } catch (e) { /* not rendered */ }
      const w = (tw || (lab[0].length + 2 + lab[1].length) * 7.2) + 18, hx = x(i);
      const tx = hx + 8 + w > W - padR ? hx - 8 - w : hx + 8;
      tagBg.setAttribute('x', tx); tagBg.setAttribute('y', padT); tagBg.setAttribute('width', w);
      tagTx.setAttribute('x', tx + 9); tagTx.setAttribute('y', padT + 15);
      tag.setAttribute('visibility', 'visible');
    } };
    const move = (ev) => {
      const r = svg.getBoundingClientRect(); const px = (ev.clientX - r.left) * (W / r.width);
      let i;
      if (xv) {
        const v = d0 + ((px - padL) / plotW) * (d1 - d0); let a = 0, b = n - 1;
        while (b - a > 1) { const mid = (a + b) >> 1; if (xv[mid] < v) a = mid; else b = mid; }
        i = Math.abs(xv[a] - v) <= Math.abs(xv[b] - v) ? a : b;
      } else i = Math.max(0, Math.min(n - 1, Math.round(((px - padL) / plotW) * (n - 1))));
      point(i);
      showTip(ev.clientX, ev.clientY, cfg.titleFmt ? cfg.titleFmt(cfg.dates[i], i) : shortDate(cfg.dates[i]),
        cfg.series.map((sr, k) => ({ label: sr.name, color: sr.color, value: cfg.tipVal ? cfg.tipVal(k, i) : (cfg.fmt || money)(sr.values[i]) })).concat(cfg.extraTip ? cfg.extraTip(i) : []));
      if (syn) for (const o of others()) {
        const k = syn.keys[i], j = nearestKey(o.keys, k);
        if (j >= 0 && Math.abs(o.keys[j] - k) <= Math.max(o.tol, syn.tol || 0.5)) o.show(j); else o.hide();
      }
    };
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerdown', move);
    hit.addEventListener('pointerleave', () => { hideTip(); unpoint(); others().forEach((o) => o.hide()); });
    fill(el, svg);
    if (cfg.markers) for (const mk of cfg.markers) {
      const i = cfg.dates.findIndex((d) => d >= mk.date); if (i < 0) continue;
      const c = s('circle', { cx: x(i), cy: y(mk.value), r: 5, fill: mk.color, stroke: 'var(--panel)', 'stroke-width': 2 });
      c.addEventListener('pointermove', (ev) => { ev.stopPropagation(); showTip(ev.clientX, ev.clientY, shortDate(mk.date), [{ label: mk.label, value: mk.text }]); });
      c.addEventListener('pointerleave', hideTip);
      svg.insertBefore(c, hit.nextSibling);
    }
  }

  function barChart(el, cfg) {
    // cfg: {labels, values, color | colorFn, fmt, height, tipTitle} or {months: ['YYYY-MM', ...]} for calendar labels
    const W = Math.max(280, el.clientWidth || 600), H = cfg.height || 200, padR = 8, padT = 10, padB = 24;
    if (cfg.months && cfg.months.length) {
      const src = cfg.months, at = new Map(src.map((ym, i) => [ym, i])), months = [];
      for (let y0 = +src[0].slice(0, 4), m0 = +src[0].slice(5, 7); ; m0++) {
        if (m0 > 12) { m0 = 1; y0++; }
        const ym = `${y0}-${String(m0).padStart(2, '0')}`; months.push(ym);
        if (ym >= src[src.length - 1]) break;
      }
      const orig = months.map((ym) => (at.has(ym) ? at.get(ym) : -1)), vals0 = cfg.values, color0 = cfg.colorFn, extra0 = cfg.extraTip;
      cfg = { ...cfg, months, values: orig.map((k) => (k >= 0 ? vals0[k] : 0)),
        colorFn: color0 ? (v, i) => color0(v, orig[i]) : null, extraTip: extra0 ? (i) => (orig[i] >= 0 ? extra0(orig[i]) : []) : null };
      cfg.labels = months.map((ym) => (ym.slice(5) === '01' ? ym.slice(0, 4) : MON[+ym.slice(5) - 1]));
      cfg.tipTitle = cfg.tipTitle ? ((tt) => (i) => (orig[i] >= 0 ? tt(orig[i]) : monthYear(months[i])))(cfg.tipTitle) : (i) => monthYear(months[i]);
    }
    const n = (cfg.labels || []).length;
    if (!n) { fill(el, h('div', { class: 'faint', text: 'Nothing yet.' })); return; }
    const vals = cfg.values.map((v) => (isNum(v) ? v : 0));
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const ticks = niceTicks(lo, hi); lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
    const ystep = ticks.length > 1 ? ticks[1] - ticks[0] : 1, ybig = Math.max(...ticks.map(Math.abs));
    const ylab = ticks.map((t) => { const l = (cfg.yfmt || axisMoney)(t, ystep, ybig); return l.includes(HID) ? '' : l; });
    const padL = Math.max(36, Math.ceil(Math.max(...ylab.map((l) => l.length)) * CHAR_W) + 14);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo || 1)) * (H - padT - padB);
    const bw = (W - padL - padR) / n, gap = Math.min(6, Math.max(2, bw * 0.25));
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': cfg.label || 'bar chart' });
    const g = s('g', { class: 'grid' }), ax = s('g', { class: 'axis' });
    for (const t of ticks) {
      g.append(s('line', { x1: padL, x2: W - padR, y1: y(t), y2: y(t) }));
      const tx = s('text', { x: padL - 8, y: y(t) + 4, 'text-anchor': 'end' }); tx.textContent = ylab[ticks.indexOf(t)]; ax.append(tx);
    }
    // labels: months on a calendar step (every 1, 2, 3, 6 or 12 months, January as its year); otherwise every k-th bar
    const need = Math.max(...cfg.labels.map((l) => String(l).length)) * CHAR_W + 12;
    let show;
    if (cfg.months) {
      const e = [1, 2, 3, 6, 12].find((k) => bw * k >= need) || 12;
      show = (i) => (+cfg.months[i].slice(5) - 1) % e === 0;
    } else { const lstep = Math.max(1, Math.ceil(need / bw)); show = (i) => i % lstep === 0; }
    cfg.labels.forEach((l, i) => {
      if (!show(i)) return;
      const cx = padL + bw * i + bw / 2;
      ax.append(s('line', { class: 'tick', x1: cx, x2: cx, y1: H - padB, y2: H - padB + 4 }));
      const tx = s('text', { x: cx, y: H - 5, 'text-anchor': 'middle' }); tx.textContent = l; ax.append(tx);
    });
    svg.append(g, ax);
    vals.forEach((v, i) => {
      const x0 = padL + bw * i + gap / 2, w = Math.max(1, bw - gap), y0 = y(Math.max(v, 0)), y1 = y(Math.min(v, 0));
      const hgt = Math.max(v === 0 ? 0 : 1, y1 - y0), r = Math.min(4, w / 2, hgt);
      const color = cfg.colorFn ? cfg.colorFn(v, i) : cfg.color;
      // rounded on the data end only, square on the baseline
      const p = v >= 0
        ? `M${x0} ${y1}V${y0 + r}Q${x0} ${y0} ${x0 + r} ${y0}H${x0 + w - r}Q${x0 + w} ${y0} ${x0 + w} ${y0 + r}V${y1}Z`
        : `M${x0} ${y0}V${y1 - r}Q${x0} ${y1} ${x0 + r} ${y1}H${x0 + w - r}Q${x0 + w} ${y1} ${x0 + w} ${y1 - r}V${y0}Z`;
      const bar = s('path', { d: p, fill: color });
      const hitR = s('rect', { x: padL + bw * i, y: padT, width: bw, height: H - padT - padB, fill: 'transparent' });
      const on = (ev) => { bar.setAttribute('opacity', '0.8'); showTip(ev.clientX, ev.clientY, cfg.tipTitle ? cfg.tipTitle(i) : cfg.labels[i], [{ label: cfg.name || 'Value', value: (cfg.fmt || money)(cfg.values[i]), cls: cfg.toneTip ? tone(cfg.values[i]) : '' }].concat(cfg.extraTip ? cfg.extraTip(i) : [])); };
      hitR.addEventListener('pointermove', on); hitR.addEventListener('pointerdown', on);
      hitR.addEventListener('pointerleave', () => { bar.removeAttribute('opacity'); hideTip(); });
      svg.append(bar, hitR);
    });
    svg.append(s('line', { class: 'base', x1: padL, x2: W - padR, y1: y(0), y2: y(0) }));
    fill(el, svg);
  }

  // color: one color, or a function of the row (sector lists color each bar by its sector)
  function hbars(list, fmt = pct, color) {
    const max = Math.max(...list.map((x) => x.weight), 0.0001);
    return h('div', {}, list.map((x) => {
      const fill = h('div', { class: 'fill' }); fill.style.width = (x.weight / max) * 100 + '%';
      const c = typeof color === 'function' ? color(x) : color; if (c) fill.style.background = c;
      return h('div', { class: 'hbar' }, h('span', { class: 'n', text: x.name, title: x.name }), h('div', { class: 'track' }, fill), h('span', { class: 'v', text: fmt(x.weight) }));
    }));
  }

  function table(cols, rows, opts = {}) {
    const thead = h('tr', {}, cols.map((c) => {
      const th = h('th', { class: [c.r ? 'r' : '', c.m ? 'hide-m' : '', opts.sortKey && c.key ? 'sort' : '', opts.sortKey && opts.sortKey === c.key ? 'on' : ''].join(' '), text: c.label + (opts.sortKey && opts.sortKey === c.key ? (opts.sortDir < 0 ? ' \u2193' : ' \u2191') : '') });
      if (opts.onSort && c.key) th.addEventListener('click', () => opts.onSort(c.key));
      return th;
    }));
    const tbody = h('tbody', {}, rows.map((r) => {
      const tr = h('tr', { class: opts.onRow ? 'click' : '' }, cols.map((c) => {
        const v = c.get(r);
        const td = h('td', { class: [c.r ? 'r' : '', c.m ? 'hide-m' : '', c.cls ? c.cls(r) : ''].join(' ') });
        if (v != null && v.nodeType) td.append(v); else td.textContent = v == null ? '' : v;
        return td;
      }));
      if (opts.onRow) tr.addEventListener('click', () => opts.onRow(r));
      return tr;
    }));
    return h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, thead), tbody));
  }

  function rangeSlice(dates, key) {
    if (!dates.length) return 0;
    const rk = key || ST.range;
    const last = dates[dates.length - 1], d = new Date(last + 'T12:00:00');
    const back = { '1M': 31, '3M': 92, '6M': 183, '1Y': 366, '3Y': 1096, '5Y': 1827 }[rk];
    let start = '';
    if (rk === 'YTD') start = last.slice(0, 4) + '-01-01';
    else if (back) { d.setDate(d.getDate() - back); start = d.toISOString().slice(0, 10); }
    else return 0;
    const i = dates.findIndex((x) => x >= start);
    return Math.max(0, i - (i > 0 ? 1 : 0));
  }

  function card(title, ...kids) { return h('div', { class: 'card' }, title ? h('h2', { text: title }) : null, ...kids); }
  function tile(label, value, cls, small) { return h('div', { class: 'card tile' }, h('div', { class: 'label', text: label }), h('div', { class: 'val ' + (cls || ''), text: value }), small ? h('div', { class: 'small', text: small }) : null); }

  // ---- pages --------------------------------------------------------------------------------------------------
  function alerts(m) {
    const out = [];
    for (const mg of m.margin || []) {
      if (mg.debt > 0 && (mg.excess < 0 || (mg.drop_to_call != null && mg.drop_to_call < 0.15))) {
        out.push(h('div', { class: 'alert ' + (mg.excess < 0 ? 'bad' : '') }, icon(WARN_ICON),
          h('div', {}, h('b', { text: mg.excess < 0 ? `Margin shortfall about ${money(-mg.excess, 0)}` : `Margin call after a ${pct(mg.drop_to_call, 0)} drop` }),
            ` on ${mg.account} (estimate, buffer ${money(mg.excess, 0)}). `, h('a', { href: '/risk', text: 'Margin details' }))));
      }
    }
    const gaps = (m.gaps || []).reduce((n, g) => n + (g.shares || []).length, 0);
    if (gaps) out.push(h('div', { class: 'alert' }, icon(WARN_ICON), h('div', {}, `${gaps} holding${gaps > 1 ? 's were' : ' was'} sold without a matching buy in the history. `, h('a', { href: '/settings', text: 'See Data and settings' }))));
    return out;
  }

  const CHART_RANGES = ['Live', '1D', '1W', '1M', '3M', 'YTD', '1Y', '3Y', '5Y', 'All'];
  const chartMode = () => (CENSOR ? 'return' : ST.mode);          // censored, the charts run in percent
  function perfChart(el, m, height) {
    const p = m.performance, hs = m.history;
    if (!p || !p.ready) { el.append(h('div', { class: 'faint', text: 'Upload an export to see your history.' })); return; }
    const controls = h('div', { class: 'head' },
      h('div', { class: 'chips' }, ['value', 'return'].map((k) => h('button', { class: 'chip' + (chartMode() === k ? ' on' : ''), disabled: CENSOR && k === 'value', title: CENSOR && k === 'value' ? 'Hidden while amounts are hidden' : null, text: k === 'value' ? 'Dollars' : 'Percent', onclick: () => { ST.mode = k; render(); } }))),
      h('div', { class: 'chips' }, CHART_RANGES.map((k) => h('button', { class: 'chip' + (ST.range === k ? ' on' : '') + (k === 'Live' ? ' live' : ''), text: k, onclick: () => { ST.range = k; render(); } }))));
    if (['Live', '1D', '1W'].includes(ST.range)) { intradayChart(el, m, controls, height); return; }
    const i0 = rangeSlice(hs.dates);
    const dates = hs.dates.slice(i0);
    let series, fmt, yfmt;
    if (chartMode() === 'value') {
      series = [
        { name: 'You', color: 'var(--s-you)', values: hs.value.slice(i0) },
        { name: 'VOO, same deposits', color: 'var(--s-voo)', values: (p.bench.VOO || {}).value.slice(i0), dot: true },
        { name: 'QQQM, same deposits', color: 'var(--s-qqqm)', values: (p.bench.QQQM || {}).value.slice(i0), dot: true },
        ...(p.bench.M2 ? [{ name: 'M2, same deposits', color: 'var(--s-m2)', values: p.bench.M2.value.slice(i0), width: 1.75 }] : []),
        { name: 'Money put in', color: 'var(--s-ref)', values: hs.net_invested.slice(i0), dash: true }];
      fmt = (v) => money(v, 0);
    } else {
      const base = (arr) => { const b = arr.find(isNum); return arr.map((v) => (isNum(v) && b ? v / b - 1 : null)); };
      const idx = p.index.slice(i0);
      series = [
        { name: 'You', color: 'var(--s-you)', values: base(idx) },
        { name: 'VOO', color: 'var(--s-voo)', values: base((p.bench.VOO || {}).px.slice(i0)), dot: true },
        { name: 'QQQM', color: 'var(--s-qqqm)', values: base((p.bench.QQQM || {}).px.slice(i0)), dot: true },
        ...(p.bench.M2 ? [{ name: 'M2 money supply', color: 'var(--s-m2)', values: base(p.bench.M2.px.slice(i0)), width: 1.75 }] : [])];
      fmt = (v) => spct(v, 1); yfmt = axisPct;
    }
    const legend = h('div', { class: 'legend' }, series.map((sr) => { const i = h('i', { class: sr.dash ? 'dash' : sr.dot ? 'dot' : '' }); i.style.background = sr.color; return h('span', {}, i, sr.name); }));
    const box = h('div', { class: 'chart' });
    el.append(controls, legend, box);
    requestAnimationFrame(() => lineChart(box, { dates, series, fmt, yfmt, height: height || 280, label: 'Portfolio value over time',
      sync: { group: 'home', id: 'networth', keys: dayKeys(dates), label: (i) => (chartMode() === 'value' ? ['Net worth', money(series[0].values[i], 0)] : ['You', fmt(series[0].values[i])]) } }));
  }

  const VERDICT_TONE = { 'Strongly overvalued': 'bad', Overvalued: 'bad', 'Fair value': '', Undervalued: 'good', 'Strongly undervalued': 'good' };
  // ---- dashboard pieces ---------------------------------------------------------------------------------------
  const tier = () => (window.innerWidth >= 3000 ? 4 : window.innerWidth >= 2200 ? 3 : window.innerWidth >= 1500 ? 2 : 1);
  function panel(title, link, ...kids) {
    return h('div', { class: 'card panel' },
      h('div', { class: 'panel-head' }, h('h2', { text: title }), link ? h('a', { class: 'more', href: link[0], text: link[1] }) : null), ...kids);
  }
  function sparkline(values, color) {
    const vals = values.filter(isNum);
    const svg = s('svg', { viewBox: '0 0 120 32', class: 'spark', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    if (vals.length < 2) return svg;
    const lo = Math.min(...vals), hi = Math.max(...vals), n = values.length;
    let d = '', pen = false;
    values.forEach((v, i) => { if (!isNum(v)) { pen = false; return; } d += (pen ? 'L' : 'M') + ((i / (n - 1)) * 120).toFixed(1) + ' ' + (30 - ((v - lo) / (hi - lo || 1)) * 28).toFixed(1); pen = true; });
    svg.append(s('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round' }));
    return svg;
  }
  // diverging color: red below zero, green above, a neutral gray at zero
  function heatColor(p, span) {
    const t = Math.max(-1, Math.min(1, (p || 0) / span));
    const mix = (a, b, k) => a.map((x, i) => Math.round(x + (b[i] - x) * k));
    const mid = [42, 42, 72], up = [24, 150, 98], dn = [190, 58, 78];
    const c = t >= 0 ? mix(mid, up, Math.sqrt(t)) : mix(mid, dn, Math.sqrt(-t));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  function squarify(items, x, y, w, hgt) {
    // items sorted by value desc; returns rects {item,x,y,w,h}
    const out = [], total = items.reduce((a, b) => a + b.v, 0);
    if (!total) return out;
    let rest = items.map((it) => ({ ...it, a: (it.v / total) * w * hgt }));
    const worst = (row, len) => { const sum = row.reduce((a, b) => a + b.a, 0), mx = Math.max(...row.map((r) => r.a)), mn = Math.min(...row.map((r) => r.a)); return Math.max((len * len * mx) / (sum * sum), (sum * sum) / (len * len * mn)); };
    while (rest.length) {
      const len = Math.min(w, hgt); let row = [rest[0]], i = 1;
      while (i < rest.length && worst([...row, rest[i]], len) <= worst(row, len)) { row.push(rest[i]); i++; }
      const sum = row.reduce((a, b) => a + b.a, 0);
      if (w >= hgt) { const cw = sum / hgt; let cy = y; for (const r of row) { const rh = r.a / cw; out.push({ item: r, x, y: cy, w: cw, h: rh }); cy += rh; } x += cw; w -= cw; }
      else { const ch = sum / w; let cx = x; for (const r of row) { const rw = r.a / ch; out.push({ item: r, x: cx, y, w: rw, h: ch }); cx += rw; } y += ch; hgt -= ch; }
      rest = rest.slice(i);
    }
    return out;
  }
  function heatmap(m) {
    const mode = ST.heatMode || 'day';
    const box = h('div', { class: 'heat' });
    const chips = h('div', { class: 'chips' }, [['day', 'Today'], ['gain', 'Total gain'], ['high', 'From high']].map(([k, l]) => h('button', { class: 'chip' + (mode === k ? ' on' : ''), text: l, onclick: () => { ST.heatMode = k; render(); } })));
    // From high: green at the all-time high, gray 20% under it, red 40% or more under
    const span = mode === 'day' ? 4 : mode === 'high' ? 20 : 60;
    const legend = h('div', { class: 'heat-legend' }, h('span', { text: mode === 'high' ? '-40%' : `-${span}%` }), h('i'), h('span', { text: mode === 'high' ? 'at high' : `+${span}%` }));
    const card2 = panel('Holdings map', ['/positions', 'All positions'], h('div', { class: 'head', style: 'margin:-4px 0 10px' }, chips, legend), box,
      h('p', { class: 'faint', style: 'font-size:12px;margin:8px 0 0', text: mode === 'high' ? 'Box size is the position’s value; color is how far the price is under its all-time high. Hover or tap a box for the high and its date.' : 'Box size is the position’s value; color is the move. Hover a box for more, including its distance from the all-time high. Other holds the smallest positions.' }));
    requestAnimationFrame(() => {
      const W = box.clientWidth || 600, H = W < 600 ? Math.round(W * 1.15) : Math.max(260, Math.min(520, Math.round(W * 0.56)));
      box.style.height = H + 'px';
      // small positions fold into one "Other" box so the map stays on a clean grid
      const all = m.positions.filter((p) => p.value > 1);
      const total = all.reduce((a, p) => a + p.value, 0) || 1;
      const minShare = Math.max(0.003, (W < 600 ? 1200 : 1700) / (W * H));   // smallest box about 41 x 41 px (35 on a phone)
      let keep = all.filter((p, i) => i < 10 || p.value / total >= minShare);
      let rest = all.filter((p) => !keep.includes(p));
      if (rest.length === 1) { keep = all; rest = []; }
      const items = keep.map((p) => ({ v: p.value, p }));
      items.sort((a, b) => b.v - a.v);
      let rects;
      if (!rest.length) rects = squarify(items, 0, 0, W, H);
      else {
        // "Other" is a small fixed box in the bottom-right corner, not sized by value. The smallest named holdings
        // fill the rest of that bottom strip, and the strip's height is solved so every named box keeps its share.
        const v = rest.reduce((a, p) => a + p.value, 0);
        const day = rest.reduce((a, p) => a + p.value * (p.change_pct || 0), 0) / v;
        const gk = rest.filter((p) => isNum(p.gain_pct)), gv = gk.reduce((a, p) => a + p.value, 0);
        const ak = rest.filter((p) => isNum(p.from_ath)), av = ak.reduce((a, p) => a + p.value, 0);
        const other = { symbol: 'Other', name: `${rest.length} smaller holdings`, value: v, change_pct: day,
                        gain_pct: gv ? gk.reduce((a, p) => a + p.value * p.gain_pct, 0) / gv : null, gain: null, weight: v / total, other: rest,
                        from_ath: av ? ak.reduce((a, p) => a + p.value * p.from_ath, 0) / av : null };
        const wo = Math.max(70, Math.min(120, W * 0.11)), target = Math.max(40, Math.min(60, H * 0.1));
        const kept = items.reduce((a, it) => a + it.v, 0);
        let k = 1, hb = 0;
        for (; k < items.length; k++) {
          const share = items.slice(-k).reduce((a, it) => a + it.v, 0) / kept;
          hb = (share * W * H) / (W - wo + share * wo);
          if (hb >= target) break;
        }
        const strip = items.slice(-k), top = items.slice(0, items.length - k);
        rects = [...squarify(top, 0, 0, W, H - hb), ...squarify(strip, 0, H - hb, W - wo, hb),
                 { item: { v, p: other }, x: W - wo, y: H - hb, w: wo, h: hb }];
      }
      fill(box, ...rects.map((r) => {
        const p = r.item.p;
        const val = mode === 'day' ? (p.change_pct || 0) : mode === 'high' ? (isNum(p.from_ath) ? p.from_ath * 100 + 20 : null) : (isNum(p.gain_pct) ? p.gain_pct * 100 : null);
        const label = val == null ? (mode === 'gain' ? 'basis ?' : '') : mode === 'high' ? (p.from_ath > -0.005 ? 'at high' : (p.from_ath * 100).toFixed(0) + '%') : `${val > 0 ? '+' : ''}${val.toFixed(mode === 'day' ? 2 : 0)}%`;
        const el = h('button', { class: 'tile-h', title: `${p.symbol} ${money(p.value, 0)}` });
        el.style.cssText = `left:${r.x}px;top:${r.y}px;width:${Math.max(0, r.w - 2)}px;height:${Math.max(0, r.h - 2)}px;background:${val == null ? '#2A2A48' : heatColor(val, span)}`;
        if (r.w > 29 && r.h > 21) {
          el.append(h('b', { text: p.symbol }));
          if (r.h > 40 && r.w > 46) el.append(h('span', { text: label }));
          if (r.h > 70 && r.w > 80) el.append(h('em', { text: money(p.value, 0) }));
          const fs = Math.max(r.w < 40 ? 9.5 : 11, Math.min(22, Math.sqrt(r.w * r.h) / 6));
          el.style.fontSize = fs + 'px';
        }
        if (p.other) {
          el.classList.add('other');
          el.style.background = '';
          el.style.fontSize = '12px';
          el.replaceChildren(h('b', { text: 'Other' }), h('span', { text: `${p.other.length} more` }), r.h > 58 ? h('em', { text: money(p.value, 0) }) : '');
          el.addEventListener('click', () => go('/positions'));
          el.addEventListener('pointermove', (ev) => showTip(ev.clientX, ev.clientY, `${p.other.length} smaller holdings`, mode === 'high'
            ? [{ label: 'Value', value: money(p.value, 0) }].concat([...p.other].filter((x) => isNum(x.from_ath)).sort((a, b) => b.from_ath - a.from_ath).slice(0, 6).map((x) => ({ label: x.symbol, value: x.from_ath > -0.005 ? 'at the high' : spct(x.from_ath, 1) })))
            : [{ label: 'Value', value: money(p.value, 0) }, { label: 'Today', value: spct((p.change_pct || 0) / 100, 2), cls: tone(p.change_pct) }]
              .concat([...p.other].sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0)).slice(0, 6).map((x) => ({ label: x.symbol, value: spct((x.change_pct || 0) / 100, 2), cls: tone(x.change_pct) })))));
          el.addEventListener('pointerleave', hideTip);
          return el;
        }
        el.addEventListener('click', () => openSymbol(p.symbol));
        el.addEventListener('pointermove', (ev) => showTip(ev.clientX, ev.clientY, `${p.symbol}  ${p.name || ''}`, [
          { label: 'Value', value: money(p.value, 0) }, { label: 'Today', value: spct((p.change_pct || 0) / 100, 2), cls: tone(p.change_pct) },
          { label: 'Total gain', value: isNum(p.gain) ? `${smoney(p.gain, 0)} (${spct(p.gain_pct)})` : 'basis unknown', cls: tone(p.gain) },
          { label: 'Weight', value: pct(p.weight) }, ...athRows(p)]));
        el.addEventListener('pointerleave', hideTip);
        return el;
      }));
    });
    return card2;
  }
  // distance from the all-time high: hover rows (map, Positions) and short text
  const athRows = (p) => (isNum(p.from_ath) ? [{ label: 'From all-time high', value: p.from_ath > -0.005 ? 'at the high' : spct(p.from_ath, 1) },
    { label: 'All-time high', value: `${price(p.ath)}, ${shortDate(p.ath_date)}` }] : []);
  function segbar(parts, colors) {
    const tot = parts.reduce((a, b) => a + Math.max(b.v, 0), 0) || 1;
    const bar = h('div', { class: 'segbar' }), leg = h('div', { class: 'seglegend' });
    parts.forEach((p, i) => {
      const seg = h('i'); seg.style.flex = String(Math.max(p.v, 0) / tot); seg.style.background = colors[i % colors.length];
      seg.addEventListener('pointermove', (ev) => showTip(ev.clientX, ev.clientY, p.name, [{ label: 'Value', value: money(p.v, 0) }, { label: 'Share', value: pct(p.v / tot) }]));
      seg.addEventListener('pointerleave', hideTip);
      bar.append(seg);
      const k = h('i'); k.style.background = colors[i % colors.length];
      leg.append(h('span', {}, k, `${p.name} `, h('b', { text: pct(p.v / tot, 0) })));
    });
    return h('div', {}, bar, leg);
  }
  // "By type" keeps a fixed order and color per type (checked for colorblind separation in this order)
  const TYPE_COLORS = { 'Stock': '#3987e5', 'Bitcoin & Equivalents': '#d95926', 'Crypto & Equivalents': '#199e70', 'ETF': '#9085e9', 'Option income ETF': '#c98500', 'Leveraged ETF': '#d55181', 'Preferred stock': '#008300' };
  const TYPE_ORDER = Object.keys(TYPE_COLORS);
  // sector bars: each sector keeps its color; Bitcoin and crypto match the By type bar. Every bar is labeled, so the
  // color is a second cue, never the only one
  const SECTOR_COLORS = { 'Technology': '#3987e5', 'Bitcoin & Equivalents': '#d95926', 'Crypto & Equivalents': '#199e70', 'Communication Services': '#9085e9',
    'Financial Services': '#c98500', 'Consumer Cyclical': '#d55181', 'Industrials': '#5f8fb8', 'Utilities': '#3f9a3f', 'Energy': '#e66767', 'Healthcare': '#18a5b5',
    'Consumer Defensive': '#8f9a3c', 'Basic Materials': '#b0784e', 'Real Estate': '#b06fc9' };
  const sectorColor = (x) => SECTOR_COLORS[x.name] || '#77739C';
  const ACCT_COLORS = ['#3987e5', '#d55181', '#C98500', '#199e70', '#9085e9', '#d95926', '#77739C', '#A7A3C6'];
  // returns over each period, and the same against M2: (1 + you) / (1 + M2 growth) - 1
  function periodReturns(m) {
    const p = m.performance, hs = m.history;
    if (!p || !p.ready) return [];
    const idx = p.index, dates = hs.dates, n = dates.length, last = new Date(dates[n - 1] + 'T12:00:00');
    const lv = p.bench && p.bench.M2 ? p.bench.M2.px : null;
    const at = (iso) => { let i = dates.findIndex((d) => d >= iso); if (i < 0) i = n - 1; return Math.max(0, i - (i > 0 ? 1 : 0)); };
    const back = (days) => { const d = new Date(last); d.setDate(d.getDate() - days); return d.toISOString().slice(0, 10); };
    const r = (i0) => idx[n - 1] / idx[i0] - 1;
    const vs = (i0, ret) => (lv && lv[i0] && isNum(ret) ? (1 + ret) / (lv[n - 1] / lv[i0]) - 1 : null);
    const row = (k, i0) => { const ret = r(i0); return [k, ret, vs(i0, ret)]; };
    const ext = (m.extended || {}).move || 0, pc = m.net_worth - m.day_change - ext;
    const today = pc ? (m.day_change + ext) / pc : null;                // since the last close, extended hours included
    const first = dates.findIndex((d) => d >= p.start);
    return [['1D', today, null], row('1W', at(back(7))), row('1M', at(back(31))), row('YTD', at(dates[n - 1].slice(0, 4) + '-01-01')), row('1Y', at(back(366))),
      ['All', p.twr, vs(Math.max(0, first), p.twr)]];
  }
  const m2Word = (v) => (!isNum(v) ? '' : v >= 0 ? `${pct(v, 1)} ahead of M2` : `${pct(-v, 1)} behind M2`);
  function heroCard(m) {
    const p = m.performance || {}, inc = (m.income || {}).next12 || {}, mg = (m.margin || [])[0], lev = m.leverage, m2 = m.m2;
    const extMove = (m.extended || {}).move || 0;
    const prevClose = m.net_worth - m.day_change - extMove;               // net worth is live; today is the session
    const dayPct = prevClose ? m.day_change / prevClose : null;
    const stat = (label, value, cls, sub, title) => h('div', { class: 'hstat', title: title || null }, h('div', { class: 'label', text: label }), h('div', { class: 'v ' + (cls || ''), text: value }), sub ? h('div', { class: 'sub2' }, sub) : null);
    const per = periodReturns(m);
    const m2b = p.bench && p.bench.M2, behind = m2b ? p.final - m2b.final : null;
    const gainPct = p.net_in ? p.gain / p.net_in : null;
    const loanSub = mg && mg.rate ? [`${pct(mg.rate, 2)} a year`, CENSOR ? '' : `, ${money(-mg.interest_year / 12, 0)} a month`,
      isNum(mg.vs_m2) ? h('span', { class: mg.vs_m2 <= 0 ? 'up' : 'down', text: `. ${Math.abs(mg.vs_m2 * 100).toFixed(2)} pts ${mg.vs_m2 <= 0 ? 'under' : 'over'} M2` }) : null] : '';
    return h('div', { class: 'card hero2' },
      h('div', { class: 'hmain' }, h('div', { class: 'hnum' }, h('div', { class: 'label', text: 'Net worth' }), h('div', { class: 'big', text: money(m.net_worth, 0) }),
        h('div', { class: 'hdelta' }, h('span', { class: 'pill ' + tone(m.day_change), text: `${spct(dayPct, 2)} today` }),
          CENSOR ? null : h('span', { class: 'delta ' + tone(m.day_change), text: smoney(m.day_change, 0) })),
        m.extended && Math.abs(m.extended.move) >= 1 ? h('div', { class: 'delta ' + tone(m.extended.move), style: 'font-size:13px;margin-top:6px', text: `${chgText(m.extended.move, m.extended.move / (m.net_worth - m.extended.move))} ${m.extended.session === 'pre' ? 'pre-market' : 'after hours'}` }) : null),
        h('div', { class: 'ptable' },
          h('span', {}), ...per.map(([k]) => h('em', { text: k })),
          h('span', { class: 'pl', text: 'Return' }), ...per.map(([, v]) => h('b', { class: tone(v), text: spct(v, 1) })),
          m2b ? h('span', { class: 'pl', text: 'vs M2', title: 'Your return against the growth of the M2 money supply over the same days' }) : null,
          ...(m2b ? per.map(([k, , v]) => h('b', { class: tone(v), text: k === '1D' || k === '1W' || !isNum(v) ? '' : spct(v, 1) })) : []))),
      h('div', { class: 'hstats' },
        stat('Gain since ' + (p.start ? shortDate(p.start) : '-'), CENSOR ? spct(gainPct, 1) : smoney(p.gain, 0), tone(p.gain),
          p.ready ? [CENSOR ? 'on the money you put in' : `${money(p.net_in, 0)} put in`, isNum(behind) ? h('span', { class: tone(behind), text: CENSOR ? `. ${m2Word(p.final / m2b.final - 1)}` : `. ${money(Math.abs(behind), 0)} ${behind >= 0 ? 'ahead of' : 'behind'} M2` }) : null] : '',
          m2b ? 'M2: the same deposits growing with the money supply' : null),
        stat('Assets', money(isNum(m.assets) ? m.assets : m.invested, 0), '', `${m.positions.length} holdings, ${openAccounts(m).length} accounts, before the loan`),
        stat('Margin loan', mg ? money(-mg.debt, 0) : '$0', mg && mg.debt ? 'down' : '', loanSub,
          m2 && mg && mg.rate ? `M2 grew ${pct(m2.yoy, 2)} in the last 12 months. A loan that costs less than that is cheaper than new money.` : null),
        lev ? stat('Leverage', lev.margin.toFixed(2) + 'x', lev.margin >= 3 ? 'down' : lev.margin >= 2 ? 'warn' : '',
          [CENSOR ? 'holdings over your own money, margin account' : `${money(lev.held, 0)} held on ${money(lev.equity, 0)} of your own`, isNum(lev.taxable) ? `. ${lev.taxable.toFixed(2)}x across all non-IRA accounts` : ''],
          'Holdings divided by your own money in the margin account. Retirement accounts are left out: they are not collateral.') : null,
        stat('Income, next 12 months', smoney(inc.net, 0), tone(inc.net), CENSOR ? 'dividends and options, less interest' : `dividends ${money(inc.dividends, 0)}, options ${smoney(inc.options_pace, 0)}, interest ${money(inc.interest, 0)}`)));
  }
  function marginCard(m) {
    const mg = (m.margin || [])[0];
    if (!mg) return null;
    const buffer = mg.excess, reqv = mg.requirement;
    const total = Math.max(reqv + Math.max(buffer, 0), 1);
    const bar = h('div', { class: 'gauge' }), used = h('i', { class: 'req' }), free = h('i', { class: 'buf ' + (buffer < 0 ? 'neg' : buffer / total < 0.1 ? 'thin' : '') });
    used.style.flex = String(reqv / total); free.style.flex = String(Math.max(buffer, 0) / total || 0.002);
    bar.append(used, free);
    const kv = (a, v, cls) => h('div', { class: 'kv' }, h('span', { text: a }), h('span', { class: cls || '', text: v }));
    return panel('Margin buffer', ['/risk', 'Details'],
      h('div', { class: 'mid ' + (buffer < 0 ? 'down' : buffer / total < 0.1 ? 'warn' : 'up'), text: `${money(buffer, 0)}` },
        h('span', { class: 'dim', style: 'font-size:14px;margin-left:8px', text: pct(buffer / total, 1) })),
      h('div', { class: 'faint', style: 'font-size:12px;margin:-2px 0 8px', text: 'Estimate at live prices' }),
      bar,
      h('div', { style: 'margin-top:10px' },
        kv('Maintenance', money(reqv, 0)),
        kv('Margin loan', money(mg.debt, 0)),
        kv('A call after a drop of', isNum(mg.drop_to_call) ? pct(mg.drop_to_call, 1) : '-', mg.drop_to_call < 0.05 ? 'down' : ''),
        mg.crypto ? kv('Crypto, not counted', money(mg.crypto, 0)) : null,
        ...(m.transit || []).filter((t) => t.status === 'in transit' && t.to_name === mg.account).map((t) => kv('On the way in, not counted yet', money(t.value, 0))),
        mg.interest_year ? kv(`Interest at ${pct(mg.rate, 2)}`, money(-mg.interest_year, 0) + ' a year') : null,
        isNum(mg.vs_m2) ? kv(`Loan rate vs M2 (${pct(mg.m2_yoy, 2)})`, `${Math.abs(mg.vs_m2 * 100).toFixed(2)} pts ${mg.vs_m2 <= 0 ? 'under' : 'over'}`, mg.vs_m2 <= 0 ? 'up' : 'down') : null,
        m.leverage ? kv('Leverage', m.leverage.margin.toFixed(2) + 'x') : null),
      mg.crypto ? h('p', { class: 'note', text: `Crypto carries no margin. Selling it pays down the loan, so every ${price(1000, 0)} sold adds ${price(1000, 0)} to the buffer (enough for about ${price(4000, 0)} of stock at 25%).` }) : null);
  }
  function openAccounts(m) { return m.accounts.filter((a) => (a.rows || a.equity) && !a.closed); }
  function accountsCard(m) {
    const hs = m.history;
    const rows = openAccounts(m).sort((a, b) => b.equity - a.equity);
    const moving = (m.transit || []).filter((t) => t.status === 'in transit' && t.value > 0.5);
    const pend = m.accounts.filter((a) => (a.pending || []).length);
    const pendTxt = pend.map((a) => `${a.name} ${a.pending.slice(0, 5).map((x) => `${x.qty > 0 ? '+' : ''}${qty(x.qty)} ${x.symbol.replace('-USD', '')}`).join(', ')}${a.pending.length > 5 ? ` and ${a.pending.length - 5} more` : ''}`).join('; ');
    return panel('Accounts', ['/settings', 'Manage'], h('div', { class: 'rows' }, rows.map((a, i) => {
      const ser = (hs.accounts || []).find((x) => x.account === a.name);
      const vals = ser ? ser.value.slice(-90) : [];
      return h('div', { class: 'row acct' }, h('div', { class: 'l' }, h('div', { class: 'aname', text: a.name }), h('div', { class: 'faint', style: 'font-size:12px', text: a.value_note ? 'total only' : a.is_margin && a.cash < 0 ? `loan ${money(-a.cash, 0)}` : `cash ${money(a.cash, 0)}` })),
        sparkline(vals, ACCT_COLORS[i % ACCT_COLORS.length]),
        h('div', { class: 'r' }, h('div', { text: money(a.equity, 0) }), h('div', { class: tone(a.day_change), style: 'font-size:12.5px', text: smoney(a.day_change, 0) })));
    }), moving.map((t) => h('div', { class: 'row acct' }, h('div', { class: 'l' }, h('div', { class: 'aname', text: `On the way to ${t.to_name}` }), h('div', { class: 'faint', style: 'font-size:12px', text: `from ${t.from_name}, since ${shortDate(t.start)}${t.expected ? `, expected ${shortDate(t.expected)}` : ''}` })),
      h('span', {}), h('div', { class: 'r' }, h('div', { text: money(t.value, 0) }), h('div', { class: 'faint', style: 'font-size:12.5px', text: 'in transit' }))))),
    h('div', { style: 'margin-top:12px' }, segbar(rows.map((a) => ({ name: a.name, v: a.equity })), ACCT_COLORS)),
    pend.length ? h('p', { class: 'faint', style: 'font-size:12px;margin:10px 0 0', text: `Taken from the brokers’ positions until the activity rows catch up (they arrive about a day later): ${pendTxt}.` }) : null);
  }
  function moversCard(m) {
    const movers = [...m.positions].sort((a, b) => Math.abs(b.day_change) - Math.abs(a.day_change)).slice(0, 7);
    return panel('Biggest moves today', null, h('div', { class: 'rows' }, movers.map((x) => h('div', { class: 'row click', onclick: () => openSymbol(x.symbol) },
      h('div', { class: 'l' }, h('div', { class: 'sym', text: x.symbol }), h('div', { class: 'name', text: x.name })),
      h('div', { class: 'r' }, h('div', { class: tone(x.day_change), text: smoney(x.day_change, 0) }), h('div', { class: 'faint', style: 'font-size:12px', text: spct((x.change_pct || 0) / 100, 2) }))))));
  }
  function comingCard(m) {
    const today = m.today, limit = new Date(today + 'T12:00:00'); limit.setDate(limit.getDate() + 21);
    const end = limit.toISOString().slice(0, 10), ev = [];
    for (const u of (m.income.upcoming || [])) if (u.pay >= today && u.pay <= end) ev.push({ d: u.pay, what: `${u.symbol} dividend`, v: money(u.amount), tag: u.declared ? '' : 'estimate' });
    for (const p of m.positions) if (p.earnings && p.earnings >= today && p.earnings <= end) ev.push({ d: p.earnings, what: `${p.symbol} earnings`, v: '', tag: '' });
    const exp = {};
    for (const o of (m.options.open || [])) if (o.status === 'open' && o.expiry >= today && o.expiry <= end) (exp[o.expiry] = exp[o.expiry] || []).push(`${o.underlying} ${o.strike}${o.right}`);
    for (const [d, list] of Object.entries(exp)) ev.push({ d, what: `Options expire: ${list.join(', ')}`, v: '', tag: '' });
    for (const x of (m.macro || [])) if (x.d <= end) ev.push({ d: x.d, what: 'Fed rate decision', v: '', tag: '' });
    ev.sort((a, b) => (a.d < b.d ? -1 : 1));
    return panel('Coming up', null, ev.length ? h('div', { class: 'rows' }, ev.slice(0, 12).map((e) => h('div', { class: 'row' },
      h('div', { class: 'l' }, h('div', { class: 'faint mono', style: 'font-size:12px', text: new Date(e.d + 'T12:00:00').toLocaleDateString([], { weekday: 'short', month: 'numeric', day: 'numeric' }) }), h('div', { style: 'font-size:13.5px', text: e.what })),
      h('div', { class: 'r' }, e.v ? h('div', { text: e.v }) : null, e.tag ? h('span', { class: 'tag est', text: e.tag }) : null)))) : h('p', { class: 'dim', text: 'Nothing in the next three weeks.' }));
  }
  function receivedCard(m) {
    const subs = { dividend: '', dividend_in_lieu: 'paid while lent out', return_of_capital: 'return of capital' };
    const rows = (m.income.received_rows || []).filter((r) => r.sub in subs);
    const d30 = new Date(m.today + 'T12:00:00'); d30.setDate(d30.getDate() - 30);
    const iso30 = d30.toISOString().slice(0, 10), year = m.today.slice(0, 4);
    const sum = (f) => rows.filter(f).reduce((a, r) => a + r.amount, 0);
    const shortAcct = (a) => (a || '').replace('Robinhood ', 'RH ').replace('Fidelity ', 'Fid ');
    return panel('Recent dividends', ['/income', 'All income'],
      h('div', { class: 'feed-tot' }, h('span', {}, h('div', { class: 'label', text: 'Last 30 days' }), h('div', { class: 'up', text: money(sum((r) => r.date >= iso30)) })),
        h('span', {}, h('div', { class: 'label', text: `In ${year}` }), h('div', { class: 'up', text: money(sum((r) => r.date.startsWith(year))) }))),
      rows.length ? h('div', { class: 'rows' }, rows.slice(0, 12).map((r) => h('div', { class: 'row' },
        h('div', { class: 'l' }, h('div', {}, h('span', { class: 'sym', text: r.symbol || 'Cash' }), h('span', { class: 'faint', style: 'font-size:12px;margin-left:8px', text: shortAcct(r.account) })),
          h('div', { class: 'faint mono', style: 'font-size:12px', text: new Date(r.date + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) + (subs[r.sub] ? `  \u00b7  ${subs[r.sub]}` : '') })),
        h('div', { class: 'r up', text: '+' + money(r.amount) })))) : h('p', { class: 'dim', text: 'No dividends yet.' }));
  }

  function incomeCard(m) {
    const inc = m.income, n = inc.next12 || {}, years = (inc.by_year || []).filter((b) => b.year >= new Date().getFullYear() - 2);
    const box = h('div', { class: 'chart' });
    const part = (label, v, cls) => h('div', { class: 'ipart' }, h('div', { class: 'label', text: label }), h('div', { class: 'v ' + (cls || ''), text: smoney(v, 0) }));
    requestAnimationFrame(() => barChart(box, {
      labels: years.map((b) => String(b.year) + (b.kind === 'projected' ? '*' : '')),
      values: years.map((b) => b.net + (b.kind === 'so far' ? (b.rest_dividends || 0) + (b.rest_options || 0) + (b.rest_interest || 0) : 0)),
      colorFn: (v, i) => (years[i].kind === 'projected' ? 'rgba(57,135,229,0.45)' : v >= 0 ? 'var(--s-blue)' : 'var(--down)'), name: 'Net income', height: 170, fmt: smoney, toneTip: true,
      tipTitle: (i) => `${years[i].year} ${years[i].kind === 'projected' ? '(projected)' : years[i].kind === 'so far' ? '(so far plus rest of year)' : ''}`,
      extraTip: (i) => [{ label: 'Dividends', value: money(years[i].dividends + (years[i].rest_dividends || 0), 0) }, { label: 'Options', value: smoney(years[i].options + (years[i].rest_options || 0), 0) }, { label: 'Margin interest', value: money(years[i].interest + (years[i].rest_interest || 0), 0) }] }));
    return panel('Income', ['/income', 'Income details'],
      h('div', { class: 'iparts' }, part('Dividends, 12 months', n.dividends, ''), part('Options, at last year’s pace', n.options_pace, tone(n.options_pace)), part('Margin interest', n.interest, 'down'), part('Net', n.net, tone(n.net))),
      box, h('p', { class: 'faint', style: 'font-size:12px;margin:6px 0 0', text: `Years with * are projected: today’s holdings with each one’s own dividend growth (about ${pct(n.dividend_growth, 1)} a year on average), options at the last 12 months’ pace, interest on today’s loan.` }));
  }
  function allocationCard(m) {
    const d = m.diversification || {};
    const rank = (n) => { const i = TYPE_ORDER.indexOf(n); return i < 0 ? 99 : i; };
    const classes = (d.classes || []).map((c) => ({ name: c.name, v: c.weight * m.invested })).sort((a, b) => rank(a.name) - rank(b.name));
    return panel('What you own', ['/analysis', 'Analysis'],
      h('h3', { text: 'By type' }), segbar(classes, classes.map((c) => TYPE_COLORS[c.name] || '#77739C')),
      h('h3', { text: 'Top sectors (funds looked through)', style: 'margin-top:16px' }), hbars((d.sectors || []).slice(0, 8), pct, sectorColor),
      h('div', { style: 'margin-top:10px' },
        h('div', { class: 'kv' }, h('span', { text: 'Largest holding' }), h('span', { text: m.positions[0] ? `${m.positions[0].symbol} ${pct(m.positions[0].weight)}` : '-' })),
        h('div', { class: 'kv' }, h('span', { text: 'Top 5 holdings' }), h('span', { text: pct(d.top5, 0) })),
        h('div', { class: 'kv' }, h('span', { text: 'Moves with bitcoin' }), h('span', { text: pct(((d.links || {}).bitcoin || {}).weight, 0) }))));
  }
  function optionsCard() {
    const c = panel('Options this week', ['/options', 'Full menu'], h('div', { class: 'loading', text: 'Reading chains' }));
    const fillIt = (mn) => {
      if (!mn || mn.error) { fill(c, c.firstChild, h('p', { class: 'dim', text: mn && mn.error ? mn.error : 'Not available.' })); return; }
      const ideas = [...mn.puts.filter((r) => r.meets_target), ...mn.calls.filter((r) => r.pick === 'target' || r.pick === 'safe')].slice(0, 4);
      fill(c, c.firstChild, h('p', { class: 'dim', style: 'margin:0 0 8px;font-size:13px', text: `Friday ${shortDate(mn.expiry)}. ${mn.calls.length} call and ${mn.puts.length} put ideas pass your rules; ${mn.skips.length} skips.` }),
        ideas.length ? h('div', { class: 'rows' }, ideas.map((r) => h('div', { class: 'row' }, h('div', { class: 'l' }, h('div', { class: 'sym', text: `${r.symbol} ${r.strike} ${r.right === 'P' ? 'CSP' : 'CC'}` }), h('div', { class: 'faint', style: 'font-size:12px', text: r.reason.split(';')[0] })),
          h('div', { class: 'r' }, h('div', { text: `${pct(r.week, 2)} return` }), h('div', { class: 'faint', style: 'font-size:12px', text: `${money(r.credit, 0)} on ${money(r.collateral * r.contracts, 0)} held` }))))) : h('p', { class: 'dim', text: 'Nothing meets the target this week.' }),
        mn.notes.length ? h('p', { class: 'note', text: mn.notes[0] }) : null);
    };
    if (ST.menu) fillIt(ST.menu); else api('/api/options').then((x) => { ST.menu = x; fillIt(x); }).catch((e) => fillIt({ error: e.message }));
    return c;
  }
  function marketCard() {
    const c = panel('Market valuation', ['/market', 'Market'], h('div', { class: 'loading', text: 'Loading' }));
    (ST.market ? Promise.resolve(ST.market) : api('/api/market').then((x) => (ST.market = x))).then((mk) => {
      if (!mk || mk.error || !mk.overall) { c.remove(); return; }
      const rows = ['sp500_pe', 'ndx_pe', 'cape'].filter((k) => mk.series[k]).map((k) => {
        const sr = mk.series[k], w = sr.windows['20'];
        const z = Math.max(-3, Math.min(3, w.z || 0)), dot = h('i'); dot.style.left = ((z + 3) / 6) * 100 + '%';
        return h('div', { class: 'zrow' }, h('span', { text: sr.short }), h('b', { class: 'mono', text: sr.current.toFixed(1) }), h('div', { class: 'zbar' }, h('span', { class: 'fair' }), dot), h('span', { class: 'tag ' + (VERDICT_TONE[w.verdict] || ''), text: w.verdict }));
      });
      fill(c, c.firstChild, h('div', { class: 'mid', style: 'font-family:var(--sans);font-size:19px;font-weight:600', text: mk.overall.verdict + (mk.overall.lean ? `, ${mk.overall.lean}` : '') }),
        h('div', { style: 'margin-top:10px' }, rows), h('p', { class: 'faint', style: 'font-size:12px;margin:8px 0 0', text: 'P/E against the last 20 years; the shaded middle is fair value.' }));
    }).catch(() => c.remove());
    return c;
  }
  // ---- doom score (fin/doom.py) -------------------------------------------------------------------------------
  const longDate = (iso) => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  function doomColor(v) {            // sequential: dark slate at 0, amber at 50, red at 100
    const t = Math.max(0, Math.min(100, v || 0)), [a, b, k] = t <= 50 ? [[66, 66, 104], [176, 122, 52], t / 50] : [[176, 122, 52], [229, 72, 77], (t - 50) / 50];
    return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * k)).join(',')})`;
  }
  function getDoom(brief) {
    const key = brief ? 'doomBrief' : 'doomFull', c = ST[key];
    if (c && Date.now() - c.at < 600e3) return Promise.resolve(c.d);
    return api('/api/doom' + (brief ? '?brief=1' : '')).then((d) => { ST[key] = { d, at: Date.now() }; return d; });
  }
  // A cycle's peak as shown: the highest reading inside any day when the app has one (refresh_hilo in fin/doom.py),
  // else the highest closing reading. Cycles and eras themselves are judged on closing readings.
  const etTime = (ts) => new Date(ts * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' }) + ' ET';
  const doomPeak = (c) => (isNum(c.high) ? c.high : c.peak);
  const doomPeakDate = (c) => (isNum(c.high) ? c.high_date : c.peak_date);
  const doomPeakHow = (c) => (!isNum(c.high) ? '' : c.high_src === 'seen' ? 'as you saw it live' : c.high_src === 'live' ? `read live${c.high_at ? ' at ' + etTime(c.high_at) : ''}` : `inside the day${c.high_at ? ', ' + etTime(c.high_at) : ''}`);
  function doomHead(d, big) {
    const n = d.now, rng = isNum(n.high) && isNum(n.low) && Math.round(n.high) !== Math.round(n.low) ? ` Range that day: ${n.low.toFixed(0)} to ${n.high.toFixed(0)}.` : '';
    return h('div', { class: 'dhead' + (big ? ' lg' : '') }, h('div', { class: 'dscore', text: Math.round(n.score) }),
      h('div', {}, h('div', { class: 'dlabel', text: n.label }), h('div', { class: 'faint', style: 'font-size:12px', text: `0 is calm, 100 is panic. ${longDate(n.date)}.${rng}` })));
  }
  function doomGauge(d) {
    const g = h('div', { class: 'dgauge' });
    for (const mk of d.marks) {
      const t = h('b', { class: 'dmark' }); t.style.left = mk.score + '%';
      t.addEventListener('pointermove', (ev) => showTip(ev.clientX, ev.clientY, mk.name, [{ label: longDate(mk.date), value: mk.score.toFixed(0) }]));
      t.addEventListener('pointerleave', hideTip);
      g.append(t);
    }
    const now = h('i', { class: 'dnow' }); now.style.left = d.now.score + '%';
    g.append(now);
    return h('div', { class: 'dgauge-wrap' }, g, h('div', { class: 'dzones' }, [...d.bands].reverse().map((b) => h('span', { text: b.name }))));
  }
  function doomGroups(d) {
    return h('div', { class: 'dgroups' }, d.groups.map((gr) => {
      const v = d.now.groups[gr.key], bar = h('i');
      bar.style.width = (v || 0) + '%'; bar.style.background = doomColor(v);
      return h('div', {}, h('span', { text: gr.name }), h('b', { class: 'mono', text: isNum(v) ? v.toFixed(0) : '-' }), h('div', { class: 'dbar' }, bar));
    }));
  }
  function doomCompare(d) {
    const rows = [{ name: 'Today', date: d.now.date, score: d.now.score, today: true }, ...d.marks.map((mk) => ({ name: mk.name, date: mk.date, score: mk.score }))]
      .sort((a, b) => b.score - a.score);
    return h('div', { class: 'dcmp' }, rows.map((r) => {
      const bar = h('i'); bar.style.width = r.score + '%'; bar.style.background = doomColor(r.score);
      return h('div', { class: 'drow' + (r.today ? ' today' : '') }, h('span', { class: 'n' }, h('b', { text: r.name }), h('em', { text: longDate(r.date) })),
        h('div', { class: 'dbar' }, bar), h('span', { class: 'v mono', text: r.score.toFixed(0) }));
    }));
  }
  function doomLine(d) {
    const out = [], ref = d.marks.find((mk) => mk.key === 'tariff'), b = d.base, n = d.now.score;
    if (ref) {
      const diff = Math.round(ref.score) - Math.round(n);
      out.push(diff > 0 ? `${diff} point${diff === 1 ? '' : 's'} under the April 8, 2025 bottom (${ref.score.toFixed(0)}).` : diff < 0 ? `${-diff} point${diff === -1 ? '' : 's'} above the April 8, 2025 bottom (${ref.score.toFixed(0)}).` : 'Level with the April 8, 2025 bottom.');
    }
    if (b) out.push(`The ${b.count} times since 2010 the score first reached ${b.level}, the S&P 500 fell a median ${pct(-b.median_further_drop, 1)} more before turning (worst ${pct(-b.worst_further_drop, 0)}), the score topped out at a median ${b.median_peak.toFixed(0)}, and the S&P 500 was higher a year later ${b.up_12m} of ${b.n_12m} times.`);
    return out.join(' ');
  }
  function doomButton(d) {
    const cur = d.cycle && d.cycle.current;
    return h('a', { class: 'btn doom-btn' + (cur ? ' on' : ''), href: '/doom' },
      h('span', { text: 'Doom dashboard' }), h('em', { text: cur ? `${cur.phase}, day ${cur.length}, peak ${doomPeak(cur).toFixed(0)}` : 'standby' }));
  }
  function doomCard() {
    const c = panel('Doom score', ['/market', 'Details'], h('div', { class: 'loading', text: 'Loading' }));
    getDoom(true).then((d) => {
      if (!d || d.error || !d.now) { fill(c, c.firstChild, h('p', { class: 'faint', text: (d && d.error) || 'No data yet.' })); return; }
      fill(c, c.firstChild, doomHead(d), doomGauge(d), doomGroups(d), doomCompare(d), h('p', { class: 'dim', style: 'font-size:13px;margin:12px 0 0', text: doomLine(d) }), doomButton(d));
    }).catch(() => c.remove());
    return c;
  }
  function chartCard(m) {
    const c = h('div', { class: 'card panel' });
    perfChart(c, m, tier() >= 2 ? 340 : 260);
    return c;
  }

  // what was sold and bought on each day (today's come from the broker's share counts until the rows arrive)
  function tradesByDay(m) {
    const byDay = {};
    for (const a of m.accounts) for (const t of a.trades || []) {
      const sell = t.side === 'sell' || (t.side === 'out' && t.sub === 'pending'), buy = t.side === 'buy' || (t.side === 'in' && t.sub === 'pending');
      if (!sell && !buy) continue;
      const v = t.side === 'sell' || t.side === 'buy' ? Math.abs(t.amount || 0) : Math.abs((t.qty || 0) * (t.price || 0));
      const day = (byDay[t.date] = byDay[t.date] || { sold: {}, bought: {} });
      const k = sell ? 'sold' : 'bought', sym = t.symbol.replace('-USD', '');
      day[k][sym] = (day[k][sym] || 0) + v;
    }
    return byDay;
  }
  const topTrades = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${k} ${money(v, 0)}`).join(', ');
  function assetsButton(m) {
    const g = m.glory;
    return h('a', { class: 'btn doom-btn', href: '/assets' }, h('span', { text: 'Assets dashboard' }),
      h('em', { text: g ? `Glory Days Worth ${money(g.now, 0)}` : '' }));
  }

  // assets: what you own before the margin loan; a sale that pays down the loan shows as a drop here, not in net worth.
  // It shares the range chips with the net worth chart (one choice sets both) and its hover line (group 'home').
  function assetsCard(m, opts = {}) {
    const hs = m.history;
    if (!hs || !hs.assets || hs.dates.length < 3) return null;
    const R = ST.range, ikey = INTRA_KEY[R];
    const A = hs.assets, L = hs.loan, V = hs.value, F = hs.flow, dates = hs.dates, n = dates.length, i1 = n - 1;
    const color = { a: 'var(--s-you)', l: 'var(--s-loan)' };
    const box = h('div', { class: 'chart' }, ikey ? h('div', { class: 'loading', text: 'Loading' }) : null);
    const head = h('div', { class: 'panel-head ahead' }, h('h2', { text: 'Assets' }),
      h('div', { class: 'chips' }, CHART_RANGES.map((k) => h('button', { class: 'chip' + (R === k ? ' on' : '') + (k === 'Live' ? ' live' : ''), text: k, onclick: () => { ST.range = k; render(); } }))));
    const byDay = tradesByDay(m), top = topTrades;
    const loanDays = (i0) => {
      const big = [];
      for (let i = i0 + 1; i <= i1; i++) {
        const dl = L[i] - L[i - 1];
        if (Math.abs(dl) >= 500) big.push({ i, d: dates[i], dl, flow: F[i], day: byDay[dates[i]] || { sold: {}, bought: {} } });
      }
      return big;
    };
    const daysList = (big) => (big.length ? h('div', { class: 'adays' }, h('div', { class: 'kv faint', style: 'font-size:12.5px' }, h('span', { text: 'Days the loan moved $500 or more' }), h('span', { text: 'Loan' })),
      big.slice(-6).reverse().map((b) => h('div', { class: 'kv' }, h('span', {}, h('b', { style: 'font-weight:500;color:var(--text)', text: shortDate(b.d) + ' ' }),
        [b.dl < 0 ? (top(b.day.sold) ? 'sold ' + top(b.day.sold) : '') : (top(b.day.bought) ? 'bought ' + top(b.day.bought) : ''), b.flow > 100 ? `money in ${money(b.flow, 0)}` : b.flow < -100 ? `money out ${money(-b.flow, 0)}` : ''].filter(Boolean).join('; ') || (b.dl < 0 ? 'paid down' : 'borrowed'),
        b.d === m.today ? h('span', { class: 'faint', text: ' (from share counts until the rows arrive)' }) : null),
        h('span', { class: 'aamt', text: smoney(b.dl, 0) })))) : null);
    const lineH = tier() >= 2 ? 280 : 220;

    if (ikey) {
      // Live, 1D, 1W: the net worth chart's points plus the loan (today's holdings priced through the day)
      const sum = h('div', { class: 'asum' }, h('span', { class: 'mid', text: money(m.assets, 0) }));
      const legend = h('div', { class: 'legend' }, h('span', {}, mkKey(color.a), 'Assets'));
      const days = h('div');
      const c = h('div', { class: 'card panel' }, head, sum,
        h('p', { class: 'faint', style: 'font-size:12.5px;margin:2px 0 8px', text: `Live, 1D and 1W price the holdings you have now through the day, like the net worth chart, plus today’s loan of ${money(m.loan || 0, 0)}. Sales show as drops on 1M and longer.` }),
        legend, box, days, opts.page ? null : assetsButton(m));
      intraData(ikey).then(async (d) => {
        await new Promise((res) => requestAnimationFrame(res));
        if (!box.isConnected) return;
        if (d.error || !d.value || d.value.length < 2) {
          fill(box, h('div', { class: 'faint', text: ikey === 'live' ? 'The live line fills in as prices refresh.' : (d.error || 'No intraday prices yet.') }));
          return;
        }
        const k = d.value.length, loan = d.value.map((_, i) => (d.loan && isNum(d.loan[i]) ? d.loan[i] : m.loan || 0));
        const AV = d.value.map((v, i) => v + loan[i]);
        const base = ikey === '1d' && isNum(d.prev_close) ? d.prev_close + (m.loan || 0) : AV[0], chg = AV[k - 1] - base;
        fill(sum, h('span', { class: 'mid', text: money(AV[k - 1], 0) }),
          h('span', { class: 'mono ' + tone(chg), text: `${smoney(chg, 0)} (${spct(base ? chg / base : null, 2)}) ${INTRA_WORD[ikey]}` }));
        legend.append(h('span', {}, h('i', { class: 'dash' }), ikey === '1d' ? 'Previous close' : 'Start'));
        const ax = intraAxis(d, ikey, box);
        lineChart(box, { ...ax, series: [{ name: 'Assets', color: color.a, values: AV }], refs: [{ y: base, color: 'var(--s-ref)' }],
          fmt: (v) => money(v, 0), height: lineH, label: 'Assets ' + INTRA_WORD[ikey],
          extraTip: (i) => [{ label: 'Net worth', value: money(d.value[i], 0) }, { label: 'Margin loan', value: money(loan[i], 0) }],
          sync: { ...ax.sync, group: 'home', id: 'assets', label: (i) => ['Assets', money(AV[i], 0)] } });
        // the days inside the range when the loan moved, from the daily history
        const from = d.labels[0].slice(0, 10), j = dates.findIndex((x) => x >= from);
        fill(days, daysList(loanDays(j < 0 ? i1 : Math.max(0, j - 1))));
      });
      return c;
    }

    const i0 = rangeSlice(dates, R);
    let flows = 0;
    for (let i = i0 + 1; i <= i1; i++) flows += F[i];
    const dA = A[i1] - A[i0], dL = L[i1] - L[i0], market = V[i1] - V[i0] - flows;
    const big = loanDays(i0);
    // asset units over the range, 100 at its start: only buying and selling move them, never prices
    const U = hs.units || [], u0 = U[i0] || 1, ui = U.slice(i0).map((v) => (isNum(v) ? (v / u0) * 100 : null));
    const uchg = (k) => (k > 0 && U[k - 1] ? U[k] / U[k - 1] - 1 : 0);
    const ucolors = ui.map((_, j) => { const c = uchg(j + i0); return c >= 0.0005 ? 'var(--bar-up)' : c <= -0.0005 ? 'var(--bar-down)' : 'var(--bar-flat)'; });
    let uhi = i0;
    for (let k = i0; k <= i1; k++) if (U[k] > U[uhi]) uhi = k;
    const fromHigh = U[uhi] ? U[i1] / U[uhi] - 1 : 0;
    const series = [{ name: 'Assets', color: color.a, values: A.slice(i0) }, { name: 'Margin loan', color: color.l, values: L.slice(i0) }];
    // the loan dots can be switched off to see the assets line on its own (remembered in this browser)
    if (ST.adots == null) { ST.adots = true; try { ST.adots = localStorage.getItem('basis.loanDots') !== 'off'; } catch (e) { /* no storage */ } }
    const dotsToggle = big.some((b) => b.dl <= -1000) ? h('button', { class: 'lgbtn' + (ST.adots ? '' : ' off'), type: 'button', 'aria-pressed': String(ST.adots),
      title: ST.adots ? 'Hide the dots' : 'Show the dots', onclick: () => {
        ST.adots = !ST.adots;
        try { localStorage.setItem('basis.loanDots', ST.adots ? 'on' : 'off'); } catch (e) { /* no storage */ }
        render();
      } }, mkKey(color.l, true), 'Loan paid down $1,000+') : null;
    const cell = (label, v, sub, plain) => h('div', { class: 'acell' }, h('div', { class: 'faint', text: label }), h('div', { class: 'mono ' + (plain ? '' : tone(v)), text: smoney(v, 0) }), h('div', { class: 'faint small', text: sub }));
    const c = h('div', { class: 'card panel' }, head,
      h('div', { class: 'asum' }, h('span', { class: 'mid', text: money(A[i1], 0) }),
        h('span', { class: 'mono ' + tone(dA), text: `${smoney(dA, 0)} (${spct(A[i0] ? dA / A[i0] : null, 1)}) since ${shortDate(dates[i0])}` })),
      h('p', { class: 'faint', style: 'font-size:12.5px;margin:2px 0 8px', text: 'Everything you own at market value, before the margin loan. Selling to pay down the loan lowers assets and the loan by the same amount, so net worth stays put while what you own shrinks.' }),
      h('div', { class: 'legend' }, series.map((sr) => h('span', {}, mkKey(sr.color), sr.name)), dotsToggle),
      box,
      ui.length ? h('div', { class: 'aunits' },
        h('div', { class: 'legend', style: 'margin:6px 0 2px' }, h('span', { class: 'faint', text: 'Asset units, bars under the chart:' }),
          h('span', {}, mkKey('var(--bar-up)'), 'bought or added'), h('span', {}, mkKey('var(--bar-down)'), 'sold or sent out')),
        h('p', { class: 'faint', style: 'font-size:12.5px;margin:0', text: `Your holdings counted as units of a fund made of them: price moves change what a unit is worth, never how many you have. 100 on ${shortDate(dates[i0])}, ${ui[ui.length - 1].toFixed(1)} now` + (fromHigh < -0.0005 ? `, ${pct(-fromHigh, 1)} fewer than the high on ${shortDate(dates[uhi])}.` : '.') })) : null,
      h('div', { class: 'abreak' },
        cell('Market moves', market, 'prices, dividends, interest'),
        cell('Money in or out', flows, 'deposits, withdrawals, coins sent in', true),
        cell(dL <= 0 ? 'Loan paid down' : 'Borrowed more', dL, dL <= 0 ? 'sales and deposits that paid the loan' : 'bought on margin', true),
        h('div', { class: 'acell' }, h('div', { class: 'faint', text: 'Assets' }), h('div', { class: 'mono ' + tone(dA), text: smoney(dA, 0) }), h('div', { class: 'faint small', text: `loan now ${money(L[i1], 0)}` }))),
      daysList(big), opts.page ? null : assetsButton(m));
    requestAnimationFrame(() => lineChart(box, { dates: dates.slice(i0), series, fmt: (v) => money(v, 0), height: lineH + (ui.length ? 70 : 0), label: 'Assets and margin loan over time',
      marks: ST.adots ? big.filter((b) => b.dl <= -1000).map((b) => ({ i: b.i - i0, v: A[b.i], color: color.l })) : [],
      strip: ui.length ? { values: ui, colors: ucolors, height: 58, fmt: (v) => v.toFixed(0) } : null,
      sync: { group: 'home', id: 'assets', keys: dayKeys(dates.slice(i0)), label: (i) => ['Assets', money(A[i + i0], 0)] },
      extraTip: (i) => {
        const k = i + i0, dl = k > 0 ? L[k] - L[k - 1] : 0, c = uchg(k);
        return [{ label: 'Net worth', value: money(A[k] - L[k], 0) }]
          .concat(Math.abs(dl) >= 1 ? [{ label: dl < 0 ? 'Loan paid down' : 'Borrowed', value: money(Math.abs(dl), 0) }] : [])
          .concat(isNum(ui[i]) ? [{ label: 'Asset units', value: ui[i].toFixed(1) + (Math.abs(c) >= 0.0005 ? ` (${spct(c, 1)})` : ''), cls: c >= 0.0005 ? 'up' : c <= -0.0005 ? 'down' : '' }] : []);
      } }));
    return c;
  }

  // Live, 1D and 1W data, shared by the net worth and assets charts (one request per refresh)
  const INTRA_KEY = { Live: 'live', '1D': '1d', '1W': '5d' };
  function intraData(key) {
    ST.intra = ST.intra || {};
    const c = ST.intra[key], maxAge = key === 'live' ? 25e3 : 110e3;
    if (c && Date.now() - c.at < maxAge) return c.p;
    const p = api('/api/intraday?range=' + key).catch((e) => ({ error: e.message }));
    ST.intra[key] = { p, at: Date.now() };
    return p;
  }
  // x axis: Live and 1D run on the clock (Live the last hour, 1D 4 AM to 8 PM Eastern); 1W steps bar by bar with a tick
  // at the start of each day, so nights and the weekend take no room
  function intraAxis(d, key, box) {
    const n = d.value.length, labels = d.labels;
    const hhmm = (l) => { const [hh, mm] = l.slice(11, 16).split(':').map(Number); return { hh, mm }; };
    const vbands = [], shade = 'rgba(170,165,230,0.06)', plotW = Math.max(280, box.clientWidth || 600) - 64;
    let xopts;
    if (key === '5d') {
      const ticks = []; let lastDay = '';
      const days = new Set(labels.map((l) => l.slice(0, 10))).size, roomy = plotW / Math.max(1, days) >= 84;
      labels.forEach((l, i) => { const dd = l.slice(0, 10); if (dd !== lastDay) { ticks.push([i, new Date(dd + 'T12:00:00').toLocaleDateString('en-US', roomy ? { weekday: 'short', month: 'numeric', day: 'numeric' } : { weekday: 'short' })]); lastDay = dd; } });
      xopts = { ticks };
    } else {
      const off = isNum(d.et_offset) ? d.et_offset : -5 * 3600;
      const dom = d.domain || [d.ts[0], d.ts[n - 1]];
      xopts = { x: d.ts, xdomain: dom, xticks: timeTicks(dom[0], dom[1], off, plotW, key === '1d' ? 60 : 1) };
      if (key === '1d' && d.domain) vbands.push({ x0: dom[0], x1: dom[0] + 330 * 60, fill: shade }, { x0: dom[0] + 720 * 60, x1: dom[1], fill: shade });
      if (key === 'live' && d.domain) {
        const outside = (t) => { const e = new Date((t + off) * 1000), mm = e.getUTCHours() * 60 + e.getUTCMinutes(), wd = e.getUTCDay(); return wd === 0 || wd === 6 || mm < 570 || mm >= 960; };
        let from = null;
        for (let t = dom[0]; t <= dom[1] + 30; t += 30) {
          const o = t <= dom[1] && outside(t);
          if (o && from === null) from = t;
          if (!o && from !== null) { vbands.push({ x0: from, x1: Math.min(t, dom[1]), fill: shade }); from = null; }
        }
      }
    }
    if (d.session && !d.domain) {
      let start = null;
      d.session.forEach((sn, i) => {
        const ext = sn === 'pre' || sn === 'post' || sn === 'closed';
        if (ext && start === null) start = i;
        if ((!ext || i === n - 1) && start !== null) { vbands.push({ i0: start, i1: ext ? i : i - 1, fill: shade }); start = null; }
      });
    }
    const titleFmt = (dd, i) => { const l = labels[i]; const { hh, mm } = hhmm(l); const sn = d.session ? { pre: ' pre-market', post: ' after hours', closed: ' closed' }[d.session[i]] || '' : '';
      return (key === '5d' ? new Date(l.slice(0, 10) + 'T12:00:00').toLocaleDateString([], { weekday: 'short', month: 'numeric', day: 'numeric' }) + ' ' : '') + `${hh % 12 || 12}:${String(mm).padStart(2, '0')} ${hh < 12 ? 'AM' : 'PM'} ET` + sn; };
    return { dates: labels.map((l) => l.slice(0, 10)), vbands, titleFmt, ...xopts,
      sync: { keys: d.ts, tol: key === '5d' ? 1800 : key === '1d' ? 300 : 60 } };
  }
  const INTRA_WORD = { live: 'last hour', '1d': 'today, with pre-market and after-hours', '5d': 'past week' };
  async function intradayChart(el, m, controls, height) {
    const key = INTRA_KEY[ST.range];
    const summary = h('div', { class: 'range-sum' }), legendBox = h('div', { class: 'legend' }), box = h('div', { class: 'chart' }, h('div', { class: 'loading', text: 'Loading' }));
    el.append(controls, summary, legendBox, box);
    const d = await intraData(key);
    await new Promise((res) => requestAnimationFrame(res));   // wait until the card is on the page (cached data arrives at once)
    if (!box.isConnected) return;
    if (d.error || !d.value || d.value.length < 2) {
      fill(box, h('div', { class: 'faint', text: key === 'live' ? 'The live line fills in as prices refresh: every 30 seconds while the market is open, every minute before and after it, every 5 minutes overnight.' : (d.error || 'No intraday prices yet.') }));
      return;
    }
    const n = d.value.length;
    const base = key === '1d' && isNum(d.prev_close) ? d.prev_close : d.value[0];
    const chg = d.value[n - 1] - base;
    const word = INTRA_WORD[key];
    fill(summary, h('b', { class: tone(chg), text: `${smoney(chg, 0)} (${spct(chg / base, 2)})` }), h('span', { class: 'dim', text: ` ${word}` }));
    let series, fmt, yfmt, refs = [];
    if (chartMode() === 'value') {
      series = [{ name: 'You', color: 'var(--s-you)', values: d.value }];
      refs = [{ y: base, color: 'var(--s-ref)' }];
      fmt = (v) => money(v, 0);
      fill(legendBox, h('span', {}, mkKey('var(--s-you)'), 'You'), h('span', {}, h('i', { class: 'dash' }), key === '1d' ? 'Previous close' : 'Start'));
    } else {
      const norm = (arr, b0) => { const b = isNum(b0) ? b0 : arr.find(isNum); return arr.map((v) => (isNum(v) && b ? v / b - 1 : null)); };
      series = [{ name: 'You', color: 'var(--s-you)', values: norm(d.value, base) }];
      if (d.bench) {
        const bp = d.bench_prev || {};
        series.push({ name: 'VOO', color: 'var(--s-voo)', values: norm(d.bench.VOO || [], bp.VOO), dot: true });
        series.push({ name: 'QQQM', color: 'var(--s-qqqm)', values: norm(d.bench.QQQM || [], bp.QQQM), dot: true });
      }
      refs = [{ y: 0, color: 'var(--s-ref)' }];
      fmt = (v) => spct(v, 2); yfmt = axisPct;
      fill(legendBox, ...series.map((sr) => { const i = h('i', { class: sr.dot ? 'dot' : '' }); i.style.background = sr.color; return h('span', {}, i, sr.name); }));
    }
    const ax = intraAxis(d, key, box);
    lineChart(box, { ...ax, series, fmt, yfmt, refs, vbands: ax.vbands, height: height || 280, label: 'Portfolio value ' + word,
      sync: { ...ax.sync, group: 'home', id: 'networth', label: (i) => (chartMode() === 'value' ? ['Net worth', money(d.value[i], 0)] : ['You', fmt(series[0].values[i])]) } });
  }

  function home(page, m) {
    page.classList.add('wide');
    const t = tier();
    const al = alerts(m);
    const hero = heroCard(m), chart = chartCard(m), assets = assetsCard(m), heat = heatmap(m), income = incomeCard(m), alloc = allocationCard(m);
    const opts = optionsCard(), mkt = marketCard(), margin = marginCard(m), accts = accountsCard(m), movers = moversCard(m), coming = comingCard(m), recv = receivedCard(m);
    const doomc = doomCard();
    const col = (...k) => h('div', { class: 'col' }, ...k.filter(Boolean));
    const pair = (a, b) => h('div', { class: 'pair' }, a, b);
    let cols;
    if (t === 1) cols = [col(...al, hero, chart, margin, assets, doomc, heat, income, alloc, opts, mkt, accts, movers, coming, recv)];
    else if (t === 2) cols = [col(...al, hero, chart, assets, heat, pair(income, alloc), pair(opts, mkt)), col(margin, doomc, accts, movers, coming, recv)];
    else if (t === 3) cols = [col(...al, hero, chart, assets, heat), col(income, alloc, pair(opts, mkt)), col(margin, doomc, accts, movers, pair(coming, recv))];
    else cols = [col(...al, hero, chart, assets, income), col(heat, alloc, recv), col(doomc, opts, mkt, coming), col(margin, accts, movers)];
    page.append(h('div', { class: 'dash t' + t }, cols));
  }

  function positions(page, m) {
    const [key, dir] = ST.sort;
    page.append(h('div', { class: 'head' }, h('div', {}, h('h1', { text: 'Positions' }), h('p', { class: 'sub', text: `${m.positions.length} holdings across ${openAccounts(m).filter((a) => a.rows).length} account(s), ${money(m.invested, 0)} invested` })),
      h('div', { class: 'chips' },
        h('button', { class: 'chip' + (ST.showAth ? ' on' : ''), text: 'From all-time high', title: 'Show how far each price is under its all-time high', onclick: () => {
          ST.showAth = !ST.showAth;
          if (ST.showAth) ST.sort = ['ath', -1]; else if (ST.sort[0] === 'ath') ST.sort = ['value', -1];
          render(); } }),
        h('input', { type: 'text', placeholder: 'Filter', value: ST.filter, oninput: (e) => { ST.filter = e.target.value.toUpperCase(); renderTable(); } }))));
    const holder = h('div', { class: 'card' });
    page.append(holder);
    function renderTable() {
      const rows = m.positions.filter((p) => !ST.filter || p.symbol.includes(ST.filter) || (p.name || '').toUpperCase().includes(ST.filter));
      const get = { symbol: (p) => p.symbol, value: (p) => p.value, day: (p) => p.day_change, gain: (p) => (isNum(p.gain_pct) ? p.gain_pct : -9), weight: (p) => p.weight, income: (p) => p.income, qty: (p) => p.qty, ath: (p) => (isNum(p.from_ath) ? p.from_ath : -9), m2: (p) => (isNum(p.vs_m2) ? p.vs_m2 : -9) }[key] || ((p) => p.value);
      const priceCell = (p) => {
        const d = h('div', {}, h('div', { text: price(p.price) }), h('div', { class: tone(p.change_pct) + ' faint', style: 'font-size:12px', text: spct((p.change_pct || 0) / 100, 2) }),
          isNum(p.ext_pct) && Math.abs(p.ext_pct) >= 0.01 ? h('div', { class: tone(p.ext_pct), style: 'font-size:11.5px', text: `${spct(p.ext_pct / 100, 2)} ${(m.extended || {}).session === 'pre' ? 'pre' : 'after'}` }) : null);
        if (isNum(p.from_ath)) {
          d.addEventListener('pointermove', (ev) => showTip(ev.clientX, ev.clientY, p.symbol, athRows(p)));
          d.addEventListener('pointerleave', hideTip);
        }
        return d;
      };
      rows.sort((a, b) => (get(a) > get(b) ? 1 : get(a) < get(b) ? -1 : 0) * dir);
      const cols = [
        { label: 'Holding', key: 'symbol', get: (p) => h('div', {}, h('div', { class: 'sym', text: p.symbol }), h('div', { class: 'name', text: p.name })) },
        { label: 'Shares', key: 'qty', r: true, m: !!ST.showAth, get: (p) => qty(p.qty) },   // on a phone, From high takes its place
        { label: 'Price', r: true, m: true, get: priceCell },
        ST.showAth ? { label: 'From high', key: 'ath', r: true, get: (p) => (isNum(p.from_ath) ? h('div', {}, h('div', { text: p.from_ath > -0.005 ? 'at high' : spct(p.from_ath, 1) }), h('div', { class: 'faint', style: 'font-size:12px', text: price(p.ath) })) : '') } : null,
        { label: 'Value', key: 'value', r: true, get: (p) => h('div', {}, h('div', { text: money(p.value, 0) }), CENSOR ? null : h('div', { class: tone(p.day_change), style: 'font-size:12px', text: smoney(p.day_change, 0) })) },
        { label: 'Weight', key: 'weight', r: true, m: true, get: (p) => pct(p.weight) },
        { label: 'Avg cost', r: true, m: true, get: (p) => (p.avg_cost ? price(p.avg_cost) : h('span', { class: 'tag est', text: 'partial' })) },
        { label: 'Total gain', key: 'gain', r: true, get: (p) => (isNum(p.gain) ? (CENSOR ? h('div', { class: tone(p.gain), text: spct(p.gain_pct) }) : h('div', {}, h('div', { class: tone(p.gain), text: smoney(p.gain, 0) }), h('div', { class: tone(p.gain), style: 'font-size:12px', text: spct(p.gain_pct) }))) : h('span', { class: 'faint', text: 'basis unknown' })) },
        m.m2 ? { label: 'vs M2', key: 'm2', r: true, m: true, get: (p) => (isNum(p.vs_m2) ? h('span', { class: tone(p.vs_m2), title: 'Value against what you paid, grown at the pace of M2 since each purchase', text: spct(p.vs_m2, 0) }) : '') } : null,
        { label: 'Income / yr', key: 'income', r: true, m: true, get: (p) => (p.income ? money(p.income, 0) : '') },
      ];
      fill(holder, table(cols.filter(Boolean), rows, { sortKey: key, sortDir: dir, onSort: (k) => { ST.sort = [k, ST.sort[0] === k ? -ST.sort[1] : -1]; render(); }, onRow: (p) => openSymbol(p.symbol) }));
    }
    renderTable();
    const opts = (m.options || {}).open || [];
    if (opts.length) page.append(h('section', { class: 'block' }, card('Open options', table([
      { label: 'Contract', get: (o) => h('span', { class: 'mono', text: `${o.underlying} ${shortDate(o.expiry)} ${o.strike} ${o.right === 'C' ? 'call' : 'put'}` }) },
      { label: 'Position', r: true, get: (o) => (o.qty < 0 ? `short ${-o.qty}` : `long ${o.qty}`) },
      { label: 'Opened for', r: true, get: (o) => money(-o.cost) },
      { label: 'Now', r: true, get: (o) => (o.status === 'open' ? (o.quoted ? money(o.value) : 'no quote') : o.status) },
      { label: 'P/L', r: true, cls: (o) => tone(o.pnl), get: (o) => smoney(o.pnl) },
      { label: 'Stock', r: true, m: true, get: (o) => price(o.underlying_price) }], opts),
      h('p', { class: 'note', text: 'Contracts past their date that your latest export has not closed yet are shown as expired (or probably assigned if they finished in the money). The next upload settles them.' }))));
  }

  async function options(page, m) {
    page.append(h('div', { class: 'head' }, h('div', {}, h('h1', { text: 'Options menu' }), h('p', { class: 'sub', text: 'Covered calls and cash-secured puts for this week, from live chains and your rules. No AI: the rules are listed in Data and settings.' })),
      h('button', { class: 'btn', text: 'Refresh chains', onclick: async (e) => { e.target.disabled = true; e.target.textContent = 'Fetching'; ST.menu = await api('/api/options?refresh=1').catch((er) => ({ error: er.message })); render(); } })));
    const body = h('div', {}, h('div', { class: 'loading', text: 'Reading option chains (a few seconds)' }));
    page.append(body);
    if (!ST.menu) ST.menu = await api('/api/options').catch((e) => ({ error: e.message }));
    const mn = ST.menu;
    if (ST.page !== 'options') return;
    if (mn.error) { fill(body, h('div', { class: 'alert bad', text: mn.error })); return; }
    const cu = mn.cushion;
    const top = h('div', { class: 'grid g4' },
      tile('Expiry', shortDate(mn.expiry), '', `${{ REGULAR: 'market open', PRE: 'pre-market', POST: 'after hours', POSTPOST: 'market closed', CLOSED: 'market closed', PREPRE: 'market closed' }[mn.market] || mn.market}`),
      tile('Target', `${mn.target[0]}-${mn.target[1]}% a week`, '', 'premium as a share of the cash or shares held'),
      tile('Margin cushion', cu ? money(cu.excess, 0) : '-', cu ? tone(cu.excess) : '', cu ? 'estimate: equity minus maintenance' : ''),
      tile(cu && cu.symbol ? `10% ${cu.symbol} move` : '10% move', cu && isNum(cu.ten_pct) ? money(cu.ten_pct, 0) : '-', '', 'change in cushion'));
    const kindTag = (r) => h('span', { class: 'tag ' + (r.premium_kind === 'quote' ? '' : 'est'), text: r.premium_kind });
    const optCols = (withLoss) => [
      { label: 'Ticker', get: (r) => h('span', { class: 'sym', text: r.symbol }) },
      { label: 'Type', get: (r) => h('span', { class: 'pc', text: (r.right === 'C' ? 'Covered call' : 'CSP') + (r.contracts > 1 ? ` x${r.contracts}` : '') }) },
      { label: 'Strike', r: true, get: (r) => price(r.strike) },
      { label: 'Expiry', r: true, m: true, get: (r) => shortDate(r.expiry) },
      { label: 'Premium', r: true, get: (r) => h('div', {}, h('div', { text: price(r.premium) }), kindTag(r)) },
      { label: 'Collateral', r: true, m: true, get: (r) => money(r.collateral * r.contracts, 0) },
      { label: 'Return / week', r: true, get: (r) => pct(r.week, 2) },
      { label: 'From price', r: true, m: true, get: (r) => spct(r.distance, 1) },
      { label: 'Delta', r: true, m: true, get: (r) => (isNum(r.delta) ? r.delta.toFixed(2) : '-') },
      { label: 'Why', cls: () => 'why', get: (r) => r.reason }];
    const sec = (title, rows, sub) => h('section', { class: 'block' }, card(title, sub ? h('p', { class: 'sub', text: sub }) : null, rows.length ? h('div', { class: 'opt-table' }, table(optCols(), rows)) : h('p', { class: 'dim', text: 'Nothing passes the rules this week.' })));
    const longs = mn.long || [];
    const longCalls = longs.filter((x) => x.strategy === 'call'), collars = longs.filter((x) => x.strategy === 'collar');
    fill(body, top,
      ...mn.notes.map((n) => h('div', { class: 'alert', style: 'margin-top:14px' }, icon(WARN_ICON), h('div', { text: n }))),
      sec('Covered calls', mn.calls, 'Underwater lots show a safe strike at or above your basis and a target strike near 0.25-0.30 delta with the loss it would lock in.'),
      sec('Cash-secured puts at support', mn.puts, 'Strikes at or under a support level (swing low, 50-day or 200-day average). Sorted with fresh dips that held first.'),
      longCalls.length ? sec('30-45 day calls', longCalls, 'An alternative to weeklies on your biggest holdings.') : null,
      collars.length ? h('section', { class: 'block' }, card('Hedge a big holding (collar)', h('p', { class: 'sub', text: 'Sell a call and buy a put on the same date: protection below the put, upside capped at the call.' }), table([
        { label: 'Ticker', get: (r) => h('span', { class: 'sym', text: r.symbol }) },
        { label: 'Expiry', r: true, get: (r) => shortDate(r.expiry) },
        { label: 'Sell call', r: true, get: (r) => `${price(r.call_strike)} @ ${price(r.call_premium)}` },
        { label: 'Buy put', r: true, get: (r) => `${price(r.put_strike)} @ ${price(r.put_premium)}` },
        { label: 'Net', r: true, cls: (r) => tone(r.credit), get: (r) => smoney(r.credit, 0) },
        { label: 'Why', cls: () => 'why', get: (r) => r.reason }], collars))) : null,
      h('section', { class: 'block' }, card('Skip this week', h('ul', { class: 'skips' }, mn.skips.map((x) => h('li', {}, h('b', { text: x.symbol }), ` ${x.side}: ${x.reason}`))))),
      h('p', { class: 'note', text: `Built ${new Date(mn.generated).toLocaleString()}. "quote" is the bid/ask middle, "estimate" is Black-Scholes from the chain's IV, "last" is the last trade. Check your broker's mid before selling.` }));
  }

  function income(page, m) {
    const inc = m.income;
    page.append(h('h1', { text: 'Income' }), h('p', { class: 'sub', text: inc.note }));
    const mi = (m.costs || {}).ttm || {};
    page.append(h('div', { class: 'grid g4' },
      tile('Dividends, next 12 months', money(inc.annual, 0), '', `about ${money(inc.monthly, 0)} a month on what you hold now`),
      tile('All income, next 12 months', smoney((inc.next12 || {}).net, 0), tone((inc.next12 || {}).net), `with options at last year\u2019s pace and today\u2019s loan interest`),
      tile('Received, last 12 months', money(inc.received_ttm, 0), '', 'dividends, in-lieu and return of capital'),
      tile('Margin interest, last 12 months', money(mi.margin_interest || 0, 0), mi.margin_interest ? 'down' : '', 'what the loan cost')));
    const yrs = inc.by_year || [], n12 = inc.next12 || {};
    const ybox = h('div', { class: 'chart' });
    const yrows = yrs.filter((b) => b.dividends || b.options || b.interest || b.kind === 'projected');
    page.append(h('section', { class: 'block grid g2' },
      card('Income by year', h('p', { class: 'dim', style: 'margin:0 0 10px;font-size:13.5px', text: `Past years are what came in. ${new Date().getFullYear()} is so far plus the rest of the year at today\u2019s pace; later years are projected from today\u2019s holdings with each one\u2019s own dividend growth, options at the last 12 months\u2019 pace, and interest on today\u2019s loan${n12.margin_rate ? ` (about ${pct(n12.margin_rate, 1)} a year)` : ''}.` }), ybox),
      card('By year, in numbers', table([
        { label: 'Year', get: (b) => h('span', {}, String(b.year), b.kind === 'projected' ? h('span', { class: 'tag est', style: 'margin-left:6px', text: 'projected' }) : b.kind === 'so far' ? h('span', { class: 'tag', style: 'margin-left:6px', text: 'so far' }) : null) },
        { label: 'Dividends', r: true, get: (b) => money(b.dividends + (b.rest_dividends || 0), 0) },
        { label: 'Options', r: true, cls: (b) => tone(b.options), get: (b) => smoney(b.options + (b.rest_options || 0), 0) },
        { label: 'Interest', r: true, get: (b) => money(b.interest + (b.rest_interest || 0), 0) },
        { label: 'Other', r: true, m: true, get: (b) => money(b.other || 0, 0) },
        { label: 'Net', r: true, cls: (b) => tone(b.net), get: (b) => smoney(b.net + (b.rest_dividends || 0) + (b.rest_options || 0) + (b.rest_interest || 0), 0) }], yrows))));
    requestAnimationFrame(() => barChart(ybox, { labels: yrows.map((b) => String(b.year)), values: yrows.map((b) => b.net + (b.rest_dividends || 0) + (b.rest_options || 0) + (b.rest_interest || 0)),
      colorFn: (v, i) => (yrows[i].kind === 'projected' ? 'rgba(57,135,229,0.45)' : v >= 0 ? 'var(--s-blue)' : 'var(--down)'), name: 'Net income', fmt: smoney, toneTip: true, height: 220,
      tipTitle: (i) => `${yrows[i].year}${yrows[i].kind === 'projected' ? ' (projected)' : ''}`,
      extraTip: (i) => [{ label: 'Dividends', value: money(yrows[i].dividends + (yrows[i].rest_dividends || 0), 0) }, { label: 'Options', value: smoney(yrows[i].options + (yrows[i].rest_options || 0), 0) }, { label: 'Interest', value: money(yrows[i].interest + (yrows[i].rest_interest || 0), 0) }] }));
    const cal = h('div', { class: 'chart' });
    page.append(h('section', { class: 'block' }, card('Projected dividends by month', cal)));
    requestAnimationFrame(() => barChart(cal, { months: inc.calendar.map((c) => c.month), values: inc.calendar.map((c) => c.amount), color: 'var(--s-blue)', name: 'Projected' }));
    const rec = inc.received_by_month || [];
    const divv = rec.map((r) => (r.dividend || 0) + (r.dividend_in_lieu || 0) + (r.return_of_capital || 0));
    const optv = rec.map((r) => r.options || 0);
    const intv = rec.map((r) => r.margin_interest || 0);
    const c1 = h('div', { class: 'chart' }), c2 = h('div', { class: 'chart' }), c3 = h('div', { class: 'chart' });
    page.append(h('section', { class: 'block grid g3 stack' },
      card('Dividends received', c1), card('Options profit and loss', c2), card('Margin interest paid', c3)));
    requestAnimationFrame(() => {
      barChart(c1, { months: rec.map((r) => r.month), values: divv, color: 'var(--s-blue)', name: 'Received', height: 180,
        extraTip: (i) => [{ label: 'paid while lent out', value: money(rec[i].dividend_in_lieu || 0) }, { label: 'return of capital', value: money(rec[i].return_of_capital || 0) }] });
      barChart(c2, { months: rec.map((r) => r.month), values: optv, colorFn: (v) => (v >= 0 ? 'var(--up)' : 'var(--down)'), name: 'Realized', fmt: smoney, toneTip: true, height: 180 });
      barChart(c3, { months: rec.map((r) => r.month), values: intv.map((v) => -v), color: 'var(--down)', name: 'Interest', height: 180 });
    });
    const up = inc.upcoming || [];
    const bySym = Object.values(inc.by_symbol).sort((a, b) => b.annual - a.annual);
    page.append(h('section', { class: 'block grid g2' },
      card('Coming payments', up.length ? table([
        { label: 'Pay date', get: (u) => shortDate(u.pay) },
        { label: 'Holding', get: (u) => h('span', { class: 'sym', text: u.symbol }) },
        { label: 'Ex-date', r: true, m: true, get: (u) => shortDate(u.ex) },
        { label: 'Amount', r: true, get: (u) => money(u.amount) },
        { label: '', get: (u) => h('span', { class: 'tag ' + (u.declared ? 'good' : 'est'), text: u.declared ? 'declared' : 'estimate' }) }], up.slice(0, 25)) : h('p', { class: 'dim', text: 'No payments in the next two months.' })),
      card('By holding', table([
        { label: 'Holding', get: (x) => h('span', { class: 'sym', text: x.symbol }) },
        { label: 'Per year', r: true, get: (x) => money(x.annual) },
        { label: 'Yield', r: true, get: (x) => pct(x.yield, 2) },
        { label: 'Pays', r: true, m: true, get: (x) => ({ 1: 'yearly', 2: 'twice a year', 4: 'quarterly', 12: 'monthly', 52: 'weekly' }[Math.round(x.payments_per_year)] || `${x.payments_per_year}x a year`) },
        { label: 'Last', r: true, m: true, get: (x) => (x.last_amount ? `${price(x.last_amount, 4)} ${shortDate(x.last_ex)}` : '') }], bySym, { onRow: (x) => openSymbol(x.symbol) }))));
    page.append(h('section', { class: 'block' }, card('Income received (statement)', table([
      { label: 'Date', get: (r) => shortDate(r.date) },
      { label: 'Holding', get: (r) => r.symbol || '' },
      { label: 'Type', get: (r) => ({ dividend: 'Dividend', dividend_in_lieu: 'Paid while lent out', return_of_capital: 'Return of capital', interest: 'Interest', bonus: 'Bonus', stock_lending: 'Stock lending', cash_in_lieu: 'Cash in lieu' }[r.sub] || r.sub) },
      { label: 'Shares', r: true, m: true, get: (r) => (r.shares ? qty(r.shares) : '') },
      { label: 'Amount', r: true, get: (r) => money(r.amount) }], inc.received_rows.slice(0, 80)))));
  }

  function analysis(page, m) {
    const p = m.performance, g = m.grades, d = m.diversification, o = m.options;
    page.append(h('h1', { text: 'Analysis' }), h('p', { class: 'sub', text: p.ready ? p.note : 'Needs history first.' }));
    // report card
    const items = Object.entries(g.items || {});
    page.append(h('div', { class: 'report' },
      h('div', { class: 'card gpa' }, h('div', { class: 'label', text: 'Overall' }), h('div', { class: 'letter g-' + (g.overall || 'C'), text: g.overall || '-' }), h('div', { class: 'dim', style: 'font-size:13px;margin-top:6px', text: isNum(g.gpa) ? `${g.gpa.toFixed(1)} of 4.0` : '' })),
      h('div', { class: 'grades' }, items.map(([k, x]) => h('div', { class: 'card grade-card' },
        h('div', { class: 'top' }, h('span', { class: 'label', text: x.label }), h('span', { class: 'letter s g-' + x.grade, text: x.grade })),
        h('div', { style: 'font-size:14px;margin-top:6px', text: x.text }), h('div', { class: 'why', text: m.grade_rules[k] }))))));
    if (p.ready) {
      const voo = p.bench.VOO || {}, q = p.bench.QQQM || {}, m2b = p.bench.M2 || {};
      const over = (a) => (isNum(a) && isNum(m2b.annual) ? h('span', { class: tone(a - m2b.annual), text: spct(a - m2b.annual) }) : '-');
      const rows = [
        ['Total return (time-weighted)', spct(p.twr), spct(voo.twr), spct(q.twr), spct(m2b.twr)],
        ['Per year', spct(p.annual), spct(voo.annual), spct(q.annual), spct(m2b.annual)],
        ...(p.bench.M2 ? [['Per year, above M2', over(p.annual), over(voo.annual), over(q.annual), '']] : []),
        ['Ending value with the same deposits', money(p.final, 0), money(voo.final, 0), money(q.final, 0), money(m2b.final, 0)],
        ['Sharpe ratio', fx(p.stats.sharpe), fx(voo.stats && voo.stats.sharpe), fx(q.stats && q.stats.sharpe)],
        ['Sortino ratio', fx(p.stats.sortino), fx(voo.stats && voo.stats.sortino), fx(q.stats && q.stats.sortino)],
        ['Volatility per year', pct(p.stats.vol), pct(voo.stats && voo.stats.vol), pct(q.stats && q.stats.vol)],
        ['Largest drop from a high', pct(p.stats.max_dd), pct(voo.stats && voo.stats.max_dd), pct(q.stats && q.stats.max_dd)],
        ['Best day', spct(p.stats.best_day, 2), spct(voo.stats && voo.stats.best_day, 2), spct(q.stats && q.stats.best_day, 2)],
        ['Worst day', spct(p.stats.worst_day, 2), spct(voo.stats && voo.stats.worst_day, 2), spct(q.stats && q.stats.worst_day, 2)]];
      function fx(v) { return isNum(v) ? v.toFixed(2) : '-'; }
      const chart = h('div', { class: 'card' });
      page.append(h('section', { class: 'block grid g2' },
        card(`You vs VOO, QQQM and M2 since ${shortDate(p.start)}`, table([{ label: '', get: (r) => r[0] }, { label: 'You', r: true, get: (r) => r[1] }, { label: 'VOO', r: true, get: (r) => r[2] }, { label: 'QQQM', r: true, get: (r) => r[3] },
          ...(p.bench.M2 ? [{ label: 'M2', r: true, get: (r) => (r[4] == null ? '' : r[4]) }] : [])], rows),
          h('div', { style: 'margin-top:12px' },
            h('div', { class: 'kv' }, h('span', { text: 'Money-weighted return (your timing)' }), h('span', { class: tone(p.irr), text: spct(p.irr) })),
            h('div', { class: 'kv' }, h('span', { text: 'Beta to VOO' }), h('span', { text: isNum(p.beta) ? p.beta.toFixed(2) : '-' })),
            h('div', { class: 'kv' }, h('span', { text: 'Correlation to VOO' }), h('span', { text: isNum(p.correlation) ? p.correlation.toFixed(2) : '-' })),
            h('div', { class: 'kv' }, h('span', { text: 'Alpha per year' }), h('span', { class: tone(p.alpha), text: spct(p.alpha) })),
            h('div', { class: 'kv' }, h('span', { text: 'Largest drop' }), h('span', { text: p.stats.dd_peak ? `${shortDate(p.stats.dd_peak)} to ${shortDate(p.stats.dd_trough)}` : '-' })))),
        chart));
      perfChart(chart, m, 320);
    }
    if (d.ready) {
      const links = d.links || {};
      page.append(h('section', { class: 'block grid g3 stack' },
        card('Sectors (funds looked through)', hbars(d.sectors.slice(0, 14), pct, sectorColor)),
        card('Asset types', hbars(d.classes, pct, (x) => TYPE_COLORS[x.name] || 'var(--s-ref)'),
          h('div', { style: 'margin-top:14px' },
            h('div', { class: 'kv' }, h('span', { text: 'Effective number of holdings' }), h('span', { text: d.effective_n.toFixed(1) })),
            h('div', { class: 'kv' }, h('span', { text: 'Holdings' }), h('span', { text: d.count })),
            h('div', { class: 'kv' }, h('span', { text: 'Largest holding' }), h('span', { text: pct(d.top1) })),
            h('div', { class: 'kv' }, h('span', { text: 'Top 5 / top 10' }), h('span', { text: `${pct(d.top5, 0)} / ${pct(d.top10, 0)}` })),
            h('div', { class: 'kv' }, h('span', { text: 'Average correlation between holdings' }), h('span', { text: isNum(d.avg_corr) ? d.avg_corr.toFixed(2) : '-' })),
            h('div', { class: 'kv' }, h('span', { text: 'Diversification ratio' }), h('span', { text: isNum(d.div_ratio) ? d.div_ratio.toFixed(2) : '-' })))),
        card('What moves together', ...Object.entries(links).map(([k, v]) => h('div', { style: 'margin-bottom:14px' },
          h('div', { class: 'kv' }, h('span', { text: `Moves with ${k} (correlation 0.6+)` }), h('span', { text: pct(v.weight, 0) })),
          h('div', { class: 'dim', style: 'font-size:13px', text: v.symbols.slice(0, 10).map((x) => x.symbol).join(', ') || 'none' }))),
          h('h3', { text: 'Most alike pairs, last 6 months', style: 'margin-top:8px' }),
          h('div', {}, (d.most_alike || []).map((x) => h('div', { class: 'kv' }, h('span', { class: 'mono', text: `${x.a} + ${x.b}` }), h('span', { text: x.corr.toFixed(2) })))))));
    }
    // options + realized
    const byU = o.by_underlying || [];
    const c = h('div', { class: 'chart' });
    page.append(h('section', { class: 'block grid g2' },
      card('Options record', h('div', { class: 'grid g3' }, tile('Realized', smoney(o.realized, 0), tone(o.realized)), tile('Win rate', pct(o.win_rate, 0)), tile('Contracts closed', String(o.count))),
        h('div', { style: 'margin-top:12px' },
          h('div', { class: 'kv' }, h('span', { text: `Selling premium (${o.short_count})` }), h('span', { class: tone(o.short_pnl), text: smoney(o.short_pnl, 0) })),
          h('div', { class: 'kv' }, h('span', { text: `Buying options (${o.long_count})` }), h('span', { class: tone(o.long_pnl), text: smoney(o.long_pnl, 0) }))),
        h('div', { class: 'chart', style: 'margin-top:12px' }, c)),
      card('Options by stock', table([{ label: 'Stock', get: (x) => h('span', { class: 'sym', text: x.symbol }) }, { label: 'Realized', r: true, cls: (x) => tone(x.pnl), get: (x) => smoney(x.pnl, 0) }],
        byU.slice(0, 8).concat(byU.length > 12 ? byU.slice(-4) : [])))));
    requestAnimationFrame(() => barChart(c, { months: o.by_month.map((r) => r.month), values: o.by_month.map((r) => r.pnl), colorFn: (v) => (v >= 0 ? 'var(--up)' : 'var(--down)'), fmt: smoney, toneTip: true, name: 'Realized', height: 170 }));
  }

  async function doomSection(box) {
    const d = await getDoom(false).catch((e) => ({ error: e.message }));
    if (!box.isConnected) return;
    if (!d || d.error || !d.now) { fill(box, h('div', { class: 'alert', text: (d && d.error) || 'No doom score data yet.' })); return; }
    const now = d.now, marks = d.marks, comps = d.components;
    // 1. today, the scale, the groups, today against the remembered bottoms
    const top = h('div', { class: 'grid g2 stack' },
      h('div', { class: 'card' }, h('h2', { text: 'Doom score' }), doomHead(d, true), doomGauge(d), doomGroups(d),
        h('p', { class: 'dim', style: 'font-size:13.5px;margin:14px 0 0', text: doomLine(d) }), doomButton(d)),
      h('div', { class: 'card' }, h('h2', { text: 'Today against past bottoms' }), doomCompare(d),
        h('div', { class: 'dnotes' }, marks.map((mk) => h('p', {}, h('b', { text: `${mk.name}, ${longDate(mk.date)}. ` }), mk.note || '')))));
    // 2. history
    const range = ST.doomRange || '5Y';
    const chips = h('div', { class: 'chips' }, ['1Y', '2Y', '5Y', 'All'].map((k) => h('button', { class: 'chip' + (range === k ? ' on' : ''), text: k === 'All' ? 'Since 2010' : k, onclick: () => { ST.doomRange = k; render(); } })));
    const hs = d.history, back = { '1Y': 366, '2Y': 731, '5Y': 1827 }[range];
    let i0 = 0;
    if (back) { const s0 = new Date(Date.now() - back * 864e5).toISOString().slice(0, 10); i0 = Math.max(0, hs.dates.findIndex((x) => x >= s0)); }
    const chartBox = h('div', { class: 'chart' });
    const histCard = h('div', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Doom score over time', style: 'margin:0' }), chips),
      h('div', { class: 'legend' }, h('span', {}, mkKey('var(--s-doom)'), 'Doom score'), h('span', {}, mkKey('var(--gold)', true), 'Past bottoms'), h('span', {}, h('i', { class: 'dash' }), 'Today’s level'), h('span', {}, mkKey('rgba(229,72,77,0.16)', true), 'Panic (80 and up)')),
      chartBox);
    // 3. every reading, today against each bottom
    const fmtRaw = (c, snap) => {
      const x = snap.components[c.key]; if (!x || !isNum(x.raw)) return '-';
      if (c.unit === 'index') return x.raw.toFixed(0);
      if (c.unit === 'level') return x.raw.toFixed(1);
      if (c.unit === 'ratio') return x.raw.toFixed(2);
      if (c.unit === 'pctpt') return x.raw.toFixed(2) + '%';
      if (c.unit === 'pct') return spct(x.raw, 1);
      const pe = snap.pe[c.key === 'spx_pe' ? 'spx' : 'ndx'];
      return `${isNum(pe) ? pe.toFixed(1) : '-'} (${spct(x.raw - 1, 0)})`;
    };
    const cell = (raw, score) => h('div', { class: 'dcell' }, h('span', { text: raw }), isNum(score) ? (() => { const b = h('b', { class: 'mono', text: score.toFixed(0) }); b.style.background = doomColor(score); return b; })() : null);
    const snaps = [now, ...marks];
    const rows = [{ kind: 'total', name: 'Doom score' }];
    for (const gr of d.groups) {
      rows.push({ kind: 'group', key: gr.key, name: `${gr.name} (${Math.round(gr.weight * 100)}%)` });
      comps.filter((c) => c.group === gr.key).forEach((c) => rows.push({ kind: 'comp', c, name: c.name }));
    }
    rows.push({ kind: 'you', name: 'Your portfolio from its high (not scored)' });
    const cols = [{ label: 'Reading', get: (r) => h('span', { class: r.kind === 'comp' ? 'dim' : '', style: r.kind === 'comp' ? 'padding-left:12px' : 'font-weight:600', text: r.name }) },
      ...snaps.map((sn, k) => ({ label: k === 0 ? 'Today' : `${sn.name}, ${shortDate(sn.date)}`, r: true, get: (r) => {
        if (r.kind === 'total') return cell('', sn.score);
        if (r.kind === 'group') return cell('', sn.groups[r.key]);
        if (r.kind === 'you') return isNum(sn.you_dd) ? spct(sn.you_dd, 1) : '-';
        return cell(fmtRaw(r.c, sn), (sn.components[r.c.key] || {}).score);
      } }))];
    const cmpCard = h('div', { class: 'card' }, h('h2', { text: 'Every reading, today against past bottoms' }),
      h('p', { class: 'dim', style: 'margin:0 0 10px;font-size:13.5px', text: 'Each cell is the reading and its 0 to 100 fear score. P/E rows show the estimated P/E and how far it sits from its own 10-year median.' }),
      (() => { const t = table(cols, rows); t.classList.add('dtable'); return t; })());
    // 4. what happened after readings like today's
    const b = d.base, eps = d.episodes || [];
    const epCard = h('div', { class: 'card' }, h('h2', { text: `After the score first reached ${Math.round(now.score)}` }),
      h('p', { class: 'dim', style: 'margin:0 0 10px;font-size:13.5px', text: b ? `${b.count} times since 2010 (counting a new time only after 40 trading days below it). Before turning, the S&P 500 fell a median ${pct(-b.median_further_drop, 1)} more, worst ${pct(-b.worst_further_drop, 1)}, and the low came a median ${Math.round(b.median_days_to_low)} trading days later. Three months on it was a median ${spct(b.median_3m, 1)}; a year on, higher ${b.up_12m} of ${b.n_12m} times (median ${spct(b.median_12m, 1)}). Past results only, not a forecast.` : 'Not enough history at this level.' }),
      table([
        { label: 'First reached', get: (e) => shortDate(e.date) + (e.complete ? '' : ' (still going)') },
        { label: 'What it was', get: (e) => h('span', { class: 'dim', text: e.name || '' }) },
        { label: 'Score then', r: true, get: (e) => e.score.toFixed(0) },
        { label: 'Highest in 6 months', r: true, get: (e) => `${e.peak_score.toFixed(0)} on ${shortDate(e.peak_date)}` },
        { label: 'S&P 500 fell further', r: true, cls: (e) => (e.further_drop < -0.0005 ? 'down' : ''), get: (e) => (e.further_drop < -0.0005 ? spct(e.further_drop, 1) : 'none') },
        { label: 'Days to low', r: true, m: true, get: (e) => String(e.days_to_low) },
        { label: '3 months later', r: true, cls: (e) => tone(e.fwd['63']), get: (e) => (isNum(e.fwd['63']) ? spct(e.fwd['63'], 1) : '-') },
        { label: '1 year later', r: true, cls: (e) => tone(e.fwd['252']), get: (e) => (isNum(e.fwd['252']) ? spct(e.fwd['252'], 1) : '-') }], [...eps].reverse()));
    const pk = d.peaks || [];
    const pkCard = h('div', { class: 'card' }, h('h2', { text: 'Every panic since 2010' }),
      h('p', { class: 'dim', style: 'margin:0 0 10px;font-size:13.5px', text: 'The most fearful day of each stretch at 80 or above, what was going on, and what the S&P 500 did after it.' }),
      table([
        { label: 'Day', get: (x) => shortDate(x.date) },
        { label: 'What it was', get: (x) => h('span', { class: 'dim', text: x.name || '' }) },
        { label: 'Score', r: true, get: (x) => x.score.toFixed(0) },
        { label: '3 months later', r: true, cls: (x) => tone(x.fwd['63']), get: (x) => (isNum(x.fwd['63']) ? spct(x.fwd['63'], 1) : '-') },
        { label: '1 year later', r: true, cls: (x) => tone(x.fwd['252']), get: (x) => (isNum(x.fwd['252']) ? spct(x.fwd['252'], 1) : 'not yet') }], [...pk].reverse()));
    const how = h('div', { class: 'card' }, h('h2', { text: 'How the score works' }),
      h('p', { class: 'dim', style: 'font-size:13.5px;margin:0 0 8px', text: 'Thirteen readings, each turned into 0 to 100 where 100 is the most fear. CNN Fear & Greed and Crypto Fear & Greed are already 0 to 100, so they are flipped. Every other reading is ranked against every trading day since 2010: a VIX higher than on 89% of those days scores 89. Readings are averaged inside their group, and the groups are weighted Fear 40%, Price damage 30%, Valuation 15%, Crypto 15%. Before 2021 CNN’s readings do not exist, so those days use the rest.' }),
      h('p', { class: 'faint', style: 'font-size:12.5px;margin:0', text: 'Sources: Yahoo (VIX, 3-month VIX, S&P 500, Nasdaq-100, Bitcoin), CNN Fear & Greed with its put/call and junk bond readings, alternative.me Crypto Fear & Greed, multpl.com and worldperatio.com P/E. Refreshed every 30 minutes while the market is open.' }));
    fill(box, top, h('div', { class: 'block' }, histCard), h('div', { class: 'block' }, cmpCard), h('div', { class: 'block' }, epCard), h('div', { class: 'block grid g2 stack' }, pkCard, how));
    const dates = hs.dates.slice(i0), vals = hs.score.slice(i0), spx = hs.spx.slice(i0);
    requestAnimationFrame(() => lineChart(chartBox, { dates, series: [{ name: 'Doom score', color: 'var(--s-doom)', values: vals }], yrange: [0, 100],
      yfmt: (v) => v.toFixed(0), fmt: (v) => (isNum(v) ? v.toFixed(0) : '-'), height: 260, label: 'Doom score over time',
      bands: [{ lo: 80, hi: 100, fill: 'rgba(229,72,77,0.10)' }], refs: [{ y: now.score }],
      titleFmt: (dd) => longDate(dd), extraTip: (i) => [{ label: 'S&P 500', value: isNum(spx[i]) ? spx[i].toLocaleString('en-US', { maximumFractionDigits: 0 }) : '-' }],
      markers: marks.filter((mk) => mk.date >= dates[0]).map((mk) => ({ date: mk.date, value: mk.score, color: 'var(--gold)', label: mk.name, text: mk.score.toFixed(0) })) }));
  }

  // ---- Assets dashboard: Glory Days Worth (analytics._glory) and the assets chart ---------------------------------
  function assetsPage(page, m) {
    page.classList.add('wide');
    const g = m.glory, hs = m.history;
    page.append(h('div', { class: 'head' }, h('div', {}, h('h1', { text: 'Assets dashboard' }),
      h('p', { class: 'sub', text: 'What you own before the loan, and what it would be worth back at the prices of your best day.' }))));
    if (!g || !hs.glory) { page.append(h('div', { class: 'card' }, h('p', { class: 'dim', text: 'Not enough history yet.' }))); return; }
    const hd = g.date, day = new Date(hd + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }), k = hs.dates.indexOf(hd), dates = hs.dates.slice(k), n = dates.length;
    const line = hs.glory.slice(k), nw = hs.value.slice(k), eff = (hs.glory_effect || []).slice(k), flows = hs.flow.slice(k);
    const byDay = tradesByDay(m);
    const big = (v) => Math.abs(v) >= Math.max(50, Math.abs(g.now) * 0.0005);
    const colors = eff.map((v) => (!isNum(v) || !big(v) ? 'var(--bar-flat)' : v > 0 ? 'var(--bar-up)' : 'var(--bar-down)'));
    const color = { g: 'var(--s-you)', n: 'var(--s-blue)' };
    const series = [{ name: 'Glory Days Worth', color: color.g, values: line }, { name: 'Net worth', color: color.n, values: nw }];
    const box = h('div', { class: 'chart' });
    const cell = (label, v, sub, cls) => h('div', { class: 'acell' }, h('div', { class: 'faint', text: label }), h('div', { class: 'mono ' + (cls || ''), text: (cls === undefined ? money : smoney)(v, 0) }), h('div', { class: 'faint small', text: sub }));
    const rows = g.rows.filter((r) => isNum(r.change));
    const adds = rows.filter((r) => r.change >= 1).sort((a, b) => b.change - a.change).slice(0, 5);
    const cuts = rows.filter((r) => r.change <= -1).sort((a, b) => a.change - b.change).slice(0, 5);
    const sym = (s) => s.replace('-USD', '');
    const dq = (r) => { const d = r.qty - r.qty_high; return (d > 0 ? '+' : '−') + qty(Math.abs(d)) + (r.symbol.endsWith('-USD') ? '' : ' sh'); };
    const moveList = (title, list) => h('div', { class: 'gmoves' }, h('div', { class: 'faint', style: 'font-size:12.5px;margin-bottom:4px', text: title }),
      list.length ? list.map((r) => h('div', { class: 'kv' }, h('span', {}, h('b', { class: 'sym', text: sym(r.symbol) }), h('span', { class: 'faint', text: ' ' + dq(r) + (r.qty < 1e-9 ? ', all of it' : '') })),
        h('span', { class: 'mono ' + tone(r.change), text: smoney(r.change, 0) }))) : h('div', { class: 'faint', style: 'font-size:13px', text: 'None' }));
    const glory = h('div', { class: 'card panel' },
      h('div', { class: 'panel-head' }, h('h2', { text: 'Glory Days Worth' }), h('span', { class: 'tag', text: `${day} prices` })),
      h('div', { class: 'gnum mono', text: money(g.now, 0) }),
      h('div', { class: 'gline' }, h('span', { class: 'mono ' + tone(g.trading), text: smoney(g.trading, 0) }), ` from buying and selling since your ${day} high, at that day’s prices`),
      h('p', { class: 'dim', style: 'font-size:13.5px;margin:4px 0 10px', text: `If every holding went back to its ${day} price, net worth would be ${money(g.now, 0)}, ${money(g.now - g.net_worth, 0)} more than today’s ${money(g.net_worth, 0)}.` }),
      h('div', { class: 'legend' }, series.map((sr) => h('span', {}, mkKey(sr.color), sr.name)), h('span', {}, h('i', { class: 'dash' }), `Your high plus money added since, ${money(g.start, 0)}`)),
      box,
      h('div', { class: 'legend', style: 'margin:6px 0 2px' }, h('span', { class: 'faint', text: 'Each day’s change, bars under the chart:' }),
        h('span', {}, mkKey('var(--bar-up)'), 'bought or added'), h('span', {}, mkKey('var(--bar-down)'), 'sold')),
      h('p', { class: 'faint', style: 'font-size:12.5px;margin:0 0 12px', text: `Your holdings each day at the price they traded for on ${longDate(hd)}, the day of your highest net worth (${money(g.high, 0)}), plus that day’s cash and loan. Price moves don’t change it. Buying adds the gain the new shares would make on the way back to that price; selling takes it away. Money and shares added or taken out count as if they were there on ${day}, so the line moves only when you buy or sell, collect dividends or pay interest.` + (g.missing.length ? ` ${g.missing.join(', ')} had no price on ${day} and count${g.missing.length === 1 ? 's' : ''} at today’s price.` : '') + ' Today’s trades come from the broker’s share counts at live prices until the activity rows arrive.' }),
      h('div', { class: 'abreak' },
        cell(`Net worth on ${day}`, g.high, 'your high'),
        cell('Money in or out since', g.money_in, 'shares and coins at that day’s prices', ''),
        cell('Buying and selling', g.trading, 'with dividends, interest and options', tone(g.trading)),
        cell('Glory Days Worth', g.now, 'now')),
      h('div', { class: 'gmovesrow' }, moveList(`Added since ${day}, at that day’s prices`, adds), moveList(`Sold since ${day}, at that day’s prices`, cuts)));
    requestAnimationFrame(() => lineChart(box, { dates, series, fmt: (v) => money(v, 0), height: (tier() >= 2 ? 300 : 230) + 70, label: 'Glory Days Worth since the high',
      refs: [{ y: g.start, color: 'var(--s-ref)' }],
      strip: { values: eff.map((v, i) => (i === 0 ? null : v)), colors, height: 58, zero: true, fmt: (v) => smoney(v, 0) },
      sync: { group: 'home', id: 'glory', keys: dayKeys(dates), label: (i) => ['Glory Days Worth', money(line[i], 0)] },
      extraTip: (i) => {
        const out = [];
        if (i > 0 && isNum(eff[i]) && Math.abs(eff[i]) >= 1) out.push({ label: 'Buying and selling that day', value: smoney(eff[i], 0), cls: big(eff[i]) ? tone(eff[i]) : '' });
        if (i > 0 && Math.abs(flows[i]) >= 1) out.push({ label: 'Money in or out', value: smoney(flows[i], 0) });
        const t = byDay[dates[i]];
        if (t && i > 0) {
          if (topTrades(t.bought)) out.push({ label: 'Bought', value: topTrades(t.bought) });
          if (topTrades(t.sold)) out.push({ label: 'Sold', value: topTrades(t.sold) });
        }
        return out;
      } }));

    // every holding now, and the ones sold since, at the high-day price
    const sortKey = ST.gsort || ['worth', -1];
    const val = (r, key) => (key === 'symbol' ? r.symbol : key === 'back' ? (r.star && r.price ? r.star / r.price - 1 : null) : r[key]);
    const sorted = [...g.rows].sort((a, b) => {
      const x = val(a, sortKey[0]), y = val(b, sortKey[0]);
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sortKey[1];
    });
    const shown = ST.gall ? sorted : sorted.slice(0, 25);
    const cols = [
      { label: 'Holding', key: 'symbol', get: (r) => h('span', {}, h('b', { class: 'sym', text: sym(r.symbol) }), r.qty < 1e-9 ? h('span', { class: 'faint', text: ' sold' }) : null) },
      { label: 'Now', key: 'qty', r: true, get: (r) => qty(r.qty) },
      { label: `Since ${day}`, key: 'change', r: true, m: true, get: (r) => (Math.abs(r.qty - r.qty_high) < 1e-6 ? '' : dq(r)) },
      { label: `${day} price`, key: 'star', r: true, get: (r) => (r.star ? price(r.star, 2) : 'none') },
      { label: 'Price now', key: 'price', r: true, m: true, get: (r) => (r.price ? price(r.price, 2) : '-') },
      { label: 'To get back', key: 'back', r: true, m: true, get: (r) => (r.star && r.price ? spct(r.star / r.price - 1, 0) : ''), cls: (r) => (r.star && r.price ? tone(r.star / r.price - 1) : '') },
      { label: `Worth at ${day} price`, key: 'worth', r: true, get: (r) => (isNum(r.worth) ? money(r.worth, 0) : '') },
      { label: 'Adds on the way back', key: 'upside', r: true, m: true, get: (r) => (isNum(r.upside) ? smoney(r.upside, 0) : ''), cls: (r) => (isNum(r.upside) ? tone(r.upside) : '') }];
    const holdCard = h('div', { class: 'card panel' }, h('div', { class: 'panel-head' }, h('h2', { text: `Your holdings at ${day} prices` }), h('span', { class: 'faint', style: 'font-size:12.5px', text: `${g.rows.filter((r) => r.qty > 1e-9).length} held, ${g.rows.filter((r) => r.qty <= 1e-9).length} sold since` })),
      table(cols, shown, { sortKey: sortKey[0], sortDir: sortKey[1], onSort: (key) => { ST.gsort = [key, sortKey[0] === key ? -sortKey[1] : -1]; render(); }, onRow: (r) => openSymbol(r.symbol) }),
      sorted.length > 25 ? h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn small', text: ST.gall ? 'Show the top 25' : `Show all ${sorted.length}`, onclick: () => { ST.gall = !ST.gall; render(); } })) : null);
    page.append(h('div', { class: 'adash' }, glory, assetsCard(m, { page: true })), h('div', { style: 'margin-top:14px' }, holdCard));
  }

  // ---- Customize: a chat where Claude changes the program (fin/agent.py) -------------------------------------------
  const ci = (d) => { const v = s('svg', { viewBox: '0 0 24 24', class: 'ci', 'aria-hidden': 'true' }); v.append(s('path', { d })); return v; };
  const CI = { clip: 'M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.5 3.5 0 0 1 5 5l-8.5 8.4a2 2 0 0 1-2.8-2.8l7.8-7.8', copy: 'M9 9h11v12H9zM5 15V4h10',
    check: 'M5 12.5l4.5 4.5L19 7.5', x: 'M6 6l12 12M18 6L6 18', up: 'M12 19V5M6 11l6-6 6 6', stop: 'M7 7h10v10H7z', down: 'M12 5v14M6 13l6 6 6-6', plus: 'M12 5v14M5 12h14',
    file: 'M7 3h7l4 4v14H7zM14 3v4h4', retry: 'M4 12a8 8 0 1 0 2.6-5.9M4 4v4.5h4.5', globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.6 2.4 3.9 5.4 3.9 9s-1.3 6.6-3.9 9c-2.6-2.4-3.9-5.4-3.9-9s1.3-6.6 3.9-9z',
    term: 'M4 5h16v14H4zM7.5 9.5l3 2.5-3 2.5M12.5 15h4', tune: 'M4 7h9M17 7h3M4 17h3M11 17h9M13 4.5v5M7 14.5v5' };
  // Copying works on the plain http address too (the clipboard API needs https; the old command does not)
  async function copyText(text, btn) {
    let ok = false;
    try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; } } catch (e) { /* try the old way */ }
    if (!ok) {
      const ta = h('textarea', { class: 'offscreen', 'aria-hidden': 'true', tabindex: '-1' });
      ta.value = text; document.body.append(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
    }
    if (btn && !btn._busy) {
      const old = [...btn.childNodes]; btn._busy = true;
      fill(btn, ci(ok ? CI.check : CI.x), btn.dataset.label ? (ok ? 'Copied' : 'Could not copy') : null);
      setTimeout(() => { fill(btn, ...old); btn._busy = false; }, 1400);
    }
    return ok;
  }
  const copyBtn = (getText, label) => {
    const b = h('button', { class: 'cpy', type: 'button', title: 'Copy', 'aria-label': 'Copy', 'data-label': label ? '1' : null }, ci(CI.copy), label || null);
    b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); copyText(getText(), b); });
    return b;
  };
  const diffView = (lines) => h('pre', { class: 'diff' }, lines.map((l) => h('div', { class: l[0] === '+' ? 'add' : l[0] === '-' ? 'del' : l.startsWith('@@') ? 'hunk' : '', text: l || ' ' })));
  const codeBox = (text, lang) => h('div', { class: 'codebox' }, h('div', { class: 'codehead' }, h('span', { text: lang || 'text' }), copyBtn(() => text, 'Copy')),
    lang === 'diff' ? diffView(text.split('\n')) : h('pre', {}, h('code', { text })));
  // Markdown as nodes (never as HTML): headings, lists, tables, quotes, code boxes with a copy button, links, bold, italic
  function mdInline(text, into) {
    const re = /(`+)(.+?)\1|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>]*[^\s<>.,;:!?)\]'"*])|\*\*(.+?)\*\*|~~(.+?)~~|(^|[^\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])|(^|[^\w_])_(?!\s)([^_\n]+?)_(?![\w_])/g;
    let last = 0, m;
    const wrap = (tag, inner) => { const el = h(tag); mdInline(inner, el); into.append(el); };
    while ((m = re.exec(text))) {
      const pre = m[8] != null ? m[8] : m[10] != null ? m[10] : '';
      if (m.index + pre.length > last) into.append(text.slice(last, m.index + pre.length));
      if (m[1]) into.append(h('code', { text: m[2] }));
      else if (m[3]) { const a = h('a', { href: m[4], target: '_blank', rel: 'noopener noreferrer' }); mdInline(m[3], a); into.append(a); }
      else if (m[5]) into.append(h('a', { href: m[5], target: '_blank', rel: 'noopener noreferrer', text: m[5] }));
      else if (m[6] != null) wrap('strong', m[6]);
      else if (m[7] != null) wrap('s', m[7]);
      else if (m[9] != null) wrap('em', m[9]);
      else if (m[11] != null) wrap('em', m[11]);
      last = re.lastIndex;
    }
    if (last < text.length) into.append(text.slice(last));
  }
  function md(src) {
    const out = [], lines = String(src || '').replace(/\r/g, '').split('\n');
    const LI = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/, FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/, HEAD = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/, RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
    const TSEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
    const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
    const cell = (tag, c) => { const el = h(tag); mdInline(c, el); return el; };
    let i = 0;
    const list = (indent) => {
      const first = LI.exec(lines[i]), ordered = /\d/.test(first[2]);
      const el = h(ordered ? 'ol' : 'ul', ordered && parseInt(first[2], 10) !== 1 ? { start: parseInt(first[2], 10) } : null);
      while (i < lines.length) {
        const m = LI.exec(lines[i]);
        if (!m) break;
        const ind = m[1].replace(/\t/g, '    ').length;
        if (ind < indent) break;
        if (ind > indent + 1 && el.lastChild) { el.lastChild.append(list(ind)); continue; }
        if (/\d/.test(m[2]) !== ordered) break;                 // bullets after numbers (or the reverse) start a new list
        const li = h('li'); mdInline(m[3], li); el.append(li); i++;
        while (i < lines.length && lines[i].trim() && !LI.test(lines[i]) && /^\s{2,}\S/.test(lines[i]) && !FENCE.test(lines[i])) { li.append(h('br')); mdInline(lines[i].trim(), li); i++; }
        if (i + 1 < lines.length && !lines[i].trim() && LI.test(lines[i + 1])) i++;       // a blank line between items
      }
      return el;
    };
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) { i++; continue; }
      if ((m = FENCE.exec(line))) {
        const close = new RegExp('^\\s*' + (m[1][0] === '`' ? '`' : '~') + '{' + m[1].length + ',}\\s*$'), body = [];
        i++;
        while (i < lines.length && !close.test(lines[i])) body.push(lines[i++]);
        i++;
        out.push(codeBox(body.join('\n'), m[2]));
      } else if ((m = HEAD.exec(line))) { out.push(cell('h' + Math.min(6, m[1].length + 2), m[2])); i++; }
      else if (RULE.test(line)) { out.push(h('hr')); i++; }
      else if (/^\s*>/.test(line)) {
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push(h('blockquote', {}, md(q.join('\n'))));
      } else if (line.includes('|') && i + 1 < lines.length && TSEP.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        const head = cells(line), body = [], align = cells(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? 'center' : /-:$/.test(c) ? 'right' : ''));
        const acell = (tag, c, k) => { const el = cell(tag, c); if (align[k]) el.style.textAlign = align[k]; return el; };
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) body.push(cells(lines[i++]));
        out.push(h('div', { class: 'mdt-wrap' }, h('table', { class: 'mdt' }, h('thead', {}, h('tr', {}, head.map((c, k) => acell('th', c, k)))),
          h('tbody', {}, body.map((r) => h('tr', {}, head.map((_, k) => acell('td', r[k] || '', k))))))));
      } else if (LI.test(line)) out.push(list(LI.exec(line)[1].replace(/\t/g, '    ').length));
      else {
        const p = h('p');
        let k = 0;
        while (i < lines.length && lines[i].trim() && !(k && (FENCE.test(lines[i]) || HEAD.test(lines[i]) || LI.test(lines[i]) || /^\s*>/.test(lines[i]) || RULE.test(lines[i])))) {
          if (k++) p.append(h('br'));
          mdInline(lines[i++], p);
        }
        out.push(p);
      }
    }
    return out;
  }
  function lightbox(src, name) {
    const box = h('div', { class: 'lightbox', role: 'dialog', 'aria-label': name || 'Image' }, h('img', { src, alt: name || '' }));
    const close = () => { box.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    box.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    document.body.append(box);
  }
  // What the chat page keeps while you move around the app: which chat is open, the unsent text and attachments
  const CHAT = { id: null, fresh: false, rev: 0, gen: 0, msgs: new Map(), order: [], nodes: new Map(), open: new Set(), diffs: new Set(), draft: '', files: [], st: null, paste: null, ctl: null, poll: 0, title: null };
  try { CHAT.id = localStorage.getItem('basis.chat') || null; CHAT.draft = localStorage.getItem('basis.chatDraft') || ''; } catch (e) { /* no storage */ }
  document.addEventListener('paste', (e) => { if (ST.page === 'customize' && CHAT.paste) CHAT.paste(e); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && CHAT.title) { document.title = CHAT.title; CHAT.title = null; } });
  const readDataUrl = (file) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(new Error('Could not read ' + (file.name || 'the file'))); r.readAsDataURL(file); });
  const loadImage = (url) => new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error('That image could not be opened.')); im.src = url; });
  // An image for Claude: at most 1568 pixels on the long side (it sees no more than that), PNG kept for screenshots
  async function prepImage(file) {
    const url = await readDataUrl(file), name = file.name && file.name !== 'image.png' ? file.name : 'Pasted image.png';
    if (file.type === 'image/gif') return { name, url, kind: 'image' };
    const im = await loadImage(url), k = Math.min(1, 1568 / Math.max(im.naturalWidth, im.naturalHeight));
    if (k === 1 && /^image\/(png|jpeg|webp)$/.test(file.type) && file.size < 1500000) return { name, url, kind: 'image' };
    const cv = document.createElement('canvas');
    cv.width = Math.max(1, Math.round(im.naturalWidth * k)); cv.height = Math.max(1, Math.round(im.naturalHeight * k));
    cv.getContext('2d').drawImage(im, 0, 0, cv.width, cv.height);
    let out = file.type === 'image/jpeg' ? '' : cv.toDataURL('image/png');
    if (!out || out.length > 2600000) out = cv.toDataURL('image/jpeg', 0.9);
    return { name: name.replace(/\.[a-z0-9]+$/i, '') + (out.startsWith('data:image/png') ? '.png' : '.jpg'), url: out, kind: 'image' };
  }
  const bytes = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1e3)) + ' KB');
  const agoText = (ts) => { const sec = Date.now() / 1000 - ts; return sec < 90 ? 'now' : sec < 3600 ? Math.round(sec / 60) + 'm' : sec < 86400 ? Math.round(sec / 3600) + 'h' : sec < 86400 * 14 ? Math.round(sec / 86400) + 'd' : new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); };

  async function customizePage(page) {
    page.classList.add('wide');
    const C = CHAT, gen = ++C.gen;
    if (C.ctl) C.ctl.abort();
    C.rev = 0; C.nodes = new Map(); C.msgs = new Map(); C.order = []; C.st = null;
    const live = () => gen === C.gen && ST.page === 'customize' && root.isConnected;
    const note = h('div', { class: 'cx-note', role: 'status' });
    const say = (text, bad) => { note.textContent = text || ''; note.classList.toggle('down', !!bad); };
    const act = async (body) => { try { say(''); return await api('/api/agent', { json: body }); } catch (e) { say(e.message, true); return null; } };
    const waitBack = async () => {            // after a restart: wait for the server, then load the new code
      C.gen++;
      fill(msgs, h('div', { class: 'loading', text: 'Restarting. This takes up to a minute.' }));
      await new Promise((r) => setTimeout(r, 4000));
      for (let i = 0; i < 100; i++) {
        try { const r = await fetch('/api/status', { headers: { 'X-Requested-With': 'finance' } }); if (r.ok) { location.reload(); return; } } catch (e) { /* still down */ }
        await new Promise((r) => setTimeout(r, 2000));
      }
      fill(msgs, h('div', { class: 'alert bad', text: 'The server did not come back. Open /recover on this address to undo the last change.' }));
    };

    // ---- messages ----
    const msgs = h('div', { class: 'cx-msgs', tabindex: '0', 'aria-label': 'Conversation' }, h('div', { class: 'loading', text: 'Loading' }));
    const jump = h('button', { class: 'cx-jump', type: 'button', hidden: true, title: 'Jump to the latest' }, ci(CI.down), 'Latest');
    const atEnd = () => msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90;
    let stick = true;                       // follow the newest text until you scroll up
    const toEnd = () => { msgs.scrollTop = msgs.scrollHeight; jump.hidden = true; stick = true; };
    jump.addEventListener('click', toEnd);
    msgs.addEventListener('scroll', () => { stick = atEnd(); if (stick) jump.hidden = true; });
    const settle = () => { if (stick) toEnd(); else jump.hidden = atEnd(); };       // after a picture loads or a box opens
    const keep = (el, key) => { el.open = C.open.has(key); el.addEventListener('toggle', () => { if (el.open) C.open.add(key); else C.open.delete(key); }); return el; };
    // what a step gave back: a web search as a list of links, anything else as plain text
    const toolOut = (b) => {
      const m = b.name === 'WebSearch' && /Links: (\[.*\])/.exec(b.out);
      let links = null;
      if (m) { try { links = JSON.parse(m[1]).filter((l) => l && /^https?:\/\//.test(l.url)); } catch (e) { links = null; } }
      if (!links || !links.length) return h('pre', { class: 'tout', text: b.out });
      return h('ul', { class: 'tlinks' }, links.map((l) => h('li', {}, h('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer', text: l.title || l.url }), h('span', { class: 'faint', text: ' ' + l.url.split('/')[2] }))));
    };
    const toolRow = (b, key) => {
      const mark = b.state === 'run' ? h('i', { class: 'tdot run' }) : b.state === 'err' ? ci(CI.x) : b.state === 'stop' ? h('i', { class: 'tdot' }) : ci(CI.check);
      const head = [h('span', { class: 'tstate ' + (b.state || 'ok') }, mark), h('span', { class: 'tlabel', text: b.label || b.name || 'Step' })];
      if (!(b.diff && b.diff.length) && !b.cmd && !b.out) return h('div', { class: 'trow' }, head);
      return keep(h('details', { class: 'trow' }, h('summary', {}, head),
        h('div', { class: 'tbody' },
          b.cmd ? codeBox(b.cmd, 'command') : null,
          b.diff && b.diff.length ? diffView(b.diff) : null,
          b.more ? h('div', { class: 'faint tmore', text: `${b.more} more lines not shown here. View changes under the message shows the full diff.` }) : null,
          b.out ? toolOut(b) : null)), key);
    };
    const changeBox = (m, isLast) => {
      const ch = m.changed || [];
      if (!ch.length) return null;
      const web = ch.some((c) => c.file.startsWith('web/'));
      const plus = ch.reduce((a, c) => a + (c.plus || 0), 0), minus = ch.reduce((a, c) => a + (c.minus || 0), 0);
      const dbox = h('div', { class: 'cdiff', hidden: true });
      const show = async () => {
        dbox.hidden = false; view.textContent = 'Hide changes';
        if (dbox._done) return;
        fill(dbox, h('div', { class: 'faint', text: 'Loading' }));
        try {
          const d = await api('/api/agent/diff?id=' + m.snap);
          fill(dbox, ...d.files.map((f) => h('div', { class: 'dfile' }, h('div', { class: 'dname' }, h('span', { text: f.file }), h('span', { class: 'faint', text: f.what === 'changed' ? '' : f.what }), copyBtn(() => f.lines.join('\n'))),
            f.lines.length ? diffView(f.lines) : h('div', { class: 'faint tmore', text: 'No text to show.' }), f.more ? h('div', { class: 'faint tmore', text: `${f.more} more lines not shown.` }) : null)));
          dbox._done = true;
        } catch (e) { fill(dbox, h('p', { class: 'down', text: e.message })); }
      };
      const view = h('button', { class: 'btn small', type: 'button', text: 'View changes', onclick: () => { if (dbox.hidden) { C.diffs.add(m.snap); show(); } else { C.diffs.delete(m.snap); dbox.hidden = true; view.textContent = 'View changes'; } } });
      if (C.diffs.has(m.snap)) show();
      return h('div', { class: 'cchange' },
        h('div', { class: 'chead' }, h('b', { text: m.undone ? 'Changed, then undone' : `Changed ${ch.length} file${ch.length === 1 ? '' : 's'}` }),
          plus || minus ? h('span', { class: 'mono' }, h('span', { class: 'up', text: '+' + plus }), ' ', h('span', { class: 'down', text: '-' + minus })) : null),
        h('div', { class: 'cfiles' }, ch.map((c) => h('span', { class: 'tag', text: `${c.file}${c.what === 'changed' ? '' : ' ' + c.what}` }))),
        h('div', { class: 'cbtns' }, view,
          !m.undone && m.needs_restart ? h('button', { class: 'btn small primary', type: 'button', text: 'Restart to apply', title: 'Server code changed. If the server does not come back, the change is undone.', onclick: async () => { if (await act({ action: 'apply', id: m.snap })) waitBack(); } }) : null,
          !m.undone && web && !m.needs_restart ? h('button', { class: 'btn small', type: 'button', text: 'Reload the page', onclick: () => location.reload() }) : null,
          !m.undone ? h('button', { class: 'btn small', type: 'button', text: 'Undo this change', onclick: async () => { const r = await act({ action: 'undo', id: m.snap }); if (r && web) location.reload(); } }) : null),
        dbox);
    };
    const renderAssistant = (m, isLast) => {
      const kids = [], blocks = m.blocks || [];
      let group = null, n = 0;
      const closeGroup = () => {
        if (group && !m.working && group.childElementCount > 5) {       // a long run of steps folds away once the answer is in
          const count = group.childElementCount, key = m.id + ':g' + n++;
          const d = keep(h('details', { class: 'tfold' }, h('summary', { text: `${count} steps` })), key);
          kids[kids.indexOf(group)] = d; d.append(group);
        }
        group = null;
      };
      blocks.forEach((b, i) => {
        if (b.t === 'tool') { if (!group) { group = h('div', { class: 'tgroup' }); kids.push(group); } group.append(toolRow(b, m.id + ':' + i)); return; }
        closeGroup();
        if (b.t === 'think') { if ((b.text || '').trim()) kids.push(keep(h('details', { class: 'think' }, h('summary', { text: b.live ? 'Thinking' : 'Thought' }), h('div', { text: b.text })), m.id + ':' + i)); return; }
        kids.push(h('div', { class: 'md' }, md(b.text)));
      });
      closeGroup();
      const text = () => blocks.filter((b) => b.t === 'text').map((b) => b.text.trim()).join('\n\n');
      if (m.working) {
        const lastB = blocks[blocks.length - 1];
        const status = !lastB ? 'Starting' : lastB.t === 'think' ? 'Thinking' : lastB.t === 'tool' ? (lastB.state === 'run' ? lastB.label : 'Working') : 'Writing';
        kids.push(h('div', { class: 'cwork' }, h('i'), h('span', { text: status })));
      }
      const retryBtn = isLast && !m.working ? h('button', { class: 'btn small', type: 'button', onclick: () => act({ action: 'retry', chat: C.id }) }, 'Try again') : null;
      if (m.error) kids.push(h('div', { class: 'cerr' }, h('span', { text: m.error }), retryBtn));
      else if (m.stopped) kids.push(h('div', { class: 'cstop' }, h('span', { text: 'Stopped.' }), retryBtn));
      if (m.check) kids.push(h('div', { class: 'cerr col' }, h('span', { text: 'The change did not pass the syntax check, so it was undone.' }), h('pre', { class: 'tout', text: m.check })));
      kids.push(changeBox(m, isLast));
      if (!m.working) kids.push(h('div', { class: 'cact' }, text() ? copyBtn(text) : null,
        h('span', { class: 'faint', text: [m.model ? m.model.replace(/^claude-/, '') : '', isNum(m.secs) && m.secs >= 1 ? (m.secs >= 90 ? (m.secs / 60).toFixed(1) + ' min' : Math.round(m.secs) + ' s') : ''].filter(Boolean).join(' · ') })));
      return h('div', { class: 'cmsg ai' }, kids);
    };
    const renderUser = (m) => {
      const fs = m.files || [], imgs = fs.filter((f) => f.kind === 'image'), rest = fs.filter((f) => f.kind !== 'image');
      return h('div', { class: 'cmsg me' + (m.queued ? ' queued' : '') },
        h('div', { class: 'bubble' },
          imgs.length ? h('div', { class: 'cimgs' }, imgs.map((f) => { const im = h('img', { src: '/api/agent/file/' + f.id, alt: f.name, title: f.name }); im.addEventListener('click', () => lightbox(im.src, f.name)); im.addEventListener('load', settle); return im; })) : null,
          rest.length ? h('div', { class: 'cfiles' }, rest.map((f) => h('a', { class: 'fchip', href: '/api/agent/file/' + f.id, download: f.name }, ci(CI.file), h('span', { text: f.name }), h('span', { class: 'faint', text: bytes(f.size) })))) : null,
          m.text ? h('div', { class: 'utext', text: m.text }) : null),
        h('div', { class: 'cact' },
          m.queued ? h('span', { class: 'tag', text: 'Queued' }) : null,
          m.queued ? h('button', { class: 'cpy', type: 'button', title: 'Take it back', 'data-label': '1', onclick: async () => { const r = await act({ action: 'unqueue', chat: C.id, id: m.id }); if (r && r.text && !box.value) { box.value = r.text; grow(); saveDraft(); } } }, ci(CI.x), 'Cancel') : null,
          m.text ? copyBtn(() => m.text) : null));
    };
    const renderMsg = (m, isLast) => (m.role === 'user' ? renderUser(m) : m.role === 'note'
      ? h('div', { class: 'cnote' }, m.text, m.needs_restart ? h('button', { class: 'btn small', type: 'button', text: 'Restart to apply', onclick: async () => { if (await act({ action: 'apply', id: m.snap_undo })) waitBack(); } }) : null)
      : renderAssistant(m, isLast));
    const QUICK = [['What can you change?', 'In a few lines, what parts of this program can you change for me? Give three examples. Change nothing.'],
      ['Security check', 'Run a security check of this program: read every file and report anything that sends data out, writes a secret to a log or the page, or skips the request checks. Change nothing.'],
      ['Add a Bitcoin stock', 'Count the symbol ____ as Bitcoin & Equivalents.'],
      ['Fix what is in my screenshot', 'Here is a screenshot of something that looks wrong. Find the cause and fix it.']];
    const paintMsgs = () => {
      const st = C.st, wasEnd = stick || !C.nodes.size;
      if (!C.order.length) {
        C.nodes.clear();
        fill(msgs, h('div', { class: 'cempty' },
          h('h2', { text: st && !st.available ? 'Claude Code was not found' : 'What should change?' }),
          h('p', { class: 'dim', text: st && !st.available ? 'The claude command is not on this machine. Install Claude Code and sign in, then reload.' : 'Ask for a change, a fix or an explanation. Paste a screenshot, drop in a file, or start from one of these.' }),
          h('div', { class: 'chips', style: 'justify-content:center' }, QUICK.map(([l, t]) => h('button', { class: 'chip', type: 'button', text: l, onclick: () => { box.value = t; grow(); saveDraft(); box.focus(); } })))));
        return;
      }
      const lastAi = [...C.order].reverse().find((id) => (C.msgs.get(id) || {}).role === 'assistant');
      const want = [];
      for (const id of C.order) {
        const m = C.msgs.get(id);
        if (!m) continue;
        const isLast = id === lastAi && !(st && st.busy), sig = `${m.rev}|${isLast}`;
        let node = C.nodes.get(id);
        if (!node || node._sig !== sig) { node = renderMsg(m, isLast); node._sig = sig; C.nodes.set(id, node); }
        want.push(node);
      }
      for (const id of [...C.nodes.keys()]) if (!C.order.includes(id)) C.nodes.delete(id);
      const have = [...msgs.children];
      if (have.length !== want.length || have.some((el, k) => el !== want[k])) {
        // keep untouched messages where they are, so a selection or an open box in them survives
        let k = 0;
        for (const node of want) { if (msgs.children[k] !== node) msgs.insertBefore(node, msgs.children[k] || null); k++; }
        while (msgs.children.length > want.length) msgs.lastChild.remove();
      }
      if (wasEnd) toEnd(); else jump.hidden = atEnd();
    };

    // ---- composer ----
    const box = h('textarea', { rows: 1, placeholder: 'Ask for a change, or paste a screenshot', 'aria-label': 'Message to Claude' });
    box.value = C.draft || '';
    const saveDraft = () => { C.draft = box.value; try { localStorage.setItem('basis.chatDraft', box.value.slice(0, 20000)); } catch (e) { /* no storage */ } };
    const grow = () => { box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight + 2, Math.round(window.innerHeight * 0.38)) + 'px'; };
    const tray = h('div', { class: 'cx-tray', hidden: true });
    const paintTray = () => {
      tray.hidden = !C.files.length;
      fill(tray, ...C.files.map((f, k) => {
        const rm = h('button', { type: 'button', title: 'Remove', 'aria-label': 'Remove ' + f.name, onclick: () => { C.files.splice(k, 1); paintTray(); } }, ci(CI.x));
        if (f.kind === 'image') { const im = h('img', { src: f.url, alt: f.name, title: f.name }); im.addEventListener('click', () => lightbox(f.url, f.name)); return h('div', { class: 'cx-att' }, im, rm); }
        return h('div', { class: 'cx-att file', title: f.name }, ci(CI.file), h('span', { text: f.name }), rm);
      }));
    };
    const addFiles = async (list) => {
      for (const file of [...list]) {
        if (C.files.length >= 10) { say('At most 10 attachments in one message.', true); break; }
        try {
          if (file.size > 12e6 && !file.type.startsWith('image/')) throw new Error(`${file.name} is too large (the limit is 12 MB).`);
          C.files.push(file.type.startsWith('image/') ? await prepImage(file) : { name: file.name || 'file', url: await readDataUrl(file), kind: 'file' });
          say('');
        } catch (e) { say(e.message, true); }
      }
      paintTray(); box.focus();
    };
    C.paste = (e) => {
      const fl = e.clipboardData && e.clipboardData.files;
      if (!live() || !fl || !fl.length) return;
      const txt = e.clipboardData.getData('text/plain');
      if (txt && txt.trim()) return;                    // copied cells or text that also carry a picture: paste the text
      e.preventDefault(); addFiles(fl);
    };
    const pick = h('input', { type: 'file', multiple: true, hidden: true, accept: 'image/*,application/pdf,text/*,.md,.csv,.json,.py,.js,.css,.html,.log,.txt,.yaml,.yml,.toml,.sh' });
    pick.addEventListener('change', () => { addFiles(pick.files); pick.value = ''; });
    const attachBtn = h('button', { class: 'cbtn', type: 'button', title: 'Attach images or files', 'aria-label': 'Attach', onclick: () => pick.click() }, ci(CI.clip));
    const sendBtn = h('button', { class: 'cbtn primary', type: 'button', title: 'Send', 'aria-label': 'Send' }, ci(CI.up));
    const stopBtn = h('button', { class: 'cbtn stop', type: 'button', title: 'Stop', 'aria-label': 'Stop', hidden: true, onclick: () => act({ action: 'stop' }) }, ci(CI.stop));
    const modelSel = h('select', { 'aria-label': 'Model', title: 'Model' });
    const effortSel = h('select', { 'aria-label': 'Effort', title: 'How hard Claude thinks before it answers' });
    const webBtn = h('button', { class: 'chip tog', type: 'button', title: 'Let Claude search the web and read pages' }, ci(CI.globe), 'Web');
    const shellBtn = h('button', { class: 'chip tog', type: 'button', title: 'Let Claude run commands on this machine' }, ci(CI.term), 'Commands');
    modelSel.addEventListener('change', () => act({ model: modelSel.value }));
    effortSel.addEventListener('change', () => act({ effort: effortSel.value }));
    webBtn.addEventListener('click', async () => { const on = !(C.st && C.st.web); if (await act({ web: on })) { C.st.web = on; paintBar(); } });
    shellBtn.addEventListener('click', async () => {
      const on = !(C.st && C.st.shell);
      if (on && !window.confirm('Turn commands on?\n\nClaude gets a shell on this machine. A shell is not held back by the file rules: it can run anything your user account can, and that includes reading the data folder (your database and brokerage key). Its rules tell it not to, but nothing else stops it.\n\nLeave this off unless a job needs it.')) return;
      if (await act({ shell: on })) { C.st.shell = on; paintBar(); }
    });
    const hint = h('div', { class: 'faint cx-hint' });
    const paintBar = () => {
      const st = C.st;
      if (!st) return;
      const names = { haiku: 'Haiku (fastest)', sonnet: 'Sonnet', opus: 'Opus', fable: 'Fable (most capable)' };
      if (modelSel.dataset.sig !== st.models.join()) { fill(modelSel, ...st.models.map((k) => h('option', { value: k, text: names[k] || k }))); modelSel.dataset.sig = st.models.join(); }
      if (!effortSel.options.length) fill(effortSel, ...[['', 'Effort: default'], ['low', 'Effort: low'], ['medium', 'Effort: medium'], ['high', 'Effort: high']].map(([v, t]) => h('option', { value: v, text: t })));
      if (document.activeElement !== modelSel) modelSel.value = st.model;
      if (document.activeElement !== effortSel) effortSel.value = st.effort || '';
      webBtn.classList.toggle('on', st.web); webBtn.setAttribute('aria-pressed', String(st.web));
      shellBtn.classList.toggle('on', st.shell); shellBtn.setAttribute('aria-pressed', String(st.shell));
      const here = st.busy && st.running === C.id, elsewhere = st.busy && !here;
      stopBtn.hidden = !here;
      sendBtn.disabled = !st.available || !st.enabled || elsewhere;
      sendBtn.title = here ? 'Send when Claude finishes this one' : 'Send';
      hint.textContent = elsewhere ? 'Claude is working in another chat.' : here ? 'Claude is working. A message sent now waits its turn.'
        : window.matchMedia('(pointer: coarse)').matches ? 'Paste or attach screenshots.' : 'Enter sends, Shift+Enter makes a new line. Paste or drop screenshots.';
    };
    const send = async () => {
      const text = box.value.trim();
      if (!text && !C.files.length) return;
      if (C.files.reduce((a, f) => a + f.url.length, 0) > 44e6) { say('Those attachments are too large together. Send fewer at a time.', true); return; }
      sendBtn.disabled = true;
      const r = await act({ action: 'send', chat: C.fresh ? null : C.id, message: text, files: C.files.map((f) => ({ name: f.name, data: f.url.slice(f.url.indexOf(',') + 1) })) });
      sendBtn.disabled = false;
      if (!r) return;
      box.value = ''; C.files = []; saveDraft(); grow(); paintTray();
      if (r.chat !== C.id || C.fresh) openChat(r.chat); else toEnd();
    };
    sendBtn.addEventListener('click', send);
    box.addEventListener('input', () => { saveDraft(); grow(); });
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing && (e.metaKey || e.ctrlKey || (!e.shiftKey && !window.matchMedia('(pointer: coarse)').matches))) { e.preventDefault(); send(); }
      else if (e.key === 'ArrowUp' && !box.value) {             // bring back your last message to edit
        const mine = [...C.order].reverse().map((id) => C.msgs.get(id)).find((m) => m && m.role === 'user' && m.text);
        if (mine) { e.preventDefault(); box.value = mine.text; grow(); saveDraft(); }
      }
    });

    // ---- chats, limits ----
    const title = h('button', { class: 'cx-title', type: 'button', title: 'Rename this chat' });
    title.addEventListener('click', () => {
      if (!C.id || C.fresh) return;
      const inp = h('input', { type: 'text', class: 'cx-rename', maxlength: 80, 'aria-label': 'Chat name' });
      inp.value = (C.st && C.st.chat && C.st.chat.title) || '';
      let done = false;
      const finish = async (save) => { if (done) return; done = true; const v = inp.value.trim(); inp.replaceWith(title); if (save && v && v !== title.textContent) { title.textContent = v; await act({ action: 'rename', chat: C.id, title: v }); } };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false); });
      inp.addEventListener('blur', () => finish(true));
      title.replaceWith(inp); inp.focus(); inp.select();
    });
    const chatSel = h('select', { class: 'cx-pick', 'aria-label': 'Chats' });
    chatSel.addEventListener('change', () => openChat(chatSel.value === 'new' ? null : chatSel.value));
    const newBtn = h('button', { class: 'btn small', type: 'button', onclick: () => openChat(null) }, 'New chat');
    const chatList = h('div', { class: 'cx-chats' });
    const limits = h('div', { class: 'card cx-side-card', hidden: true });
    const paintSide = () => {
      const st = C.st;
      if (!st) return;
      title.textContent = C.fresh || !st.chat ? 'New chat' : st.chat.title || 'New chat';
      fill(chatSel, h('option', { value: 'new', text: 'New chat', selected: C.fresh || !st.chat }), ...st.chats.map((c) => h('option', { value: c.id, text: c.title, selected: !C.fresh && c.id === C.id })));
      fill(chatList, ...(st.chats.length ? st.chats.map((c) => {
        const row = h('div', { class: 'cx-chat' + (!C.fresh && c.id === C.id ? ' on' : '') },
          h('button', { type: 'button', class: 'open', onclick: () => openChat(c.id) }, h('span', { class: 't', text: c.title }), st.busy && st.running === c.id ? h('i', { class: 'tdot run', title: 'Working' }) : h('span', { class: 'faint', text: agoText(c.updated) })),
          h('button', { type: 'button', class: 'del', title: 'Delete this chat', 'aria-label': 'Delete ' + c.title, onclick: async () => {
            if (!window.confirm(`Delete the chat "${c.title}"?\n\nChanges it made to the program stay as they are.`)) return;
            if (await act({ action: 'delete', chat: c.id }) && c.id === C.id) openChat(null);
          } }, ci(CI.x)));
        return row;
      }) : [h('p', { class: 'faint', style: 'font-size:13px;margin:0', text: 'No chats yet.' })]));
      const w = (st.usage && st.usage.windows) || {}, keys = Object.keys(w).filter((k) => isNum(w[k].used));
      limits.hidden = !keys.length;
      const label = (k) => ({ five_hour: '5-hour limit', seven_day: 'Weekly limit', seven_day_opus: 'Weekly limit, Opus', seven_day_sonnet: 'Weekly limit, Sonnet' }[k] || k.replace(/_/g, ' '));
      fill(limits, h('h2', { text: 'Your Claude limits' }), ...keys.map((k) => {
        const bar = h('i'); bar.style.width = Math.min(100, w[k].used * 100) + '%';
        if (w[k].used >= 0.9) bar.style.background = 'var(--down)'; else if (w[k].used >= 0.7) bar.style.background = 'var(--warn)';
        return h('div', { class: 'cx-lim' }, h('div', { class: 'kv' }, h('span', { text: label(k) }), h('span', { text: Math.round(w[k].used * 100) + '% used' })), h('div', { class: 'cx-limbar' }, bar),
          w[k].resets ? h('div', { class: 'faint', text: 'Resets ' + new Date(w[k].resets * 1000).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit' }) }) : null);
      }), h('p', { class: 'faint', style: 'font-size:12px;margin:8px 0 0', text: 'As Claude Code reported them after the last message. The chat shares these with your other Claude use.' }));
    };
    function openChat(id) {
      C.id = id; C.fresh = !id; C.rev = 0; C.nodes = new Map(); C.msgs = new Map(); C.order = [];
      try { if (id) localStorage.setItem('basis.chat', id); else localStorage.removeItem('basis.chat'); } catch (e) { /* no storage */ }
      fill(msgs, h('div', { class: 'loading', text: 'Loading' }));
      loop();
      box.focus();
    }
    // One request waits on the server until something changes, so words show as Claude writes them
    async function loop() {
      const mine = ++C.poll;
      if (C.ctl) C.ctl.abort();
      let first = true, fails = 0;
      while (live() && mine === C.poll) {
        C.ctl = new AbortController();
        const sentFor = C.fresh ? 'new' : C.id || '';
        let st;
        try { st = await api(`/api/agent?chat=${sentFor}&rev=${C.rev}&wait=${first ? 0 : 1}`, { signal: C.ctl.signal }); fails = 0; }
        catch (e) {
          if (!live() || mine !== C.poll) return;
          if (++fails > 2) say('Lost the connection to the app. Trying again.', true);
          await new Promise((r) => setTimeout(r, Math.min(8000, 1000 * fails)));
          continue;
        }
        if (!live() || mine !== C.poll) return;
        if (fails === 0 && note.textContent.startsWith('Lost the connection')) say('');
        first = false;
        const was = C.st;
        if (!C.fresh && st.chat && st.chat.id !== C.id) { C.id = st.chat.id; C.nodes = new Map(); C.msgs = new Map(); try { localStorage.setItem('basis.chat', C.id); } catch (e) { /* no storage */ } }
        if (!C.fresh && !st.chat) C.fresh = true;
        C.st = st; C.rev = st.rev; C.order = st.order;
        for (const m of st.messages) C.msgs.set(m.id, m);
        for (const id of [...C.msgs.keys()]) if (!st.order.includes(id)) C.msgs.delete(id);
        paintMsgs(); paintBar();
        if (!was || JSON.stringify([was.chats, was.usage, was.chat, was.busy]) !== JSON.stringify([st.chats, st.usage, st.chat, st.busy])) paintSide();
        if (was && was.busy && !st.busy && document.hidden) { CHAT.title = CHAT.title || document.title; document.title = 'Claude is done · Basis'; }
      }
    }

    // ---- the page ----
    const drop = h('div', { class: 'cx-drop', hidden: true }, h('div', {}, ci(CI.clip), 'Drop to attach'));
    const compose = h('div', { class: 'cx-compose' }, tray, box, pick);
    // on a phone the model and effort pickers fold behind this button
    const optBtn = h('button', { class: 'cbtn cx-opt', type: 'button', title: 'Model and effort', 'aria-label': 'Model and effort', 'aria-expanded': 'false',
      onclick: () => { const on = compose.classList.toggle('opts'); optBtn.setAttribute('aria-expanded', String(on)); optBtn.classList.toggle('on', on); } }, ci(CI.tune));
    compose.append(h('div', { class: 'cx-row' }, attachBtn, webBtn, shellBtn, optBtn, modelSel, effortSel, h('span', { class: 'sp' }), stopBtn, sendBtn));
    const main = h('div', { class: 'card cx-main' },
      h('div', { class: 'cx-bar' }, title, chatSel, newBtn),
      h('div', { class: 'cx-scroll' }, msgs, jump),
      h('div', { class: 'cx-foot' }, note, compose, hint),
      drop);
    let depth = 0;
    const hasFiles = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
    main.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; drop.hidden = false; });
    main.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    main.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) drop.hidden = true; });
    main.addEventListener('drop', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth = 0; drop.hidden = true; addFiles(e.dataTransfer.files); });
    const how = h('details', { class: 'card cx-side-card cx-how' }, h('summary', {}, h('h2', { text: 'How this works' })),
      h('ul', { class: 'chow' },
        h('li', { text: 'Each message runs Claude Code on this machine, signed in as you, on your own Claude subscription.' }),
        h('li', { text: 'Paste, drop or attach screenshots, PDFs and text files. Images are scaled down to what Claude can see before they are sent.' }),
        h('li', { text: 'Claude can read and edit the program’s files, and nothing outside its folder. The data folder (your database and brokerage key) is closed to those tools.' }),
        h('li', { text: 'Web lets it search and read pages. Commands gives it a shell on this machine, which is not held back by the file rules, so it is off unless you turn it on.' }),
        h('li', { text: 'Before every message the program’s files are copied aside. Each change shows its diff and has an Undo button.' }),
        h('li', { text: 'The app checks the code after each change. A change that does not parse goes back to Claude to fix, and is undone if it still fails.' }),
        h('li', { text: 'Changes to the page show after a reload. Changes to server code need Restart to apply; if the server does not come back, the change is undone.' }),
        h('li', {}, 'If a change breaks this page, open ', h('a', { href: '/recover', text: '/recover' }), ' to undo it.')),
      h('p', { class: 'faint', style: 'font-size:12.5px;margin:10px 0 0', text: 'The rules Claude follows are in agent/RULES.md. You can ask it to show them.' }));
    how.open = (function () { try { return localStorage.getItem('basis.chatHow') === 'open'; } catch (e) { return false; } })();
    how.addEventListener('toggle', () => { try { localStorage.setItem('basis.chatHow', how.open ? 'open' : 'closed'); } catch (e) { /* no storage */ } });
    const root = h('div', { class: 'cx' }, main,
      h('div', { class: 'cx-side' },
        h('div', { class: 'card cx-side-card' }, h('div', { class: 'panel-head' }, h('h2', { text: 'Chats' }), h('button', { class: 'btn small', type: 'button', onclick: () => openChat(null) }, 'New chat')), chatList),
        limits, how));
    page.append(h('div', { class: 'head cx-head' }, h('div', {}, h('h1', { text: 'Customize' }),
      h('p', { class: 'sub', text: 'Ask Claude to change, fix or explain this program. It works on the program’s own files, on your Claude subscription.' }))), root);
    paintTray(); grow();
    loop();
  }

  // ---- the desktop program: first run, updates, links (server.py first_run_state, fin/update.py, basis.pyw) ----------
  const APP = () => (ST.model && ST.model.app) || { mode: 'server', ready: true, alerts: true };
  // sign-in pages open in the computer's own browser, not in the app's window
  const openLink = async (url) => {
    try { const r = await api('/api/open', { json: { url } }); if (!r.opened) window.open(r.url || url, '_blank', 'noopener'); }
    catch (e) { window.open(url, '_blank', 'noopener'); }
  };
  const SECURITY_PROMPT = 'Read every file in this folder. It is a portfolio dashboard that I am about to give a read-only brokerage key. Report anything that sends data anywhere other than SnapTrade and the public market-data sites it names, anything that writes a key or account number to a log or a page, any request handler that skips the host and origin checks, and anything that runs or downloads code. Give file and line for each finding. Change nothing.';
  const stepCard = (n, total, title, state, ...kids) => h('section', { class: 'wstep ' + state },
    h('div', { class: 'whead' }, h('span', { class: 'wnum', text: state === 'done' ? '' : String(n) }, state === 'done' ? ci(CI.check) : null),
      h('div', {}, h('div', { class: 'faint', style: 'font-size:12px', text: `Step ${n} of ${total}` }), h('h2', { text: title }))),
    state === 'wait' ? null : h('div', { class: 'wbody' }, kids));
  async function welcomePage(page) {
    document.body.classList.add('setup');
    ST.welcome = true;
    const box = h('div', { class: 'welcome' }, h('div', { class: 'loading', text: 'Loading' }));
    page.append(box);
    const W = ST.wz || (ST.wz = { msg: '', opened: false });
    const live = () => box.isConnected;
    const post = async (path, body) => { try { W.msg = ''; return await api(path, { json: body }); } catch (e) { W.msg = e.message; return null; } };
    const paint = (st) => {
      const keys = st.has_keys && !W.editKeys, linked = st.connections.filter((c) => !c.disabled), job = st.job;
      const synced = st.ready || job.done;
      const msg = W.msg ? h('p', { class: 'down', style: 'margin:10px 0 0;font-size:13.5px', text: W.msg }) : null;
      // 1. the key
      let s1;
      if (keys) s1 = stepCard(1, 3, 'Your SnapTrade key', 'done', h('p', { class: 'dim', text: `Saved (ending ${st.key_tail}).` }),
        !synced ? h('button', { class: 'linkish', type: 'button', text: 'Use a different key', onclick: () => { W.editKeys = true; tick(); } }) : null);
      else {
        const cid = h('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Client ID' }), key = h('input', { type: 'password', autocomplete: 'off', 'aria-label': 'Consumer Key' });
        cid.value = W.cid || ''; key.value = W.key || '';
        cid.addEventListener('input', () => { W.cid = cid.value; }); key.addEventListener('input', () => { W.key = key.value; });
        s1 = stepCard(1, 3, 'Get a SnapTrade key', 'now',
          h('p', { text: 'Basis reads your accounts through SnapTrade, a service that links to brokerages. Its Personal plan is free, covers up to 20 brokerage connections, and Basis asks it for read-only access.' }),
          h('ol', { class: 'wlist' },
            h('li', {}, 'Create a Personal account in the SnapTrade dashboard and verify your email. ', h('button', { class: 'btn small', type: 'button', text: 'Open SnapTrade', onclick: () => openLink('https://dashboard.snaptrade.com/signup') })),
            h('li', { text: 'Turn on two-factor authentication there.' }),
            h('li', {}, 'In the dashboard’s left menu, open ', h('b', { text: 'Build' }), ', then ', h('b', { text: 'API Keys' }), '. ', h('button', { class: 'btn small', type: 'button', text: 'Open API Keys', onclick: () => openLink('https://dashboard.snaptrade.com/api-key') })),
            h('li', {}, 'Copy the ', h('b', { text: 'Client ID' }), ' (it starts with PERS-) and the ', h('b', { text: 'Consumer Key' }), '. The Consumer Key is hidden: copy it with the button at the end of its box, or press the eye to show it.'),
            h('li', { text: 'Paste both below and press Save the key.' })),
          h('div', { class: 'form' }, h('label', { class: 'f' }, 'Client ID', cid), h('label', { class: 'f' }, 'Consumer Key', key),
            h('div', {}, h('button', { class: 'btn primary', type: 'button', text: 'Save the key', onclick: async () => {
              if (await post('/api/snaptrade/keys', { client_id: cid.value, consumer_key: key.value })) { W.editKeys = false; W.key = ''; }
              tick();
            } }))),
          h('p', { class: 'faint', style: 'font-size:12.5px;margin:10px 0 0', text: 'The key is saved on this computer only (on Windows it is encrypted for your Windows account) and is sent nowhere except to SnapTrade. One key can serve more than one copy of Basis: brokerages you linked with it before show up here as already linked.' }), msg);
      }
      // 2. a brokerage
      let s2;
      if (!keys) s2 = stepCard(2, 3, 'Link a brokerage', 'wait');
      else if (synced) s2 = stepCard(2, 3, 'Your brokerage', 'done', h('p', { class: 'dim', text: `${st.accounts} account${st.accounts === 1 ? '' : 's'} linked and read.` }));
      else {
        const connect = h('button', { class: 'btn' + (linked.length ? '' : ' primary'), type: 'button', text: linked.length ? 'Link another' : 'Link a brokerage', onclick: async () => {
          connect.disabled = true;
          const r = await post('/api/snaptrade/connect', {});
          if (r) { W.opened = true; openLink(r.url); }
          tick();
        } });
        s2 = stepCard(2, 3, 'Link a brokerage', 'now',
          h('p', { text: 'SnapTrade opens in your browser. Pick your broker and sign in on the broker’s own page. Basis never sees that password and can only read: it cannot trade or move money.' }),
          linked.length ? h('div', { class: 'wlinked' }, linked.map((c) => h('div', {}, ci(CI.check), h('span', { text: `${c.broker} is linked` })))) : null,
          W.opened && !linked.length ? h('p', { class: 'wwait' }, h('i'), 'Waiting for the link. Finish in your browser, then come back: this page notices by itself.') : null,
          st.error ? h('p', { class: 'down', style: 'font-size:13.5px', text: st.error }) : null,
          job.running ? h('p', { class: 'wwait' }, h('i'), job.step) : null,
          job.error && !job.running ? h('p', { class: 'down', style: 'font-size:13.5px', text: job.error }) : null,
          h('div', { class: 'cbtns', style: 'margin-top:12px' }, job.running ? null : connect,
            linked.length && !job.running ? h('button', { class: 'btn primary', type: 'button', text: job.error ? 'Try again' : 'Continue', onclick: async () => { await post('/api/first-run', { action: 'sync' }); tick(); } }) : null), msg);
      }
      // 3. Claude, optional
      let s3;
      if (!synced) s3 = stepCard(3, 3, 'Connect Claude (optional)', 'wait');
      else {
        const done = async (value) => { await post('/api/first-run', { action: 'agent', value }); ST.welcome = false; ST.wz = null; document.body.classList.remove('setup'); await load(true); go('/'); };
        const cmd = 'irm https://claude.ai/install.ps1 | iex';
        s3 = stepCard(3, 3, 'Connect Claude (optional)', 'now',
          h('p', { text: 'Basis has a Customize page where Claude changes the program for you: add a metric, fix something, recolor a chart. It runs Claude Code on this computer, on your own Claude subscription. Nothing about your accounts is sent to it unless you paste it into that chat yourself.' }),
          st.agent.available
            ? h('div', { class: 'wlinked' }, h('div', {}, ci(CI.check), h('span', { text: `Claude Code is installed${st.agent.version ? ' (' + st.agent.version + ')' : ''}. If it is signed in, Customize is ready.` })))
            : h('div', {},
              h('ol', { class: 'wlist' },
                h('li', {}, 'Install Claude Code. In PowerShell:', codeBox(cmd, 'PowerShell')),
                h('li', {}, 'Run ', h('code', { text: 'claude' }), ' once in a terminal and sign in. It needs a paid Claude plan or a Console account.'),
                h('li', { text: 'Come back and press Check again.' })),
              h('button', { class: 'btn small', type: 'button', text: 'Check again', onclick: tick })),
          h('div', { class: 'cbtns', style: 'margin-top:14px' },
            h('button', { class: 'btn primary', type: 'button', text: 'Open Basis', onclick: () => done(st.agent.available ? 'ready' : 'skipped') }),
            st.agent.available ? null : h('span', { class: 'faint', style: 'font-size:12.5px;align-self:center', text: 'You can do this later from the Customize page.' })));
      }
      const check = h('details', { class: 'wcheck' }, h('summary', { text: 'Want to check the program before you give it a key?' }),
        h('p', { class: 'dim', text: 'Basis is plain source code on your computer, and nothing in it is hidden. Open its folder in Claude Code or another coding agent and paste this:' }),
        codeBox(SECURITY_PROMPT, 'prompt'),
        h('p', { class: 'faint', style: 'font-size:12.5px;margin:8px 0 0', text: 'The program folder: ' + st.folder }));
      check.open = !!W.checkOpen;
      check.addEventListener('toggle', () => { W.checkOpen = check.open; });
      const active = document.activeElement, keep = active && box.contains(active) && active.matches('input') ? active.getAttribute('aria-label') : null;
      fill(box, h('div', { class: 'whero' }, h('img', { src: '/static/icon.svg', alt: '', width: 56, height: 56 }),
        h('div', {}, h('h1', { text: 'Welcome to Basis' }), h('p', { class: 'sub', text: 'Three steps: a key, a brokerage, and (if you want it) Claude. Everything stays on this computer.' }))),
        check, s1, s2, s3, h('p', { class: 'faint wfoot', text: `Basis ${st.version}` }));
      if (keep) { const again = box.querySelector(`input[aria-label="${keep}"]`); if (again) again.focus(); }
    };
    let last = '';
    async function tick() {
      if (!live()) return;
      const st = await api('/api/first-run').catch(() => null);
      if (!st || !live()) return;
      const sig = JSON.stringify([st.has_keys, st.connections, st.job, st.ready, st.agent, st.error, W.msg, W.editKeys, W.opened]);
      if (sig !== last) { last = sig; paint(st); }
    }
    await tick();
    const loop = setInterval(() => { if (!live()) { clearInterval(loop); return; } if (!box.querySelector('input:focus')) tick(); }, 3000);
  }
  // updates: a button in the top bar when a newer version is out, and a note after one was installed
  const updateBtn = $('#updateBtn');
  async function checkUpdate() {
    if (APP().mode !== 'desktop') return;
    const u = await api('/api/update').catch(() => null);
    if (!u) return;
    ST.update = u;
    const show = u.supported && (u.available || u.installer_only || (u.last && !u.last.seen) || u.busy);
    updateBtn.hidden = !show;
    if (show) fill(updateBtn, h('i'), u.last && !u.last.seen && !u.available ? 'Updated' : 'Update');
  }
  const waitRestart = async (box) => {
    fill(box, h('div', { class: 'loading', text: 'Restarting. This takes up to a minute.' }));
    await new Promise((r) => setTimeout(r, 3500));
    for (let i = 0; i < 120; i++) {
      try { const r = await fetch('/api/status', { headers: { 'X-Requested-With': 'finance' } }); if (r.ok) { location.reload(); return; } } catch (e) { /* still down */ }
      await new Promise((r) => setTimeout(r, 1500));
    }
    fill(box, h('div', { class: 'alert bad', text: 'Basis did not come back. Close this window and start it again; if the update was the cause, the previous version is restored.' }));
  };
  function openUpdate() {
    const u = ST.update;
    if (!u) return;
    const body = h('div', {});
    const paint = () => {
      const kids = [];
      if (u.last && !u.last.seen) {
        const files = u.last.replaced || [];
        kids.push(h('div', { class: 'card flat', style: 'margin-bottom:14px' }, h('h3', { text: `Updated to ${u.last.to}` }),
          h('p', { class: 'dim', style: 'font-size:13.5px', text: files.length ? `The update replaced ${files.length} file${files.length === 1 ? '' : 's'} you had changed. Your versions are saved in the folder ${u.last.kept} inside the program folder.` : `You were on ${u.last.from}. Nothing of yours was replaced.` }),
          files.length ? h('div', { class: 'cfiles', style: 'margin-bottom:10px' }, files.map((f) => h('span', { class: 'tag', text: f }))) : null,
          h('div', { class: 'cbtns' },
            files.length ? h('button', { class: 'btn small primary', type: 'button', text: 'Ask Claude to bring them back', onclick: async () => {
              CHAT.draft = `Basis was just updated from ${u.last.from} to ${u.last.to}. The update replaced these files I had changed: ${files.join(', ')}. My versions are saved in ${u.last.kept}/. Compare each with the new file and bring my changes back where they still make sense, without undoing what the update fixed. Tell me what you did.`;
              try { localStorage.setItem('basis.chatDraft', CHAT.draft); } catch (e) { /* no storage */ }
              await api('/api/update', { json: { action: 'dismiss' } }).catch(() => null); closeSheet(); checkUpdate(); go('/customize');
            } }) : null,
            h('button', { class: 'btn small', type: 'button', text: 'OK', onclick: async () => { await api('/api/update', { json: { action: 'dismiss' } }).catch(() => null); closeSheet(); checkUpdate(); } }))));
      }
      if (u.available || u.installer_only) {
        kids.push(h('h2', { text: `Basis ${u.latest} is out` }), h('p', { class: 'sub', text: `You have ${u.current}.` }),
          u.notes ? h('div', { class: 'card flat md', style: 'margin-bottom:14px' }, md(u.notes)) : null);
        if (u.installer_only) kids.push(h('p', { class: 'dim', text: 'This version needs the new installer.' }), h('button', { class: 'btn primary', type: 'button', text: 'Open the download page', onclick: () => openLink(u.url) }));
        else {
          if (u.customized.length) kids.push(h('div', { class: 'alert' }, icon(WARN_ICON), h('div', {}, h('b', { text: `You have changed ${u.customized.length} file${u.customized.length === 1 ? '' : 's'}. ` }),
            'The update replaces them. Your versions are saved first, inside the program folder, and the Customize chat can bring your changes back afterwards.',
            h('div', { class: 'cfiles', style: 'margin-top:8px' }, u.customized.map((f) => h('span', { class: 'tag', text: f }))))));
          const err = h('p', { class: 'down', style: 'font-size:13.5px', text: u.error && !u.busy ? u.error : '' });
          kids.push(h('p', { class: 'dim', style: 'font-size:13.5px', text: 'The current version is copied aside first. If the new one does not start, the old one is put back.' }),
            h('div', { class: 'cbtns' },
              h('button', { class: 'btn primary', type: 'button', text: 'Update now', onclick: async (e) => {
                e.target.disabled = true;
                try { await api('/api/update', { json: { action: 'apply' } }); } catch (er) { err.textContent = er.message; e.target.disabled = false; return; }
                const watch = setInterval(async () => {
                  const s2 = await api('/api/update').catch(() => null);
                  if (!s2) { clearInterval(watch); waitRestart(body); return; }
                  if (s2.error && !s2.busy) { clearInterval(watch); err.textContent = s2.error; e.target.disabled = false; return; }
                  e.target.textContent = s2.step || 'Working';
                  if (s2.step === 'Restarting') { clearInterval(watch); waitRestart(body); }
                }, 700);
              } }),
              h('button', { class: 'btn', type: 'button', text: 'Later', onclick: closeSheet }),
              h('button', { class: 'linkish', type: 'button', text: 'Stop checking for updates', onclick: async () => { await api('/api/update', { json: { check: false } }).catch(() => null); closeSheet(); updateBtn.hidden = true; } })), err);
        }
      }
      fill(body, ...kids);
    };
    paint();
    openSheet(body);
  }
  updateBtn.addEventListener('click', openUpdate);
  // the last card in Settings: version, updates, where things are, and who made it
  async function aboutCard(box) {
    const [u, f] = await Promise.all([api('/api/update').catch(() => null), api('/api/first-run').catch(() => null)]);
    if (!box.isConnected) return;
    const msg = h('span', { class: 'faint', style: 'font-size:12.5px' });
    const kv = (a, v) => h('div', { class: 'kv' }, h('span', { text: a }), h('span', { text: v }));
    const auto = h('input', { type: 'checkbox', checked: u && u.check });
    auto.addEventListener('change', async () => { await api('/api/update', { json: { check: auto.checked } }).catch(() => null); msg.textContent = auto.checked ? 'Basis will look for new versions.' : 'Basis will not look for new versions or contact GitHub.'; });
    fill(box, h('h2', { text: 'About Basis' }),
      kv('Version', (u && u.current) || (f && f.version) || '-'),
      f ? kv('Program folder', f.folder) : null, f && f.data_folder ? kv('Your data', f.data_folder) : null,
      u && u.supported ? h('div', { style: 'margin-top:12px' },
        h('label', { class: 'check' }, auto, ' Look for new versions (asks GitHub twice a day, sends nothing about you)'),
        h('div', { style: 'display:flex;gap:12px;align-items:center;margin-top:10px;flex-wrap:wrap' },
          h('button', { class: 'btn small', type: 'button', text: 'Check now', onclick: async () => {
            msg.textContent = 'Checking';
            const r = await api('/api/update', { json: { action: 'check' } }).catch((e) => ({ error: e.message }));
            ST.update = r; await checkUpdate();
            msg.textContent = r.error ? 'Could not check: ' + r.error : r.available || r.installer_only ? `Version ${r.latest} is out.` : 'You have the newest version.';
            if (r.available || r.installer_only) openUpdate();
          } }), msg))
        : h('p', { class: 'faint', style: 'font-size:12.5px;margin:10px 0 0', text: u && u.mode === 'server' ? 'This copy runs from its source on a server and is updated there.' : 'This copy runs from its source and is updated with git.' }),
      h('div', { class: 'cbtns', style: 'margin-top:14px' },
        h('button', { class: 'btn small', type: 'button', text: 'Source on GitHub', onclick: () => openLink('https://github.com/Connor-03/Basis') })),
      h('p', { class: 'credit' }, 'Built by Connor Aherne, assisted by Agentic Intelligence. ',
        h('a', { href: 'https://connoraherne.com', target: '_blank', rel: 'noopener', text: 'connoraherne.com', onclick: (e) => { e.preventDefault(); openLink('https://connoraherne.com'); } })),
      h('p', { class: 'faint', style: 'font-size:12px;margin:6px 0 0', text: 'Basis shows your own records and public market data. It is not financial advice, and its estimates (the margin buffer above all) can be wrong: your broker’s own numbers are the ones that count.' }));
  }
  // while the desktop window is open the page checks in, and says goodbye when it closes, so the program can stop
  let deskHooks = false;
  function desktopHooks() {
    if (deskHooks || APP().mode !== 'desktop') return;
    deskHooks = true;
    setInterval(() => { fetch('/api/ping', { headers: { 'X-Requested-With': 'finance' } }).catch(() => {}); }, 10000);
    window.addEventListener('pagehide', () => { try { fetch('/api/bye', { keepalive: true, headers: { 'X-Requested-With': 'finance' } }); } catch (e) { /* closing anyway */ } });
    checkUpdate();
    setInterval(checkUpdate, 3600e3);
  }

  // ---- Doom dashboard (fin/dip.py, fin/doom.py cycles) ---------------------------------------------------------
  async function doomPage(page) {
    page.classList.add('wide');
    page.append(h('div', { class: 'head' }, h('div', {}, h('h1', { text: 'Doom dashboard' }),
      h('p', { class: 'sub', text: 'Doom cycles from start to peak to calm, and where the numbers say to buy the dip while one is on.' })),
      h('a', { class: 'more', href: '/market', text: 'Doom score details' })));
    const body = h('div', {}, h('div', { class: 'loading', text: 'Loading' }));
    page.append(body);
    if (!ST.dip || Date.now() - ST.dip.at > 120e3) {
      const d = await api('/api/dip').catch((e) => ({ error: e.message }));
      ST.dip = { d, at: Date.now() };
    }
    if (ST.page !== 'doom' || !body.isConnected) return;
    const d = ST.dip.d;
    if (d.error) { fill(body, h('div', { class: 'alert bad', text: d.error })); return; }
    const cyc = d.cycle || {}, cur = cyc.current, rules = cyc.rules || {}, es = cyc.era_stats;
    const kv = (a, v, cls) => h('div', { class: 'kv' }, h('span', { text: a }), h('span', { class: cls || '', text: v }));
    // 1. the cycle
    const phases = ['Rising', 'Peaking', 'Easing', 'Calming'];
    const stepper = h('div', { class: 'phases' }, [...phases, 'Ended'].map((ph) => h('span', { class: cur && cur.phase === ph ? 'on' : '', text: ph })));
    const cycCard = h('div', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', { text: 'Doom cycle' }), h('span', { class: 'tag ' + (cur ? 'bad' : ''), text: cur ? 'On' : 'Standby' })),
      cur ? h('div', {},
        h('div', { class: 'dhead' }, h('div', { class: 'dscore', text: cur.now.toFixed(0) }),
          h('div', {}, h('div', { class: 'dlabel', text: d.label || '' }), h('div', { class: 'faint', style: 'font-size:12px', text: `Peak so far ${doomPeak(cur).toFixed(0)} on ${longDate(doomPeakDate(cur))}` }))),
        stepper,
        h('div', { style: 'margin-top:12px' },
          kv('Started', `${longDate(cur.start)} (${cur.length} trading days)`),
          isNum(cur.high) ? kv('Peak so far', `${cur.high.toFixed(0)}, ${doomPeakHow(cur)}`) : null,
          kv(isNum(cur.high) ? 'Highest close' : 'Peak so far', `${cur.peak.toFixed(0)}, ${cur.days_since_peak === 0 ? 'today' : cur.days_since_peak === 1 ? '1 trading day ago' : cur.days_since_peak + ' trading days ago'}`),
          kv('S&P 500 since the start', spct(cur.spx_from_start, 1), tone(cur.spx_from_start)),
          kv(`Calm days (under ${rules.calm})`, `${cur.calm_days} of ${rules.calm_days}`)),
        es ? h('p', { class: 'dim', style: 'font-size:13px;margin:12px 0 0', text: `The ${es.count} eras since 2010 peaked at a median ${es.median_peak.toFixed(0)}, a median ${Math.round(es.median_days_to_peak)} trading days after they started. The S&P 500’s low came a median ${Math.round(Math.abs(es.median_low_vs_peak))} days from the doom peak, and it fell a median ${pct(-es.median_drop, 1)} from the start of the cycle to its low.` }) : null)
      : h('p', { class: 'dim', text: `No cycle is on. One starts when the doom score reaches ${rules.start}. Score now ${isNum(d.score) ? d.score.toFixed(0) : '-'}.` }),
      h('p', { class: 'faint', style: 'font-size:12px;margin:10px 0 0', text: `Rules: a cycle starts at ${rules.start}; its peak is the highest score until it ends; it ends after ${rules.calm_days} trading days in a row under ${rules.calm}. Rising means a new high in the last 3 trading days, Easing means 10 or more points under the peak. A cycle that peaks at ${rules.era} or higher is an era of doom. These rules, and every past cycle, use the closing reading of each day. The peak shown for this cycle is the highest reading at any moment, which can sit above the highest close.` }));
    // 2. margin room
    const mg = d.margin || {}, buf = mg.buffer;
    const marginCard2 = h('div', { class: 'card' }, h('div', { class: 'panel-head' }, h('h2', { text: 'Room to buy' }), h('a', { class: 'more', href: '/risk', text: 'Margin' })),
      isNum(buf) ? h('div', {},
        h('div', { class: 'mid ' + (buf < 0 ? 'down' : buf < 2000 ? 'warn' : 'up'), text: money(buf, 0) }, h('span', { class: 'dim', style: 'font-size:14px;margin-left:8px', text: 'margin buffer (estimate)' })),
        h('p', { class: 'dim', style: 'font-size:13.5px', text: buf < 0
          ? `Below maintenance by about ${money(-buf, 0)}. Buying on margin adds to that: each $1,000 of a 25% stock adds $250. Room only comes from selling, a deposit, or prices rising.`
          : `Each $1,000 bought on margin uses about $250 of the buffer for a 25% stock (more for the stocks your broker sets higher). About ${money(buf / 0.25, 0)} of 25% stock would use it all.` }),
        mg.crypto ? h('p', { class: 'faint', style: 'font-size:12.5px', text: `Crypto left: ${money(mg.crypto, 0)}. Selling it adds its full value to the buffer.` }) : null)
      : h('p', { class: 'dim', text: 'No margin account.' }));
    // 3. this cycle against past eras
    const eras = cyc.eras || [];
    const show = eras.slice(-5);
    const ERA_COLORS = ['#3987e5', '#9A7AE0', '#4FB39A', '#C8A045', '#A7A3C6'];
    const maxLen = Math.max(...show.map((e) => e.path.length), cur ? cur.path.length : 0, 2);
    const idx = Array.from({ length: maxLen }, (_, i) => i);
    const series = show.map((e, k) => ({ name: `${e.name || 'Era'} (${e.peak_date.slice(0, 4)})`, color: ERA_COLORS[k], values: idx.map((i) => e.path[i] ?? null), width: 1.6, opacity: 0.75 }));
    if (cur) series.push({ name: 'This cycle', color: 'var(--s-doom)', values: idx.map((i) => cur.path[i] ?? null), width: 3 });
    const pathBox = h('div', { class: 'chart' });
    const pathCard = h('div', { class: 'card' }, h('h2', { text: 'This cycle against the last five eras' }),
      h('p', { class: 'dim', style: 'margin:0 0 8px;font-size:13px', text: 'Doom score by trading day since each cycle started.' }),
      h('div', { class: 'legend' }, series.map((sr) => h('span', {}, mkKey(sr.color), sr.name))), pathBox);
    // 4. eras table
    const allEras = [...eras].reverse();
    const eraRows = [...(cur ? [{ ...cur, name: 'This cycle', current: true }] : []), ...(ST.allEras ? allEras : allEras.slice(0, 6))];
    const scares = cyc.scares || [];
    const eraCard = h('div', { class: 'card' }, h('h2', { text: `Eras of doom since 2010 (peak ${rules.era} or higher)` }),
      table([
        { label: 'Era', get: (e) => h('div', {}, h('div', { style: e.current ? 'font-weight:600;color:var(--text)' : '', text: e.name || 'Unnamed' }), h('div', { class: 'faint', style: 'font-size:12px', text: `${shortDate(e.start)} to ${e.end ? shortDate(e.end) : 'now'}` })) },
        { label: 'Peak', r: true, get: (e) => h('div', {}, h('div', { class: 'mono', text: e.peak.toFixed(0) }), h('div', { class: 'faint', style: 'font-size:12px', text: shortDate(e.peak_date) })) },
        { label: 'Days to peak', r: true, m: true, get: (e) => String(e.days_to_peak) },
        { label: 'Length', r: true, m: true, get: (e) => `${e.length} days` },
        { label: 'S&P 500 fell', r: true, cls: () => 'down', get: (e) => spct(e.spx_drop, 1) },
        { label: 'S&P low vs doom peak', r: true, m: true, get: (e) => (e.low_vs_peak_days === 0 ? 'same day' : `${Math.abs(e.low_vs_peak_days)} days ${e.low_vs_peak_days > 0 ? 'after' : 'before'}`) },
        { label: '3 months after peak', r: true, cls: (e) => tone(e.spx_from_peak['63']), get: (e) => (isNum(e.spx_from_peak['63']) ? spct(e.spx_from_peak['63'], 1) : '-') },
        { label: '1 year after peak', r: true, cls: (e) => tone(e.spx_from_peak['252']), get: (e) => (isNum(e.spx_from_peak['252']) ? spct(e.spx_from_peak['252'], 1) : '-') }], eraRows),
      h('div', { class: 'line', style: 'display:flex;gap:12px;align-items:center;margin-top:10px;flex-wrap:wrap' },
        allEras.length > 6 ? h('button', { class: 'btn', text: ST.allEras ? 'Show the last six' : `Show all ${allEras.length}`, onclick: () => { ST.allEras = !ST.allEras; render(); } }) : null,
        h('span', { class: 'faint', style: 'font-size:12.5px', text: `Plus ${scares.length} smaller cycles that peaked between ${rules.start} and ${rules.era - 1}.` })));
    const parts = [h('div', { class: 'grid g2 stack' }, cycCard, pathCard), h('div', { class: 'block' }, eraCard), h('div', { class: 'block' }, marginCard2), movesBlock()];
    // 5. the dip analysis
    const on = d.active || ST.dipForce;
    if (!on) {
      parts.push(h('div', { class: 'block card' }, h('h2', { text: 'Where to buy the dip' }),
        h('p', { class: 'dim', text: `Standby. The full analysis turns on when a doom cycle starts (score ${rules.start}) and stays on until it ends.` }),
        h('button', { class: 'btn', text: 'Run it anyway', onclick: () => { ST.dipForce = true; render(); } })));
    } else parts.push(...dipSections(d));
    parts.push(h('div', { class: 'block card' }, h('h2', { text: 'How the dip score works' }),
      h('p', { class: 'dim', style: 'font-size:13.5px;margin:0 0 8px', text: 'Each reading is ranked against the same reading for the other stocks here: 100 is the best of the group, 0 the worst. In a crash nearly everything looks cheap against its own past, so the useful question is which looks best right now. Parts: Value 30% (P/E against its own last 3 years, forward P/E, PEG with growth capped at 50%; no positive earnings scores 0), Quality 25% (expected earnings growth, revenue growth, profit margin), Pullback 15% (under the 52-week high), Analysts 15% (rating, price target, ratings rising or falling over 3 months), Strength 15% (3-month return against the S&P 500, and whether it is still falling).' }),
      h('p', { class: 'dim', style: 'font-size:13.5px;margin:0 0 8px', text: 'Shown but not scored: the P/E you paid (your lots’ cost over the trailing earnings known on each buy date), crowd mood (StockTwits bullish and bearish tags on the latest 30 posts), short interest and insider buying. Funds get no score. The picks leave out anything over 10% of your holdings or with no positive earnings. This is a ranking of today’s numbers, not a forecast: analyst targets lag in a crash, and expected growth can be cut.' }),
      h('p', { class: 'faint', style: 'font-size:12.5px;margin:0', text: `Sources: Yahoo Finance (fundamentals, analyst ratings and targets, earnings history), StockTwits. Fundamentals refresh daily, crowd mood every 6 hours. Last update ${d.updated ? new Date(d.updated).toLocaleString() : '-'}.` })));
    fill(body, ...parts);
    requestAnimationFrame(() => lineChart(pathBox, { dates: idx.map(() => '2000-01-01'), series, yrange: [0, 100], yfmt: (v) => v.toFixed(0), fmt: (v) => (isNum(v) ? v.toFixed(0) : '-'),
      height: 260, label: 'Doom cycles by day', ticks: idx.filter((i) => i % (maxLen > 100 ? 40 : 20) === 0).map((i) => [i, `day ${i}`]), titleFmt: (_, i) => `Day ${i}`,
      refs: [{ y: rules.era, color: 'rgba(229,72,77,0.5)' }] }));
  }

  // ---- moves: decisions you log, then and now --------------------------------------------------------------
  function movesBlock() {
    const box = h('div', { class: 'block card moves-card' }, h('div', { class: 'panel-head' }, h('h2', { text: 'Your moves' })), h('div', { class: 'loading', text: 'Loading' }));
    (async () => {
      if (!ST.moves || Date.now() - ST.moves.at > 60e3) ST.moves = { d: await api('/api/moves').catch((e) => ({ error: e.message })), at: Date.now() };
      if (!box.isConnected && ST.page !== 'doom') return;
      fill(box, ...movesBody(ST.moves.d));
    })();
    return box;
  }
  function movesBody(d) {
    const head = h('div', { class: 'panel-head' }, h('h2', { text: 'Your moves' }),
      h('button', { class: 'btn small', text: ST.moveEdit ? 'Close' : 'Log a move', onclick: () => { ST.moveEdit = ST.moveEdit ? null : {}; fill(box0(), ...movesBody(d)); } }));
    if (d.error) return [head, h('div', { class: 'alert bad', text: d.error })];
    const list = d.moves || [];
    const out = [head, h('p', { class: 'dim', style: 'margin:0 0 12px;font-size:13.5px', text: 'The decisions you make in a cycle, with the picture when you made them and how each has played out since. Quantities and prices you leave blank are filled from your trades on that day or the three after.' })];
    if (ST.moveEdit) out.push(moveForm(d, ST.moveEdit));
    if (!list.length && !ST.moveEdit) out.push(h('p', { class: 'dim', text: 'Nothing logged yet.' }));
    out.push(h('div', { class: 'moves' }, list.map((mv) => moveRow(mv, d))));
    return out;
  }
  const box0 = () => document.querySelector('.moves-card');
  function moveRow(mv, d) {
    const t = mv.then || {}, n = d.now || {}, r = mv.result || {};
    const cell = (label, a, b, fmt, cls) => [h('span', { class: 'faint', text: label }), h('span', { class: 'mono', text: isNum(a) ? fmt(a) : '-' }), h('span', { class: 'mono ' + (cls || ''), text: isNum(b) ? fmt(b) : '-' })];
    const doomTxt = (x, l) => (isNum(x) ? x.toFixed(0) + (l ? ' ' + l : '') : '-');
    const grid = h('div', { class: 'mv-grid' },
      h('span', {}), h('span', { class: 'faint', text: t.live ? 'Then' : `On ${shortDate(mv.d)}` }), h('span', { class: 'faint', text: 'Now' }),
      ...cell('Net worth', t.net_worth, n.net_worth, (v) => money(v, 0), tone((n.net_worth || 0) - (t.net_worth || 0))),
      ...cell('Margin buffer', t.buffer, n.buffer, (v) => money(v, 0), isNum(n.buffer) ? (n.buffer < 0 ? 'down' : 'up') : ''),
      ...cell('Margin loan', t.loan, n.loan, (v) => money(v, 0)),
      h('span', { class: 'faint', text: 'Doom score' }), h('span', { class: 'mono', text: doomTxt(t.doom, t.doom_label) }), h('span', { class: 'mono', text: doomTxt(n.doom, n.doom_label) }),
      ...cell('S&P 500', t.spx, n.spx, (v) => v.toLocaleString('en-US', { maximumFractionDigits: 0 }), tone((n.spx || 0) - (t.spx || 0))),
      ...cell('Bitcoin', t.btc, n.btc, (v) => price(v, 0), tone((n.btc || 0) - (t.btc || 0))));
    let res = null;
    const leg = (x) => (x && x.symbol ? `${qty(x.qty)} ${x.symbol.replace('-USD', '')}${isNum(x.price) ? ' at ' + price(x.price, x.price < 10 ? 2 : 0) : ''}` : '');
    const ahead = (v, what) => (isNum(v) ? h('div', { class: 'mv-res ' + tone(v) }, `${v >= 0 ? what[0] : what[1]} ${money(Math.abs(v), 0)} so far`) : null);
    if (mv.kind === 'sell' && r.sold) {
      res = h('div', {}, h('div', { text: `Sold ${leg(r.sold)}${isNum(r.proceeds) ? ` for ${money(r.proceeds, 0)}` : ''}.${isNum(r.sold_now) ? ` Kept, it would be worth ${money(r.sold_now, 0)} now.` : ''}` }),
        ahead(r.delta, ['Selling is ahead by', 'Selling cost']));
    } else if (mv.kind === 'buy' && r.bought) {
      res = h('div', {}, h('div', { text: `Bought ${leg(r.bought)}${isNum(r.cost) ? ` for ${money(r.cost, 0)}` : ''}.${isNum(r.bought_now) ? ` Worth ${money(r.bought_now, 0)} now.` : ''}` }), ahead(r.delta, ['Up', 'Down']));
    } else if (mv.kind === 'swap') {
      const parts = [];
      parts.push(r.sold && r.sold.symbol ? `Sold ${leg(r.sold)}${isNum(r.proceeds) ? ` (${money(r.proceeds, 0)})` : ' (sale not seen yet)'}.` : '');
      parts.push(r.bought && r.bought.symbol ? (isNum(r.cost) ? `Bought ${leg(r.bought)} (${money(r.cost, 0)}).` : `No ${r.bought.symbol} buy seen yet.`) : '');
      if (isNum(r.cash_kept)) parts.push(`Kept ${money(r.cash_kept, 0)} as cash.`);
      res = h('div', {}, h('div', { text: parts.filter(Boolean).join(' ') }),
        isNum(r.swap_now) ? h('div', { class: 'dim', text: `Now: ${money(r.bought_now, 0)} of ${r.bought.symbol} plus the cash is ${money(r.swap_now, 0)}; the ${r.sold.symbol.replace('-USD', '')} would be ${money(r.sold_now, 0)}.` }) : null,
        ahead(r.delta, ['The swap is ahead by', 'The swap is behind by']),
        (r.sold && r.sold.filled) || (r.bought && r.bought.filled) ? h('div', { class: 'faint', style: 'font-size:12px', text: 'Filled from your trades; edit the move to set exact numbers.' }) : null);
    } else if (mv.kind === 'transfer') {
      const st = r.status;
      const txt = st === 'in transit' ? `${money(r.value, 0)} on the way to ${r.to_name}${r.symbols_waiting ? ` (${r.symbols_waiting} holding${r.symbols_waiting === 1 ? '' : 's'}${r.cash_waiting > 0.5 ? ' and cash' : ''})` : ''}. It stays in your net worth while it moves, and counts toward ${r.to_name}’s margin once it lands.`
        : st === 'arrived' ? `Arrived in ${r.to_name}${r.arrived_on ? ' on ' + longDate(r.arrived_on) : ''}.`
        : st === 'gone' ? `Left ${r.from_name} but never showed up in ${r.to_name} within 20 days.`
        : `Nothing has left ${r.from_name} yet${isNum(r.from_value) ? ` (${money(r.from_value, 0)} there now)` : ''}. Its holdings count as in transit from the day they leave until they land in ${r.to_name}, keeping their buy dates and cost.`;
      res = h('div', {}, h('div', { text: `${r.from_name} to ${r.to_name}.${r.expected && st !== 'arrived' ? ` Expected to clear ${longDate(r.expected)}.` : ''}` }), h('div', { class: 'dim', text: txt }),
        r.from_closed ? h('div', { class: 'faint', style: 'font-size:12px', text: `${r.from_name} reads zero and is off the dashboard.` }) : null);
    }
    const kindTag = { sell: 'Sold', buy: 'Bought', swap: 'Swap', transfer: 'Transfer', note: 'Note' }[mv.kind] || 'Note';
    return h('div', { class: 'mv' }, h('div', { class: 'mv-main' },
      h('div', { class: 'mv-head' }, h('div', {}, h('div', { class: 'faint', style: 'font-size:12px', text: longDate(mv.d) + (t.live && t.cycle_day ? ` · doom cycle day ${t.cycle_day}` : '') }), h('div', { class: 'mv-title', text: mv.title })),
        h('div', { style: 'display:flex;gap:8px;align-items:center' }, h('span', { class: 'tag', text: kindTag }),
          h('button', { class: 'btn small', text: 'Edit', onclick: () => { ST.moveEdit = mv; fill(box0(), ...movesBody(d)); box0().scrollIntoView({ block: 'start', behavior: 'smooth' }); } }))),
      mv.note ? h('p', { class: 'mv-note', text: mv.note }) : null,
      res ? h('div', { class: 'mv-body' }, res) : null),
      grid);
  }
  function moveForm(d, mv) {
    const data = mv.data || {};
    const today = new Date().toLocaleDateString('en-CA');
    const kind = h('select', {}, [['note', 'Note'], ['sell', 'Sold something'], ['buy', 'Bought something'], ['swap', 'Sold one thing for another'], ['transfer', 'Moved money between accounts']]
      .map(([v, l]) => h('option', { value: v, text: l, selected: (mv.kind || 'note') === v })));
    const dt = h('input', { type: 'date', value: mv.d || today });
    const title = h('input', { type: 'text', value: mv.title || '', placeholder: 'Sold $2,000 of BTC for margin room', style: 'flex:1;min-width:220px' });
    const note = h('textarea', { rows: 2, placeholder: 'Why, in your words', style: 'width:100%' }); note.value = mv.note || '';
    const legIn = (x) => ({ sym: h('input', { type: 'text', value: (x && x.symbol) || '', placeholder: 'Symbol', style: 'width:110px;text-transform:uppercase' }),
      q: h('input', { type: 'number', step: 'any', value: x && isNum(x.qty) && !x.filled ? x.qty : '', placeholder: 'Qty (blank: from trades)', style: 'width:170px' }),
      p: h('input', { type: 'number', step: 'any', value: x && isNum(x.price) && !x.filled ? x.price : '', placeholder: 'Price (blank: from trades)', style: 'width:180px' }) });
    const sold = legIn(data.sold), bought = legIn(data.bought);
    const accts = (d.accounts || []);
    const acctSel = (sel) => h('select', {}, h('option', { value: '', text: 'Pick an account' }), accts.map((a) => h('option', { value: a.id, text: a.name + (a.closed ? ' (closed)' : ''), selected: a.id === sel })));
    const from = acctSel(data.from), to = acctSel(data.to);
    const cash = h('input', { type: 'number', step: 'any', value: isNum(data.cash) ? data.cash : '', placeholder: 'Cash kept (blank: the difference)', style: 'width:230px' });
    const soldLine = h('div', { class: 'line' }, h('label', { class: 'f' }, 'Sold', sold.sym), sold.q, sold.p);
    const boughtLine = h('div', { class: 'line' }, h('label', { class: 'f' }, 'Bought', bought.sym), bought.q, bought.p);
    const cashLine = h('div', { class: 'line' }, cash);
    const expected = h('input', { type: 'date', value: data.expected || '' });
    const xferLine = h('div', { class: 'line' }, h('label', { class: 'f' }, 'From', from), h('label', { class: 'f' }, 'To', to), h('label', { class: 'f' }, 'Expected to clear', expected));
    const show = () => {
      const k = kind.value;
      soldLine.hidden = !(k === 'sell' || k === 'swap'); boughtLine.hidden = !(k === 'buy' || k === 'swap');
      cashLine.hidden = k !== 'swap'; xferLine.hidden = k !== 'transfer';
    };
    kind.onchange = show; show();
    const msg = h('div', { class: 'msg' });
    const legVal = (x) => (x.sym.value.trim() ? { symbol: x.sym.value.trim().toUpperCase(), qty: x.q.value, price: x.p.value } : null);
    const save = async (remove) => {
      try {
        if (remove && !confirm('Delete this move?')) return;
        const k = kind.value;
        await api('/api/moves', { json: remove ? { id: mv.id, remove: true } : { id: mv.id, d: dt.value, title: title.value, note: note.value, kind: k,
          data: { sold: (k === 'sell' || k === 'swap') ? legVal(sold) : null, bought: (k === 'buy' || k === 'swap') ? legVal(bought) : null,
            cash: k === 'swap' ? cash.value : null, from: k === 'transfer' ? from.value : null, to: k === 'transfer' ? to.value : null,
            expected: k === 'transfer' ? expected.value : null, accounts: data.accounts } } });
        ST.moveEdit = null; ST.moves = null;
        if (k === 'transfer' || remove) await load(true);
        render();
      } catch (e) { msg.textContent = e.message; }
    };
    return h('div', { class: 'form mv-form' },
      h('div', { class: 'line' }, h('label', { class: 'f' }, 'What', kind), h('label', { class: 'f' }, 'Date', dt), h('label', { class: 'f', style: 'flex:1' }, 'Title', title)),
      soldLine, boughtLine, cashLine, xferLine, note,
      h('div', { class: 'line' }, h('button', { class: 'btn primary', text: mv.id ? 'Save changes' : 'Log it', onclick: () => save(false) }),
        mv.id ? h('button', { class: 'btn danger', text: 'Delete', onclick: () => save(true) }) : null,
        h('span', { class: 'faint', style: 'font-size:12.5px', text: mv.id ? '' : 'A move dated today is stamped with the numbers right now.' })), msg);
  }

  function dipSections(d) {
    const rows = d.rows || [];
    const stocks = rows.filter((r) => isNum(r.score));
    const picks = stocks.filter((r) => !r.unprofitable && (r.weight || 0) <= 0.10 && r.coverage >= 0.7).slice(0, 4);
    const fmtPE = (v) => (isNum(v) ? (v >= 1000 ? '999+' : v.toFixed(0)) : '-');
    const scoreBar = (v) => { const b = h('i'); b.style.width = (v || 0) + '%'; b.style.background = doomColor(100 - (v || 0)); return h('div', { class: 'dscore-cell' }, h('b', { class: 'mono', text: isNum(v) ? v.toFixed(0) : '-' }), h('div', { class: 'dbar' }, b)); };
    // top picks
    const pickCards = h('div', { class: 'grid g4 stack-m' }, picks.map((r, k) => h('div', { class: 'card pick' },
      h('div', { class: 'panel-head' }, h('div', {}, h('span', { class: 'faint mono', text: `#${k + 1}` }), ' ', h('b', { class: 'sym', text: r.symbol })), h('span', { class: 'pick-score mono', text: r.score.toFixed(0) })),
      h('div', { class: 'faint', style: 'font-size:12.5px;margin:-6px 0 8px', text: r.name }),
      h('div', { class: 'mid', text: price(r.price) }, h('span', { class: tone(r.from_52w) + ' mono', style: 'font-size:13px;margin-left:8px', text: spct(r.from_52w, 0) + ' from high' })),
      h('ul', { class: 'pick-why' }, r.reasons.slice(0, 5).map((t) => h('li', { text: t }))),
      r.support ? h('div', { class: 'faint', style: 'font-size:12.5px', text: `Support near ${price(r.support)} (${pct(1 - r.support / r.price, 0)} lower)` }) : null,
      r.flags.length ? h('div', { class: 'warn', style: 'font-size:12.5px;margin-top:6px', text: r.flags.join('; ') }) : null,
      h('button', { class: 'btn', style: 'margin-top:10px', text: (ST.cmp || []).includes(r.symbol) ? 'In the comparison' : 'Compare', onclick: () => { ST.cmp = [...new Set([...(ST.cmp || []), r.symbol])].slice(-4); render(); } }))));
    // compare
    if (!ST.cmp) ST.cmp = ['DELL', 'NVDA'].filter((x) => rows.some((r) => r.symbol === x));
    const sel = ST.cmp.map((x) => rows.find((r) => r.symbol === x)).filter(Boolean);
    const addIn = h('input', { type: 'text', placeholder: 'Add a ticker', style: 'width:130px;text-transform:uppercase' });
    const cmsg = h('span', { class: 'faint', style: 'font-size:12.5px' });
    const addBtn = h('button', { class: 'btn', text: 'Add', onclick: async () => {
      const sym = addIn.value.trim().toUpperCase(); if (!sym) return;
      if (rows.some((r) => r.symbol === sym)) { ST.cmp = [...new Set([...ST.cmp, sym])].slice(-4); render(); return; }
      try { await api('/api/dip/watch', { json: { symbol: sym } }); cmsg.textContent = `Added ${sym} to the watch list. Its data arrives in about a minute; reopen this page then.`; ST.cmp = [...new Set([...ST.cmp, sym])].slice(-4); ST.dip = null; }
      catch (e) { cmsg.textContent = e.message; }
    } });
    const chips = h('div', { class: 'chips' }, ST.cmp.map((x) => h('button', { class: 'chip on', text: x + ' ×', onclick: () => { ST.cmp = ST.cmp.filter((y) => y !== x); render(); } })), addIn, addBtn);
    const metric = (label, f, cls) => ({ label, f, cls });
    const metrics = [
      metric('Dip score', (r) => (isNum(r.score) ? r.score.toFixed(0) : 'fund')),
      metric('Price', (r) => price(r.price)),
      metric('From 52-week high', (r) => spct(r.from_52w, 0), (r) => tone(r.from_52w)),
      metric('From all-time high', (r) => (isNum(r.from_ath) ? spct(r.from_ath, 0) : '-')),
      metric('P/E now', (r) => fmtPE(r.pe_now)),
      metric('Its usual P/E (3 years)', (r) => fmtPE(r.pe_median_3y)),
      metric('P/E you paid', (r) => fmtPE(r.pe_paid)),
      metric('Forward P/E', (r) => fmtPE(r.fpe)),
      metric('PEG', (r) => (isNum(r.peg) ? r.peg.toFixed(2) : '-')),
      metric('Earnings growth next year (est.)', (r) => (isNum(r.growth_next_year) ? spct(r.growth_next_year, 0) : '-')),
      metric('Revenue growth', (r) => (isNum(r.rev_growth) ? spct(r.rev_growth, 0) : '-')),
      metric('Profit margin', (r) => (isNum(r.margin) ? pct(r.margin, 0) : '-')),
      metric('Analysts', (r) => (r.analysts ? `${(r.rec_key || '').replace('_', ' ')} (${r.analysts})` : '-')),
      metric('Average target', (r) => (isNum(r.target) ? `${price(r.target, 0)} (${spct(r.target_upside, 0)})` : '-')),
      metric('Upgrades / downgrades, 90 days', (r) => (r.analysts ? `${r.ups_90d || 0} / ${r.downs_90d || 0}` : '-')),
      metric('Crowd (StockTwits)', (r) => (isNum(r.crowd_bull) ? `${pct(r.crowd_bull, 0)} bullish of ${r.crowd_n}` : '-')),
      metric('3 months vs S&P 500', (r) => (isNum(r.rs_3m) ? spct(r.rs_3m, 0) : '-'), (r) => tone(r.rs_3m)),
      metric('Still falling', (r) => (r.falling ? 'yes' : 'no')),
      metric('Support', (r) => (r.support ? `${price(r.support)} (${pct(1 - r.support / r.price, 0)} lower)` : 'none under the price')),
      metric('Short interest', (r) => (isNum(r.short_float) ? pct(r.short_float, 0) : '-')),
      metric('Margin needed per $1,000', (r) => price(1000 * r.maint_rate, 0)),
      metric('Your weight', (r) => (isNum(r.weight) ? pct(r.weight, 1) : 'not held')),
      metric('Value / Quality / Pullback / Analysts / Strength', (r) => ['value', 'quality', 'pullback', 'analysts', 'trend'].map((k) => (isNum((r.parts || {})[k]) ? r.parts[k].toFixed(0) : '-')).join(' / ')),
    ];
    const best = sel.length > 1 ? sel.reduce((a, b) => ((b.score || -1) > (a.score || -1) ? b : a)) : null;
    const cmpCard = h('div', { class: 'card' }, h('h2', { text: 'Compare' }), chips, cmsg,
      sel.length ? table([{ label: '', get: (mt) => h('span', { class: 'dim', text: mt.label }) },
        ...sel.map((r) => ({ label: r.symbol + (best && best.symbol === r.symbol ? ' (higher score)' : ''), r: true, cls: (mt) => (mt.cls ? mt.cls(r) : ''), get: (mt) => mt.f(r) }))], metrics) : h('p', { class: 'faint', text: 'Pick up to four.' }),
      best && sel.length > 1 ? h('p', { class: 'dim', style: 'font-size:13.5px;margin:10px 0 0', text: `${best.symbol} scores higher: ${best.reasons.slice(0, 3).join('; ')}.` + (best.flags.length ? ` Watch: ${best.flags.join('; ')}.` : '') }) : null);
    // full ranking
    const filt = ST.dipFilter || 'all';
    const fchips = h('div', { class: 'chips' }, [['all', 'All stocks'], ['held', 'Holdings'], ['watch', 'Watch list'], ['fund', 'Funds']].map(([k, l]) => h('button', { class: 'chip' + (filt === k ? ' on' : ''), text: l, onclick: () => { ST.dipFilter = k; render(); } })));
    const list = rows.filter((r) => (filt === 'fund' ? r.fund : !r.fund && (filt === 'all' || r.source === filt)));
    const rankCard = h('div', { class: 'card' }, h('div', { class: 'head' }, h('h2', { text: 'Every stock, ranked', style: 'margin:0' }), fchips),
      table([
        { label: 'Stock', get: (r) => h('div', {}, h('div', { class: 'sym', text: r.symbol + (r.source === 'watch' ? ' · watch' : '') }), h('div', { class: 'name', text: r.name })) },
        { label: 'Dip score', get: (r) => (r.fund ? h('span', { class: 'faint', text: 'fund' }) : scoreBar(r.score)) },
        { label: 'Price', r: true, get: (r) => h('div', {}, h('div', { text: price(r.price) }), h('div', { class: tone(r.from_52w) + ' faint', style: 'font-size:12px', text: spct(r.from_52w, 0) + ' 52w' })) },
        { label: 'P/E paid → now', r: true, m: true, get: (r) => (isNum(r.pe_paid) || isNum(r.pe_now) ? `${fmtPE(r.pe_paid)} → ${fmtPE(r.pe_now)}` : '-') },
        { label: 'Forward P/E', r: true, m: true, get: (r) => fmtPE(r.fpe) },
        { label: 'Growth next yr', r: true, m: true, get: (r) => (isNum(r.growth_next_year) ? spct(r.growth_next_year, 0) : '-') },
        { label: 'Target', r: true, m: true, get: (r) => (isNum(r.target_upside) && r.analysts >= 3 ? spct(r.target_upside, 0) : '-') },
        { label: 'Crowd', r: true, m: true, get: (r) => (isNum(r.crowd_bull) ? pct(r.crowd_bull, 0) + ' bull' : '-') },
        { label: 'Watch for', m: true, get: (r) => h('span', { class: 'warn', style: 'font-size:12.5px', text: r.flags.join('; ') }) }], list, { onRow: (r) => openSymbol(r.symbol) }));
    // weakest of what you hold
    const weak = stocks.filter((r) => r.source === 'held' && (r.value || 0) >= 100).slice(-6).reverse();
    const weakCard = h('div', { class: 'card' }, h('h2', { text: 'Weakest of what you hold' }),
      h('p', { class: 'dim', style: 'margin:0 0 8px;font-size:13px', text: 'Lowest dip scores among holdings worth $100 or more. Not a sell signal on its own; these are the least attractive to add to.' }),
      table([
        { label: 'Stock', get: (r) => h('span', { class: 'sym', text: r.symbol }) },
        { label: 'Score', r: true, get: (r) => r.score.toFixed(0) },
        { label: 'Value', r: true, get: (r) => money(r.value, 0) },
        { label: 'Why', get: (r) => h('span', { class: 'dim', style: 'font-size:12.5px', text: [...r.flags, ...r.reasons.slice(0, 2)].join('; ') }) }], weak, { onRow: (r) => openSymbol(r.symbol) }));
    return [h('h2', { class: 'section-title', text: 'Where to buy the dip' }),
      h('p', { class: 'sub', text: `The top of ${stocks.length} stocks you hold or watch, ranked against each other (100 is the best of the group). Leaves out positions over 10% of your holdings and companies with no positive earnings.` }),
      pickCards, h('div', { class: 'block' }, cmpCard), h('div', { class: 'block' }, rankCard), h('div', { class: 'block' }, weakCard)];
  }

  async function market(page) {
    page.append(h('h1', { text: 'Market' }), h('p', { class: 'sub', text: 'How scared the market is and how expensive stocks are, each against its own history.' }));
    const doomBox = h('section', { class: 'block' }, h('div', { class: 'loading', text: 'Loading the doom score' }));
    page.append(doomBox);
    doomSection(doomBox);
    page.append(h('h2', { class: 'section-title', text: 'Valuation' }), h('p', { class: 'sub', text: 'Price divided by earnings for the S&P 500 and the Nasdaq-100, and the Shiller P/E, which uses ten years of earnings so one bad year cannot distort it.' }));
    const body = h('div', {}, h('div', { class: 'loading', text: 'Loading' }));
    page.append(body);
    const mk = ST.market || (ST.market = await api('/api/market').catch((e) => ({ error: e.message })));
    if (ST.page !== 'market') return;
    if (mk.error) { fill(body, h('div', { class: 'alert bad', text: mk.error })); return; }
    const win = ST.valWin || '20';
    const winLabel = { 10: 'last 10 years', 20: 'last 20 years', all: 'all history' }[win];
    const chips = h('div', { class: 'chips' }, [['10', '10 years'], ['20', '20 years'], ['all', 'All history']].map(([k, l]) =>
      h('button', { class: 'chip' + (win === k ? ' on' : ''), text: l, onclick: () => { ST.valWin = k; render(); } })));
    const o = mk.overall || {};
    const sideZ = (z) => (isNum(z) ? `${z >= 0 ? '+' : ''}${z.toFixed(2)}` : '-');
    const head = h('div', { class: 'card' },
      h('div', { class: 'label', text: 'On average, stocks look' }),
      h('div', { class: 'big', style: 'font-family:var(--sans);font-weight:600', text: o.verdict ? o.verdict + (o.lean ? `, ${o.lean}` : '') : 'Not enough data yet' }),
      h('p', { class: 'dim', style: 'margin:6px 0 0;font-size:14px', text: `Average distance of the ${o.count || 3} measures below from their typical level over the last 20 years: ${sideZ(o.z)} spreads. Within 1 spread is fair value; 1 to 2 above is overvalued, more than 2 strongly overvalued (and the same below for undervalued).` }));
    const cards = ['sp500_pe', 'ndx_pe', 'cape'].filter((k) => mk.series[k]).map((k) => {
      const sr = mk.series[k], w = sr.windows[win] || {};
      const box = h('div', { class: 'chart' });
      const card2 = h('div', { class: 'card' },
        h('div', { class: 'head', style: 'margin-bottom:6px' }, h('div', {}, h('div', { class: 'label', text: sr.name }), h('div', { class: 'mid', text: sr.current.toFixed(2) })),
          h('span', { class: 'tag ' + (VERDICT_TONE[w.verdict] || ''), text: w.verdict || '' })),
        h('p', { class: 'dim', style: 'margin:0 0 10px;font-size:13.5px', text: isNum(w.mean) ? `Typical over the ${winLabel}: ${w.mean.toFixed(1)}. Today is ${sideZ(w.z)} spreads from it and higher than ${pct(w.percentile, 0)} of months since ${shortDate(w.from)}.` : 'Not enough history for this window.' }),
        h('div', { class: 'legend' }, h('span', {}, mkKey('var(--s-blue)'), 'P/E'), h('span', {}, h('i', { class: 'dash' }), 'Typical'), h('span', {}, mkKey('rgba(57,135,229,0.16)', true), 'Fair zone (within 1 spread)')),
        box, h('p', { class: 'faint', style: 'font-size:12px;margin:6px 0 0', text: `Reading from ${shortDate(sr.date)}. Source: ${mk.sources[k]}.` }));
      const hist = sr.history.filter(([d]) => win === 'all' || d >= (w.from || ''));
      const vals = hist.map(([, v]) => v);
      const sorted = [...vals].sort((a, b) => a - b);
      const cap = sorted.length ? Math.max(sorted[Math.floor(sorted.length * 0.9)] * 1.3, (w.mean || 0) + 4 * (w.sd || 0)) : null;
      requestAnimationFrame(() => lineChart(box, {
        dates: hist.map(([d]) => d), series: [{ name: 'P/E', color: 'var(--s-blue)', values: vals }],
        fmt: (v) => (isNum(v) ? v.toFixed(2) : '-'), yfmt: (v) => v.toFixed(0), height: 230, ymax: cap, label: sr.name + ' over time',
        bands: isNum(w.mean) && isNum(w.sd) ? [{ lo: w.mean - w.sd, hi: w.mean + w.sd, fill: 'rgba(57,135,229,0.10)' }] : [],
        refs: isNum(w.mean) ? [{ y: w.mean }, ...(isNum(w.sd) ? [{ y: w.mean + 2 * w.sd, dash: '2 4' }, { y: w.mean - 2 * w.sd, dash: '2 4' }] : [])] : [],
        titleFmt: (d) => new Date(d + 'T12:00:00').toLocaleString('en-US', { month: 'short', year: 'numeric' }),
        extraTip: (i) => (isNum(w.mean) && isNum(w.sd) ? [{ label: 'vs typical', value: sideZ((vals[i] - w.mean) / w.sd) + ' spreads' }] : []) }));
      return card2;
    });
    const m2 = (ST.model || {}).m2, yg0 = mk.yield_gap, perf = (ST.model || {}).performance || {}, mg0 = ((ST.model || {}).margin || [])[0];
    let m2Card = null;
    if (m2) {
      const box = h('div', { class: 'chart' });
      const mo = m2.history.months, mv = m2.history.values, yoyD = [], yoyV = [];
      for (let i = 12; i < mo.length; i++) { yoyD.push(mo[i] + '-15'); yoyV.push(mv[i] / mv[i - 12] - 1); }
      const gap = (v, good) => (isNum(v) ? h('span', { class: (v >= 0) === good ? 'up' : 'down', text: `${Math.abs(v * 100).toFixed(2)} pts ${v >= 0 ? 'over' : 'under'} M2` }) : '');
      const per = periodReturns(ST.model).find((r) => r[0] === '1Y') || [];
      const kv = (a, ...v) => h('div', { class: 'kv' }, h('span', { text: a }), h('span', {}, ...v));
      m2Card = h('div', { class: 'card' }, h('h2', { text: 'M2 money supply: the bar to beat' }),
        h('div', { class: 'grid g4 stack-m' },
          tile('M2 growth, last 12 months', pct(m2.yoy, 2), '', `through ${monthYear(m2.month)}`),
          tile('Last 3 months, yearly pace', pct(m2.three_month, 2), '', 'where it is heading'),
          tile('Per year, last 10 years', pct(m2.cagr10, 2), '', `${pct(m2.cagr5, 2)} over 5, ${pct(m2.cagr20, 2)} over 20`),
          tile('M2 now', price(m2.value / 1e12, 2) + ' trillion', '', 'all the dollars in circulation and in the bank')),
        h('p', { class: 'dim', style: 'font-size:13.5px;margin:12px 0 8px', text: 'A return above M2’s growth beats the pace new dollars are made. A return under it loses ground even when the number is positive. A loan that costs less than M2’s growth is cheap money.' }),
        h('div', { class: 'grid g2 stack' },
          h('div', {},
            yg0 ? kv('10-year Treasury pays', `${yg0.treasury.toFixed(2)}%  `, gap(yg0.treasury / 100 - m2.yoy, true)) : null,
            mg0 && mg0.rate ? kv('Your margin loan costs', `${pct(mg0.rate, 2)}  `, gap(mg0.vs_m2, false)) : null,
            isNum(per[1]) ? kv('You, last 12 months', `${spct(per[1], 1)}  `, h('span', { class: tone(per[2]), text: m2Word(per[2]) })) : null,
            perf.bench && perf.bench.M2 ? kv(`You per year since ${shortDate(perf.start)}`, `${spct(perf.annual, 1)}  `, gap(perf.annual - perf.bench.M2.annual, true)) : null,
            perf.bench && perf.bench.VOO && perf.bench.M2 ? kv('VOO per year, same dates', `${spct(perf.bench.VOO.annual, 1)}  `, gap(perf.bench.VOO.annual - perf.bench.M2.annual, true)) : null),
          h('div', {}, h('div', { class: 'legend' }, h('span', {}, mkKey('var(--s-m2)'), 'M2 growth over the 12 months before')), box)),
        h('p', { class: 'faint', style: 'font-size:12px;margin:8px 0 0', text: m2.note }));
      requestAnimationFrame(() => lineChart(box, { dates: yoyD, series: [{ name: 'M2, 12-month growth', color: 'var(--s-m2)', values: yoyV }], fmt: (v) => pct(v, 2), yfmt: axisPct, height: 200, label: 'M2 growth over time',
        titleFmt: (d) => new Date(d + 'T12:00:00').toLocaleString('en-US', { month: 'short', year: 'numeric' }) }));
    }
    const yg = mk.yield_gap;
    const ygCard = yg ? h('div', { class: 'card' }, h('h2', { text: 'Stocks against bonds' }),
      h('div', { class: 'grid g3' }, tile('S&P 500 earnings yield', yg.earnings_yield.toFixed(2) + '%', '', '1 divided by the P/E'), tile('10-year Treasury', yg.treasury.toFixed(2) + '%', '', 'what a safe bond pays'),
        tile('Difference', (yg.gap > 0 ? '+' : '') + yg.gap.toFixed(2) + ' points', yg.gap < 0 ? 'down' : 'up', yg.gap < 0 ? 'bonds pay more than stock earnings' : 'stock earnings pay more than bonds')),
      h('p', { class: 'note', text: 'When the earnings yield is below the Treasury yield, investors are paying up for stocks and counting on growth; historically that has come before lower long-run returns more often than higher ones.' })) : null;
    fill(body, head, h('div', { class: 'head', style: 'margin:18px 0 10px' }, h('h2', { text: 'Against their own history', style: 'margin:0' }), chips),
      h('div', { class: 'grid' }, cards), ygCard ? h('section', { class: 'block' }, ygCard) : null, m2Card ? h('section', { class: 'block' }, m2Card) : null,
      h('p', { class: 'note', text: 'Trailing P/E jumps when earnings collapse (the S&P 500 read 123 in May 2009), so charts cap those spikes and the typical level is the median, not the average. Valuation says little about the next few months; it is a better guide to the next ten years.' }));
  }

  function risk(page, m) {
    page.append(h('h1', { text: 'Margin' }));
    if (!m.margin.length) { page.append(h('p', { class: 'sub', text: 'No margin account. Mark an account as margin in Data and settings.' })); return; }
    for (const mg of m.margin) {
      const acct = m.accounts.find((a) => a.name === mg.account) || {};
      page.append(h('p', { class: 'sub', text: `${mg.account}. ${mg.note}` }), ...alerts({ margin: [mg], gaps: [] }));
      page.append(h('div', { class: 'grid g6' },
        tile('Equity', money(mg.equity, 0)), tile('Margin loan', money(mg.debt, 0)), tile('Maintenance', money(mg.requirement, 0), '', `${pct(mg.avg_maint, 0)} of holdings`),
        tile('Buffer (estimate)', money(mg.excess, 0), tone(mg.excess)), tile('Drop to a call', isNum(mg.drop_to_call) ? pct(mg.drop_to_call, 0) : '-', '', 'everything falling together'),
        tile('Leverage', isNum(mg.leverage) ? mg.leverage.toFixed(2) + 'x' : '-', '', 'holdings / equity')),
        mg.rate ? h('div', { class: 'grid g3', style: 'margin-top:14px' },
          tile('Margin rate', pct(mg.rate, 2), isNum(mg.vs_m2) ? (mg.vs_m2 <= 0 ? 'up' : 'down') : '', (isNum(mg.vs_m2) ? `${Math.abs(mg.vs_m2 * 100).toFixed(2)} pts ${mg.vs_m2 <= 0 ? 'under' : 'over'} M2’s ${pct(mg.m2_yoy, 2)}. ` : '') + (mg.rate_change_at && mg.rate_below ? `${pct(mg.rate_below, 2)} if the loan drops under ${price(mg.rate_change_at, 0)}` : (mg.rate_source || ''))),
          tile('Interest at this loan', money(-mg.interest_year, 0) + ' a year', 'down', `${money(-mg.interest_year / 12, 0)} a month` + (mg.free ? `; the first ${price(mg.free, 0)} is free` : '')),
          tile('Last month’s charge implies', isNum(mg.rate_implied) ? pct(mg.rate_implied, 2) : '-', '', 'last charge over the average loan before it')) : null);
      const form = h('div', { class: 'form' });
      const bufIn = h('input', { type: 'number', step: '0.01', placeholder: 'e.g. -700' });
      const cashIn = h('input', { type: 'number', step: '0.01', placeholder: 'e.g. 61250' });
      const msg = h('div', { class: 'msg' });
      form.append(h('p', { class: 'dim', style: 'margin:0;font-size:13.5px', text: `If your broker’s margin screen shows a different buffer, type it here and the estimate recalibrates the rates of the holdings you have not entered a rate for. Right now the estimate is ${money(mg.excess, 0)}.` + (acct.linked ? ' The loan comes from Robinhood through SnapTrade.' : '') }),
        h('div', { class: 'line' }, h('label', { class: 'f' }, 'Buffer your broker shows now', bufIn),
          acct.linked ? null : h('label', { class: 'f' }, 'Margin balance owed today', cashIn),
          h('button', { class: 'btn primary', text: 'Recalibrate', onclick: async () => {
            try {
              if (cashIn.value) await api('/api/balance', { json: { account_id: acct.id, kind: 'cash', margin_balance: true, value: cashIn.value, note: 'entered on the Margin page' } });
              if (bufIn.value) { const r = await api('/api/margin-nudge', { json: { account_id: acct.id, buffer: bufIn.value } }); msg.textContent = `Done. The estimate was ${money(r.before, 0)}.`; }
              else msg.textContent = 'Saved.';
              await load(true); render();
            } catch (e) { msg.textContent = e.message; }
          } })), msg,
        mg.scale_note ? h('div', { class: 'dim', style: 'font-size:12.5px', text: mg.scale_note }) : null);
      // the loan's rate: typed in, or else read from the last interest charge
      const rateIn = h('input', { type: 'number', step: '0.01', min: '0', max: '30', placeholder: 'e.g. 6.25' }), freeIn = h('input', { type: 'number', step: '1', min: '0', placeholder: '0' });
      const rmsg = h('div', { class: 'msg' });
      if (mg.rate && mg.rate_source === 'the rate you entered') rateIn.value = (mg.rate * 100).toFixed(2);
      if (mg.free) freeIn.value = mg.free;
      const rateForm = h('div', { class: 'form' },
        h('p', { class: 'dim', style: 'margin:0;font-size:13.5px', text: 'Enter the yearly rate your broker charges on this loan. Left empty, the rate is worked out from the last interest charge. Some brokers charge nothing on the first part of a loan: enter that amount too if yours does.' + (mg.rate_source ? ` In use now: ${mg.rate_source}.` : '') }),
        h('div', { class: 'line' }, h('label', { class: 'f' }, 'Rate, % a year', rateIn), h('label', { class: 'f' }, 'Free amount, $', freeIn),
          h('button', { class: 'btn', text: 'Save', onclick: async () => {
            try { await api('/api/margin-rate', { json: { account_id: acct.id, rate: rateIn.value, free: freeIn.value } }); await load(true); render(); } catch (e) { rmsg.textContent = e.message; }
          } })), rmsg);
      page.append(h('section', { class: 'block grid g2' }, card('Line up the estimate', form), card('Your margin rate', rateForm)));
      page.append(h('section', { class: 'block grid g2' },
        card('If the market falls', table([
          { label: 'Everything drops', get: (r) => pct(r.drop, 0) },
          { label: 'Equity', r: true, get: (r) => money(r.equity, 0) },
          { label: 'Maintenance', r: true, get: (r) => money(r.requirement, 0) },
          { label: 'Buffer', r: true, cls: (r) => tone(r.excess), get: (r) => money(r.excess, 0) }], mg.stress)),
        card('Interest paid by month', (() => { const c = h('div', { class: 'chart' }); const mi = m.costs.margin_interest_by_month; requestAnimationFrame(() => barChart(c, { months: mi.map((r) => r.month), values: mi.map((r) => r.amount), color: 'var(--down)', name: 'Interest', height: 190 })); return c; })())));
      page.append(h('section', { class: 'block' }, card('What each holding needs', table([
        { label: 'Holding', get: (r) => h('span', { class: 'sym', text: r.symbol }) },
        { label: 'Value', r: true, get: (r) => money(r.value, 0) },
        { label: 'Rate', r: true, get: (r) => (isNum(r.pct) ? pct(r.pct, 0) : '-') },
        { label: 'Needs', r: true, get: (r) => money(r.req, 0) },
        { label: 'Alone, a call after', r: true, m: true, get: (r) => (isNum(r.solo_drop) ? (r.solo_drop > 1 ? 'never' : pct(r.solo_drop, 0) + ' drop') : '-') },
        { label: 'Rate from', m: true, get: (r) => r.why }], mg.rows))));
    }
  }

  async function activity(page, m) {
    const f = ST.actFilter;
    const kinds = [['', 'Everything'], ['trade', 'Buys and sells'], ['option', 'Options'], ['income', 'Income'], ['flow', 'Deposits and transfers'], ['expense', 'Interest and fees']];
    const acctSel = h('select', { onchange: (e) => { f.account = e.target.value; render(); } }, h('option', { value: '', text: 'All accounts' }), m.accounts.map((a) => h('option', { value: a.id, text: a.name, selected: String(a.id) === f.account })));
    const symIn = h('input', { type: 'text', placeholder: 'Symbol', value: f.symbol, onchange: (e) => { f.symbol = e.target.value.toUpperCase(); render(); } });
    page.append(h('h1', { text: 'Activity' }), h('p', { class: 'sub', text: 'Every row from your exports, newest first.' }),
      h('div', { class: 'head' }, h('div', { class: 'chips' }, kinds.map(([k, l]) => h('button', { class: 'chip' + (f.kind === k ? ' on' : ''), text: l, onclick: () => { f.kind = k; render(); } }))), h('div', { class: 'chips' }, acctSel, symIn)));
    const box = h('div', { class: 'card' }, h('div', { class: 'loading', text: 'Loading' }));
    page.append(box);
    const q = new URLSearchParams({ limit: 600 }); if (f.kind) q.set('kind', f.kind); if (f.symbol) q.set('symbol', f.symbol); if (f.account) q.set('account', f.account);
    const data = await api('/api/activity?' + q).catch((e) => ({ rows: [], error: e.message }));
    if (ST.page !== 'activity') return;
    fill(box, actTable(data.rows), data.rows.length >= 600 ? h('p', { class: 'note', text: 'Showing the newest 600. Filter to see older rows.' }) : null);
  }
  function actTable(rows) {
    const label = (r) => ({ trade: r.sub === 'drip' ? 'Reinvested' : r.code, option_trade: r.code, option_close: { expired: 'Expired', assigned: 'Assigned', exercised: 'Exercised' }[r.sub] || r.code, income: 'Income', expense: 'Cost', flow: 'Transfer', transfer_sec: 'Shares in/out', split: 'Split', option_ca: 'Adjusted' }[r.kind] || r.code);
    return table([
      { label: 'Date', get: (r) => shortDate(r.date) },
      { label: 'What', get: (r) => h('span', { class: 'tag', text: label(r) }) },
      { label: 'Symbol', get: (r) => h('span', { class: 'sym', text: r.symbol || '' }) },
      { label: 'Description', m: true, get: (r) => h('span', { class: 'dim', text: r.desc }) },
      { label: 'Qty', r: true, get: (r) => (isNum(r.qty) ? qty(r.qty) : '') },
      { label: 'Price', r: true, m: true, get: (r) => (isNum(r.price) ? price(r.price) : '') },
      { label: 'Amount', r: true, cls: (r) => tone(r.amount), get: (r) => (r.amount ? smoney(r.amount) : '') }], rows);
  }

  async function settings(page, m) {
    page.append(h('h1', { text: 'Data and settings' }), h('p', { class: 'sub', text: 'Uploads, accounts, the holdings your files could not see, and your rules.' }));
    const holder = h('div', {}, h('div', { class: 'loading', text: 'Loading' }));
    page.append(holder);
    const sd = await api('/api/setup').catch((e) => ({ error: e.message }));
    if (ST.page !== 'settings') return;
    if (sd.error) { fill(holder, h('div', { class: 'alert bad', text: sd.error })); return; }
    const acctOpts = (sel) => sd.accounts.map((a) => h('option', { value: a.id, text: a.name, selected: a.id === sel }));
    const refresh = async () => { ST.menu = null; await load(true); render(); };

    // upload
    const acctSel = h('select', {}, acctOpts(sd.accounts[0] && sd.accounts[0].id));
    const file = h('input', { type: 'file', accept: '.csv,text/csv', hidden: true });
    const result = h('div', { class: 'result' });
    const drop = h('div', { class: 'drop' }, h('div', { text: 'Drop a CSV here or ' }, h('button', { class: 'linkish', text: 'choose a file', onclick: () => file.click() })),
      h('div', { class: 'faint', style: 'font-size:12.5px;margin-top:6px', text: 'Robinhood: Account, Reports and statements, Reports, Account activity (pick the widest date range). Fidelity and Schwab: a positions CSV works today.' }));
    const send = async (fl) => {
      if (!fl) return; result.textContent = `Reading ${fl.name}`;
      try {
        const buf = await fl.arrayBuffer();
        const r = await fetch('/api/import?account=' + acctSel.value, { method: 'POST', body: buf, headers: { 'X-Requested-With': 'finance', 'X-Filename': encodeURIComponent(fl.name), 'Content-Type': 'text/csv' } });
        const d = await r.json(); if (!r.ok) throw new Error(d.error);
        fill(result, h('div', { class: 'up', text: `${d.kind}: ${d.added} new rows, ${d.skipped} already stored (${shortDate(d.first)} to ${shortDate(d.last)}).` }),
          d.unknown_codes.length ? h('div', { class: 'warn', text: 'Codes the console does not know yet (kept as cash rows): ' + d.unknown_codes.join(', ') }) : null);
        await refresh();
      } catch (e) { fill(result, h('div', { class: 'down', text: e.message })); }
    };
    file.addEventListener('change', () => send(file.files[0]));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); send(e.dataTransfer.files[0]); });
    const up = card('Upload a statement', h('div', { class: 'form' }, h('label', { class: 'f' }, 'Account', acctSel), drop, file, result),
      h('h3', { text: 'Uploads', style: 'margin-top:18px' }),
      table([{ label: 'When', get: (i) => shortDate((i.created || '').slice(0, 10)) }, { label: 'File', get: (i) => h('span', { class: 'dim', text: i.filename }) },
        { label: 'Covers', m: true, get: (i) => `${shortDate(i.first_date)} to ${shortDate(i.last_date)}` }, { label: 'Added', r: true, get: (i) => (i.undone ? 'undone' : `${i.rows_added} of ${i.rows_total}`) },
        { label: '', r: true, get: (i) => (i.undone ? '' : h('button', { class: 'btn small danger', text: 'Undo', onclick: async () => { if (confirm(`Remove the ${i.rows_added} rows this upload added?`)) { await api('/api/import/undo', { json: { id: i.id } }); await refresh(); } } })) }], sd.imports));

    // data checks
    const gaps = m.gaps || [];
    const opening = sd.opening;
    const openTable = table([
      { label: 'Holding', get: (o) => h('span', { class: 'sym', text: o.symbol }) },
      { label: 'Shares', r: true, get: (o) => qty(o.qty) },
      { label: 'Cost', r: true, get: (o) => (isNum(o.cost) ? money(o.cost, 0) : h('span', { class: 'faint', text: 'unknown' })) },
      { label: 'Why', m: true, get: (o) => h('span', { class: 'dim', style: 'font-size:12.5px', text: o.note }) },
      { label: '', r: true, get: (o) => h('button', { class: 'btn small', text: 'Edit', onclick: () => editOpening(o) }) }], opening);
    function editOpening(o) {
      const q = prompt(`Shares of ${o.symbol} held before ${shortDate(o.as_of)} (0 to remove)`, o.qty); if (q === null) return;
      if (+q === 0) { api('/api/opening/delete', { json: { id: o.id } }).then(refresh); return; }
      const c = prompt(`Total cost of those ${q} shares in dollars (blank if unknown)`, o.cost == null ? '' : o.cost); if (c === null) return;
      api('/api/opening', { json: { id: o.id, account_id: o.account_id, symbol: o.symbol, qty: q, cost: c, note: o.note + ' (edited)' } }).then(refresh);
    }
    const est = gaps.flatMap((g) => g.basis_estimated || []);
    const checks = card('Holdings the history does not show',
      h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Shares a broker’s history never shows being bought (it starts after you got them, or skipped a reinvestment or reward) are filled in here: from shares sold before any buy, and from each broker’s own share counts when the account was linked. Each line says why.' }),
      openTable,
      h('div', { style: 'margin-top:10px', class: 'chips' }, h('button', { class: 'btn small', text: 'Match a share count', onclick: async () => {
        const sym = prompt('Symbol'); if (!sym) return;
        const q = prompt(`Shares of ${sym.toUpperCase()} your broker shows right now`); if (q === null || q === '') return;
        const r = await api('/api/reconcile', { json: { account_id: sd.accounts[0].id, symbol: sym, qty: q, source: 'your count' } }).catch((e) => ({ error: e.message }));
        alert(r.error ? r.error : r.changed.length ? `Changed by ${r.changed[0].diff > 0 ? '+' : ''}${r.changed[0].diff}` : 'Already matches.');
        refresh();
      } }), h('button', { class: 'btn small', text: 'Add a holding', onclick: () => {
        const sym = prompt('Symbol'); if (!sym) return; const q = prompt('Shares held before your first file'); if (!q) return; const c = prompt('Total cost in dollars (blank if unknown)', '');
        api('/api/opening', { json: { account_id: sd.accounts[0].id, symbol: sym, qty: q, cost: c } }).then(refresh);
      } })),
      est.length ? h('p', { class: 'note', text: `${est.length} transferred-in lots use the market price on the transfer day as cost, because the broker did not send the original cost.` }) : null);

    // accounts
    const accts = card('Accounts', table([
      { label: 'Name', get: (a) => a.name }, { label: 'Broker', get: (a) => a.broker }, { label: 'Type', get: (a) => a.kind },
      { label: 'Margin', get: (a) => (a.margin ? 'yes' : 'no') },
      { label: '', r: true, get: (a) => h('button', { class: 'btn small', text: 'Edit', onclick: () => {
        const name = prompt('Account name', a.name); if (!name) return; const margin = confirm('Is this a margin account? OK = yes, Cancel = no');
        api('/api/account', { json: { id: a.id, name, broker: a.broker, kind: a.kind, margin } }).then(refresh);
      } }) }], sd.accounts),
      h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn small', text: 'Add an account', onclick: () => {
        const name = prompt('Account name (for example Fidelity Brokerage)'); if (!name) return;
        const broker = prompt('Broker (robinhood, fidelity, schwab, other)', 'fidelity') || 'other';
        const kind = prompt('Type (taxable, roth, traditional, hsa, other)', 'taxable') || 'taxable';
        api('/api/account', { json: { name, broker, kind, margin: false } }).then(refresh);
      } })));

    // other assets
    const man = card('Other assets', h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Things no link or export covers, like a crypto wallet or cash at a bank. With a symbol (BTC-USD) the value follows the live price.' }),
      table([{ label: 'Name', get: (x) => x.label }, { label: 'Holding', get: (x) => (x.symbol ? `${qty(x.qty)} ${x.symbol}` : '') }, { label: 'Value', r: true, get: (x) => (x.symbol ? 'live' : money(x.value, 0)) },
        { label: 'Note', m: true, get: (x) => h('span', { class: 'dim', style: 'font-size:12.5px', text: x.note }) },
        { label: '', r: true, get: (x) => h('button', { class: 'btn small', text: 'Edit', onclick: () => editManual(x) }) }], sd.manual),
      h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn small', text: 'Add an asset', onclick: () => editManual({}) })));
    function editManual(x) {
      const label = prompt('Name (blank to delete)', x.label || ''); if (label === null) return;
      if (!label && x.id) { api('/api/manual/delete', { json: { id: x.id } }).then(refresh); return; }
      const sym = prompt('Live symbol, like BTC-USD (blank for a fixed value)', x.symbol || ''); if (sym === null) return;
      let q = null, v = null;
      if (sym) { q = prompt('How many', x.qty == null ? '' : x.qty); if (q === null) return; } else { v = prompt('Value in dollars', x.value == null ? '' : x.value); if (v === null) return; }
      api('/api/manual', { json: { id: x.id, label, symbol: sym, qty: q, value: v, kind: sym ? 'crypto' : 'other', note: x.note || '' } }).then(refresh);
    }

    // option rules
    const r = sd.option_rules;
    const tl = h('input', { type: 'number', step: '0.1', value: r.target_low, style: 'width:80px' }), th = h('input', { type: 'number', step: '0.1', value: r.target_high, style: 'width:80px' });
    const watch = h('input', { type: 'text', value: (r.watch || []).join(', '), style: 'min-width:260px;flex:1' });
    const macro = h('input', { type: 'text', value: (r.macro_dates || []).join(', '), placeholder: '2026-12-09, 2026-12-10', style: 'min-width:260px;flex:1' });
    const symRules = h('textarea', { rows: 8, style: 'width:100%;font-family:var(--mono);font-size:12.5px' }); symRules.value = JSON.stringify(r.symbols, null, 1);
    const rmsg = h('div', { class: 'msg' });
    const rules = card('Options menu rules', h('div', { class: 'form' },
      h('div', { class: 'line' }, h('label', { class: 'f' }, 'Weekly target low %', tl), h('label', { class: 'f' }, 'high %', th)),
      h('label', { class: 'f' }, 'Put watch list', watch),
      h('label', { class: 'f' }, 'News days to wait on (FOMC, CPI, jobs)', macro),
      h('label', { class: 'f' }, 'Per-stock rules (no_puts, calls_above_basis, keep_uncovered, max_contracts, long_dated, note)', symRules),
      h('div', {}, h('button', { class: 'btn primary', text: 'Save rules', onclick: async () => {
        try {
          await api('/api/option-rules', { json: { target_low: tl.value, target_high: th.value, watch: watch.value.split(/[\s,]+/), macro_dates: macro.value.split(/[\s,]+/).filter(Boolean), symbols: JSON.parse(symRules.value) } });
          ST.menu = null; rmsg.textContent = 'Saved. The menu rebuilds on its next visit.';
        } catch (e) { rmsg.textContent = e.message; }
      } })), rmsg));

    // maintenance overrides
    const ov = sd.maintenance;
    const ovIn = h('input', { type: 'text', value: Object.entries(ov).map(([k, v]) => `${k}=${v}`).join(', '), placeholder: 'MSTR=100, SMCI=50', style: 'flex:1;min-width:240px' });
    const maint = card('Margin rates per stock', h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Your broker shows each stock’s maintenance rate on its page. Enter the ones you know as SYMBOL=percent; the rest use the estimate.' }),
      h('div', { class: 'line', style: 'display:flex;gap:8px;flex-wrap:wrap' }, ovIn, h('button', { class: 'btn', text: 'Save', onclick: async () => {
        const o = {}; for (const part of ovIn.value.split(',')) { const [k, v] = part.split('='); if (k && k.trim()) o[k.trim().toUpperCase()] = v == null ? '' : v.trim(); }
        for (const k of Object.keys(ov)) if (!(k in o)) o[k] = '';
        await api('/api/maintenance', { json: { account_id: sd.accounts[0].id, overrides: o } }); await refresh();
      } })));

    // which stocks count with Bitcoin or crypto in "By type" and the sector lists
    const tg = sd.type_groups || {}, tgList = (name) => Object.keys(tg).filter((k) => tg[k] === name).join(', ');
    const btcIn = h('input', { type: 'text', value: tgList('Bitcoin & Equivalents'), placeholder: 'none', style: 'width:100%' });
    const cryIn = h('input', { type: 'text', value: tgList('Crypto & Equivalents'), placeholder: 'none', style: 'width:100%' });
    const outIn = h('input', { type: 'text', value: tgList('none'), placeholder: 'none', style: 'width:100%' });
    const tgMsg = h('span', { class: 'faint', style: 'font-size:12.5px' });
    const auto = (ST.model || {}).type_auto || [], autoOf = (g) => auto.filter((x) => x.group === g && !(x.symbol in tg)).map((x) => x.symbol).join(', ') || 'none';
    const types = card('Bitcoin and crypto stocks', h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Coins, funds named for a coin, and companies that hold a coin as their treasury are grouped on their own under By type and in the sector lists. Add a symbol to a group, or keep one out, here. Separate symbols with commas.' }),
      h('div', { class: 'kv' }, h('span', { text: 'Found on their own, Bitcoin' }), h('span', { text: autoOf('Bitcoin & Equivalents') })),
      h('div', { class: 'kv', style: 'margin-bottom:10px' }, h('span', { text: 'Found on their own, crypto' }), h('span', { text: autoOf('Crypto & Equivalents') })),
      h('div', { class: 'form' }, h('label', { class: 'f' }, 'Also count as Bitcoin & Equivalents', btcIn), h('label', { class: 'f' }, 'Also count as Crypto & Equivalents', cryIn), h('label', { class: 'f' }, 'Keep out of both', outIn),
      h('div', { style: 'display:flex;gap:12px;align-items:center' }, h('button', { class: 'btn', text: 'Save', onclick: async () => {
        try { await api('/api/type-groups', { json: { bitcoin: btcIn.value, crypto: cryIn.value, none: outIn.value } }); tgMsg.textContent = 'Saved.'; await refresh(); } catch (e) { tgMsg.textContent = e.message; }
      } }), tgMsg)));

    const connect = card('Linked brokerages (SnapTrade)', h('div', { class: 'loading', text: 'Loading' }));
    const install = card('Put it on your iPhone', h('ol', { class: 'dim', style: 'font-size:13.5px;margin:0;padding-left:18px' },
      h('li', { text: `Open ${(m.app && m.app.phone_url) || 'this copy’s https address'} in Safari.` }),
      h('li', { text: 'Share, then Add to Home Screen.' }),
      h('li', { text: 'It opens full screen like an app.' })));

    const phone = !m.app || m.app.alerts;                    // a copy served over https from a home server
    const notif = card('Phone alerts', h('div', { class: 'loading', text: 'Loading' }));
    const about = h('div', { class: 'card about' }, h('div', { class: 'loading', text: 'Loading' }));
    fill(holder, h('div', { class: 'grid g2' }, up, checks), h('section', { class: 'block grid g2' }, accts, man),
      h('section', { class: 'block grid g2' }, rules, h('div', { class: 'grid' }, phone ? notif : null, maint, types, connect, phone ? install : null)),
      h('section', { class: 'block' }, about));
    if (phone) notifCard(notif);
    snapCard(connect, sd);
    aboutCard(about);
  }

  async function snapCard(box, sd) {
    const d = await api('/api/snaptrade').catch((e) => ({ error: e.message }));
    const title = h('h2', { text: 'Linked brokerages (SnapTrade)' });
    if (d.error) { fill(box, title, h('p', { class: 'down', text: d.error })); return; }
    const msg = h('div', { class: 'msg', text: ST.snapMsg || '' });
    ST.snapMsg = '';
    const again = () => snapCard(box, sd);
    const after = async () => { ST.menu = null; await load(true); render(); };
    if (!d.has_keys || box.dataset.editKeys) {
      const cid = h('input', { type: 'text', placeholder: 'Client ID', autocomplete: 'off', value: d.client_id || '' });
      const key = h('input', { type: 'password', placeholder: 'Consumer Key', autocomplete: 'off' });
      fill(box, title,
        h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Free for up to 20 connections, read-only. In the SnapTrade dashboard (dashboard.snaptrade.com) open Build, then API Keys, and copy the Client ID (it starts with PERS-) and the Consumer Key (hidden until you press the eye, or use its copy button). They stay on this computer and are never shown again.' }),
        h('div', { class: 'form' }, h('label', { class: 'f' }, 'Client ID', cid), h('label', { class: 'f' }, 'Consumer Key', key),
          h('div', {}, h('button', { class: 'btn primary', text: 'Save keys', onclick: async () => {
            try { await api('/api/snaptrade/keys', { json: { client_id: cid.value, consumer_key: key.value } }); delete box.dataset.editKeys; again(); }
            catch (e) { msg.textContent = e.message; }
          } }))), msg);
      return;
    }
    const linkSlot = h('div', { class: 'chips' });
    const connectBtn = (label, broker) => h('button', { class: 'btn', text: label, onclick: async (e) => {
      e.target.disabled = true; msg.textContent = 'Getting a sign-in link';
      try {
        const r = await api('/api/snaptrade/connect', { json: { broker } });
        msg.textContent = '';
        fill(linkSlot, h('button', { class: 'btn primary', type: 'button', text: 'Open SnapTrade to sign in', onclick: () => openLink(r.url) }),
          h('span', { class: 'faint', style: 'font-size:12.5px;align-self:center', text: 'The link works for 5 minutes. Come back and tap Sync when done.' }));
      } catch (er) { msg.textContent = er.message; }
      e.target.disabled = false;
    } });
    const acctSel = (row) => h('select', { onchange: async (e) => { await api('/api/snaptrade/map', { json: { snap_id: row.snap_id, account_id: e.target.value } }); after(); } },
      sd.accounts.map((a) => h('option', { value: a.id, text: a.name, selected: a.id === row.account_id })));
    const conns = [...new Set(d.linked.map((r) => r.authorization_id).filter(Boolean))];
    fill(box, title,
      h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: `Key ending ${d.key_tail} saved. ` + (d.last_sync ? `Last sync ${d.last_sync.replace('T', ' ')}. Syncs every hour in market hours, every 6 hours otherwise. Transactions arrive a day late (SnapTrade refreshes them daily); balances and positions are live.` : 'Not synced yet.') }),
      h('div', { class: 'chips' }, connectBtn('Link a brokerage', null),
        h('button', { class: 'btn', text: 'Sync now', onclick: async (e) => {
          e.target.disabled = true; msg.textContent = 'Syncing (can take a minute)';
          try { const r = await api('/api/snaptrade/sync', { json: {} }); ST.snapMsg = `Synced ${r.accounts} account(s), ${r.new_rows} new transactions.` + (r.errors.length ? ' Problems: ' + r.errors.join('; ') : ''); await after(); }
          catch (er) { msg.textContent = er.message; e.target.disabled = false; }
        } }),
        h('button', { class: 'linkish', text: 'Replace keys', onclick: () => { box.dataset.editKeys = '1'; again(); } })),
      linkSlot, msg, d.last_error ? h('p', { class: 'down', style: 'font-size:13px', text: d.last_error }) : null,
      d.linked.length ? h('div', { style: 'margin-top:12px' }, table([
        { label: 'Broker account', get: (r) => h('div', {}, h('div', { text: `${r.institution} ${r.name || ''}` }), h('div', { class: 'faint', style: 'font-size:12px', text: (r.number || '') + (r.last_error ? '  ' + r.last_error : '') })) },
        { label: 'Shows as', get: (r) => acctSel(r) },
        { label: 'Synced', r: true, m: true, get: (r) => (r.synced_at ? r.synced_at.slice(5, 16).replace('T', ' ') : '-') }], d.linked)) : null,
      d.diffs.length && d.suspicious ? h('p', { class: 'warn', style: 'font-size:13.5px;margin-top:14px', text: `The broker's list differs on ${d.diffs.length} holdings, which looks like an incomplete answer from SnapTrade rather than real differences. Matching is off until a sync looks normal.` }) : null,
      d.diffs.length && !d.suspicious ? h('div', { style: 'margin-top:14px' }, h('h3', { text: 'Share counts that differ from the broker' }),
        h('p', { class: 'faint', style: 'font-size:12.5px;margin:0 0 6px', text: 'A trade from today shows up here until SnapTrade sends its transaction tomorrow; match only differences that last.' }), table([
        { label: 'Account', get: (r) => r.account }, { label: 'Holding', get: (r) => h('span', { class: 'sym', text: r.symbol }) },
        { label: 'Console', r: true, get: (r) => qty(r.ours) }, { label: 'Broker', r: true, get: (r) => qty(r.theirs) },
        { label: '', r: true, get: (r) => h('button', { class: 'btn small', text: 'Match', onclick: async () => { await api('/api/snaptrade/match', { json: { symbol: r.symbol } }); after(); } }) }], d.diffs),
        h('div', { style: 'margin-top:8px' }, h('button', { class: 'btn small', text: 'Match all', onclick: async () => { await api('/api/snaptrade/match', { json: {} }); after(); } }))) : null,
      conns.length ? h('div', { style: 'margin-top:12px' }, conns.map((c) => h('button', { class: 'btn small danger', text: 'Disconnect ' + (d.linked.find((r) => r.authorization_id === c) || {}).institution, onclick: async () => {
        if (!confirm('Remove this brokerage connection at SnapTrade? Its history stays here.')) return;
        await api('/api/snaptrade/disconnect', { json: { authorization_id: c } }).catch((e) => alert(e.message)); again();
      } }))) : null);
  }

  async function notifCard(box) {
    const d = await api('/api/push').catch((e) => ({ error: e.message }));
    if (d.error) { fill(box, h('h2', { text: 'Phone alerts' }), h('p', { class: 'down', text: d.error })); return; }
    const a = d.alerts, msg = h('div', { class: 'msg' });
    const can = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window;
    const keyBytes = (b64) => { const p = (b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'); return Uint8Array.from(atob(p), (c) => c.charCodeAt(0)); };
    const turnOn = async () => {
      try {
        if ((await Notification.requestPermission()) !== 'granted') { msg.textContent = 'Notifications are blocked for this app in the phone\u2019s settings.'; return; }
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(d.public_key) });
        const label = /iPhone/.test(navigator.userAgent) ? 'iPhone' : /iPad/.test(navigator.userAgent) ? 'iPad' : /Android/.test(navigator.userAgent) ? 'Android' : 'Computer';
        await api('/api/push/subscribe', { json: { subscription: sub.toJSON(), label } });
        notifCard(box);
      } catch (e) { msg.textContent = e.message; }
    };
    const chk = (k, text) => h('label', { style: 'display:flex;gap:8px;align-items:center;font-size:14px' },
      h('input', { type: 'checkbox', checked: !!a[k], onchange: (e) => api('/api/alerts', { json: { [k]: e.target.checked } }) }), text);
    const thr = h('input', { type: 'number', value: a.margin_threshold, style: 'width:100px', onchange: (e) => api('/api/alerts', { json: { margin_threshold: e.target.value } }) });
    const mv = h('input', { type: 'number', step: '0.5', value: a.big_move_pct, style: 'width:80px', onchange: (e) => api('/api/alerts', { json: { big_move_pct: e.target.value } }) });
    fill(box, h('h2', { text: 'Phone alerts' }),
      can ? h('button', { class: 'btn primary', text: 'Turn on for this device', onclick: turnOn })
        : h('p', { class: 'dim', style: 'font-size:13.5px;margin-top:0', text: 'Alerts need the https address. On the iPhone, open the app from the Home Screen icon, then turn them on here.' }),
      msg,
      d.devices.length ? h('div', { class: 'rows', style: 'margin:10px 0' }, d.devices.map((v) => h('div', { class: 'row' },
        h('div', { class: 'l' }, h('div', { text: v.label }), h('div', { class: 'faint', style: 'font-size:12px', text: v.last_error ? 'Last error: ' + v.last_error : v.last_ok ? 'Last alert ' + v.last_ok.replace('T', ' ') : 'No alerts yet' })),
        h('div', { class: 'chips' }, h('button', { class: 'btn small', text: 'Test', onclick: async () => { const r = await api('/api/push/test', { json: { id: v.id } }); msg.textContent = r.sent ? 'Sent.' : 'Not delivered; see the error above.'; notifCard(box); } }),
          h('button', { class: 'btn small danger', text: 'Remove', onclick: async () => { await api('/api/push/remove', { json: { id: v.id } }); notifCard(box); } }))))) : null,
      h('div', { class: 'form', style: 'margin-top:10px' },
        chk('monday_menu', 'The options menu, Monday at 9:45 (Eastern)'),
        h('div', { class: 'line' }, chk('margin', 'Margin cushion under'), thr),
        h('div', { class: 'line' }, h('span', { style: 'font-size:14px', text: 'A day up or down more than' }), mv, h('span', { style: 'font-size:14px', text: '%' }))),
      d.macro.length ? h('p', { class: 'note', text: 'Coming Fed meetings (the menu warns that week): ' + d.macro.filter((x) => x.label === 'FOMC decision').map((x) => shortDate(x.d)).join(', ') + '. CPI and jobs days go in the rules box.' }) : null);
  }

  // ---- symbol sheet -------------------------------------------------------------------------------------------
  const sheet = $('#sheet'), back = $('#sheetBack');
  function closeSheet() { sheet.hidden = true; back.hidden = true; hideTip(); }
  back.addEventListener('click', closeSheet);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });
  function openSheet(...kids) {
    fill(sheet, h('button', { class: 'btn small close', text: 'Close', onclick: closeSheet }), ...kids);
    sheet.hidden = false; back.hidden = false; sheet.scrollTop = 0;
  }
  function openMore() {
    openSheet(h('h2', { text: 'More' }), h('div', { class: 'more-list' },
      [['/analysis', 'Analysis'], ['/market', 'Market'], ['/risk', 'Margin'], ['/assets', 'Assets dashboard'], ['/doom', 'Doom dashboard'], ['/activity', 'Activity'], ['/settings', 'Data and settings'], ['/customize', 'Customize']].map(([href, t]) => h('a', { href, text: t }))));
  }
  async function openSymbol(sym) {
    openSheet(h('div', { class: 'loading', text: 'Loading ' + sym }));
    const d = await api('/api/symbol?s=' + encodeURIComponent(sym)).catch((e) => ({ error: e.message }));
    if (d.error) { openSheet(h('div', { class: 'alert bad', text: d.error })); return; }
    const p = d.position || {}, q = d.quote || {}, prof = d.profile || {}, inc = d.income_projection || {};
    const chartBox = h('div', { class: 'chart' });
    const lots = (p.lots || []).filter((l) => l.qty > 1e-6);
    const held = (p.accounts || []).filter((a) => a.qty > 1e-9), unit = p.class === 'Crypto' ? 'coins' : 'shares';
    openSheet(
      h('h1', { text: sym }), h('p', { class: 'sub', text: [p.name || q.longName, prof.sector_name, prof.industry || prof.category].filter(Boolean).join(' · ') }),
      h('div', { class: 'grid g3' },
        h('div', { class: 'card tile' }, h('div', { class: 'label', text: 'Price' }), h('div', { class: 'val ' + tone(q.regularMarketChangePercent), text: price(q.regularMarketPrice || p.price) }),
          h('div', { class: 'small', text: spct((q.regularMarketChangePercent || 0) / 100, 2) + ' today' }),
          isNum(p.from_ath) ? h('div', { class: 'small', text: p.from_ath > -0.005 ? `At its all-time high (${price(p.ath)})` : `${pct(-p.from_ath, 1)} under its all-time high of ${price(p.ath)} (${shortDate(p.ath_date)})` }) : null),
        tile(unit === 'coins' ? 'Coins you hold' : 'Shares you hold', isNum(p.qty) ? qty(p.qty) : '0', '', !held.length ? 'none held now'
          : held.length === 1 ? `in ${held[0].account}${lots.length > 1 ? `, ${lots.length} lots` : ''}` : held.map((a) => `${qty(a.qty)} in ${a.account}`).join(', ')),
        tile('Value', money(p.value, 0), '', isNum(p.weight) ? `${pct(p.weight)} of holdings` : ''),
        tile('Total gain', isNum(p.gain) ? (CENSOR ? spct(p.gain_pct, 1) : smoney(p.gain, 0)) : 'basis unknown', tone(p.gain), (isNum(p.avg_cost) ? `avg cost ${price(p.avg_cost)}` : '') + (isNum(p.vs_m2) ? `${isNum(p.avg_cost) ? ', ' : ''}${m2Word(p.vs_m2)}` : '')),
        tile('Income / yr', money(inc.annual || 0, 0), '', inc.yield ? `yield ${pct(inc.yield, 2)}` : 'no dividend')),
      h('section', { class: 'block' }, card('Price, last year, with your buys and sells', h('div', { class: 'legend' }, h('span', {}, mkKey('var(--s-blue)'), 'Price'), h('span', {}, mkKey('var(--up)', true), 'Buy'), h('span', {}, mkKey('var(--down)', true), 'Sell')), chartBox)),
      lots.length ? h('section', { class: 'block' }, card(`Lots you hold: ${qty(p.qty)} ${unit} in ${lots.length} lot${lots.length === 1 ? '' : 's'}`, table([
        { label: 'Acquired', get: (l) => shortDate(l.date) }, { label: 'How', get: (l) => l.src }, { label: 'Shares', r: true, get: (l) => qty(l.qty) },
        { label: 'Cost', r: true, get: (l) => (isNum(l.cost) ? money(l.cost) : 'unknown') }, { label: 'Per share', r: true, get: (l) => (isNum(l.cost) ? price(l.cost / l.qty) : '') }], lots))) : null,
      h('section', { class: 'block' }, card('Statement log', actTable(d.activity))));
    const bars = d.bars.slice(-260);
    const trades = d.activity.filter((a) => a.kind === 'trade' && a.date >= (bars[0] || [''])[0]);
    requestAnimationFrame(() => lineChart(chartBox, { dates: bars.map((b) => b[0]), series: [{ name: 'Close', color: 'var(--s-blue)', values: bars.map((b) => b[1]) }], fmt: (v) => price(v), yfmt: (v, step) => price(v, Math.min(2, stepDec(step))), height: 240,
      markers: trades.slice(0, 120).map((t) => ({ date: t.date, value: t.price, color: t.qty && t.amount < 0 ? 'var(--up)' : 'var(--down)', label: t.amount < 0 ? 'Bought' : 'Sold', text: `${qty(t.qty)} @ ${price(t.price)}` })) }));
  }
  function mkKey(color, dot) { const i = h('i', { class: dot ? 'box' : '' }); i.style.background = color; if (dot) i.style.borderRadius = '50%'; return i; }

  // ---- start --------------------------------------------------------------------------------------------------
  let timer = null;
  function schedule() {
    clearTimeout(timer);
    if (document.hidden) return;
    const session = ST.model && ST.model.sync && ST.model.sync.session;
    timer = setTimeout(async () => {
      if (!document.hidden && sheet.hidden && ['home', 'positions'].includes(ST.page) && !document.activeElement.matches('input,select,textarea')) { await load(); render(); }
      schedule();
    }, session === 'regular' || (['Live', '1D'].includes(ST.range) && session !== 'closed') ? 30000 : 300000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { load().then(() => { if (sheet.hidden && ['home', 'positions'].includes(ST.page)) render(); }); } schedule(); });
  let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (sheet.hidden && !['settings', 'activity', 'customize'].includes(ST.page)) render(); }, 200); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  load().then(() => { go(location.pathname, false); schedule(); });
})();
