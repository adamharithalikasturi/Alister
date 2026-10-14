'use strict';
const PARENT_STYLE = 'Metafield: custom.masterstyle [single_line_text_field]';
const NONE = 'Unmapped / Ignore';
const LS_PROFILES = 'matrixify.profiles.v1';
const LS_CURRENT = 'matrixify.current.v1';

const DEFAULTS = {
  header_row: 1, repair_html: true, strip_rs_barcode: true, normalize_sizing: true, pdp_to_list: true,
  style_cleanup: { mode: 'smart', count: 1, pattern: '\\d$' },
  handle: { enabled: true, pattern: '{title}-{parent_sku}', parent_sku_source: 'auto', parent_sku_strip_regex: '',
            sku_case: 'keep', vn_to_van: true, overwrite_existing: false },
};
const clone = o => JSON.parse(JSON.stringify(o));
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  config: clone(DEFAULTS),
  maps: { copy: {}, sap: {}, img: {} },
  files: { template: null, copy: [], sap: null, image: null },
  scan: { template: null, copy: {}, sap: null, image: null },
  result: null, running: false, auditFilter: 'all',
};

/* ---------- utilities ---------- */
function toast(msg, ms = 3500) {
  const t = $('#toast'); t.textContent = msg; t.style.display = 'block';
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.style.display = 'none'), ms);
}
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) { const ks = p.split('.'); const last = ks.pop(); ks.reduce((a, k) => a[k], o)[last] = v; }
async function api(path, form) {
  const r = await fetch(path, { method: 'POST', body: form });
  if (!r.ok) {
    let d = r.statusText; try { d = (await r.json()).detail || d; } catch (_) {}
    throw new Error(r.status === 413 ? 'Upload is too large for one request (limit about 4.5 MB combined).' : d);
  }
  return r.json();
}
const targets = () => state.scan.template?.targets || [];

/* ---------- settings binding ---------- */
function applyConfigToDom() {
  $$('[data-k]').forEach(el => {
    const v = getPath(state.config, el.dataset.k);
    if (el.type === 'checkbox') el.checked = !!v; else if (v !== undefined) el.value = v;
  });
  refreshModeUI(); refreshHandleSources();
}
function refreshModeUI() {
  const m = state.config.style_cleanup.mode;
  $$('[data-show-mode]').forEach(el => (el.hidden = el.dataset.showMode !== m));
  $('#sc-hint').textContent = {
    smart: 'Removes one trailing digit, only when the code ends in a digit and is not already a copy deck style.',
    legacy: 'Removes the last character of every code, as the original version did.',
    fixed: 'Removes exactly this many characters from the end.',
    regex: 'Removes the first match of the pattern. Anchor it with $ to target the end.',
    off: 'Master style codes are used exactly as exported.',
  }[m];
}
function mappedTargets() {
  const s = new Set();
  Object.values(state.maps.copy).forEach(f => Object.values(f).forEach(t => t !== NONE && s.add(t)));
  [state.maps.sap, state.maps.img].forEach(m => Object.values(m).forEach(t => t !== NONE && s.add(t)));
  return [...s];
}
function refreshHandleSources() {
  const sel = $('#h-src'); const cur = state.config.handle.parent_sku_source;
  const opts = ['auto', ...mappedTargets()]; if (!opts.includes(cur)) opts.push(cur);
  sel.innerHTML = opts.map(o => `<option value="${esc(o)}">${o === 'auto' ? 'Auto-detect' : esc(o)}</option>`).join('');
  sel.value = cur;
}
$$('[data-k]').forEach(el => el.addEventListener('input', () => {
  let v = el.type === 'checkbox' ? el.checked : el.value;
  if (el.dataset.type === 'int') v = parseInt(v, 10) || 1;
  setPath(state.config, el.dataset.k, v);
  if (el.dataset.k === 'style_cleanup.mode') refreshModeUI();
  persist(); runTester(); renderReady();
}));

/* ---------- persistence and profiles ---------- */
const snapshot = () => ({ version: 1, settings: clone(state.config), mappings: clone(state.maps) });
function persist() { try { localStorage.setItem(LS_CURRENT, JSON.stringify(snapshot())); } catch (_) {} }
function loadSnapshot(s) {
  const merged = clone(DEFAULTS);
  Object.entries(s.settings || {}).forEach(([k, v]) => {
    if (typeof merged[k] === 'object' && typeof v === 'object') Object.assign(merged[k], v); else if (k in merged) merged[k] = v;
  });
  state.config = merged;
  state.maps = { copy: s.mappings?.copy || {}, sap: s.mappings?.sap || {}, img: s.mappings?.img || {} };
  applyConfigToDom(); renderMapping(); runTester(); persist();
}
function importLegacy(obj) {  // keys written by the Streamlit version: map_sap_X, map_img_X, map_<file>_<col>
  const m = { copy: {}, sap: {}, img: {} };
  Object.entries(obj).forEach(([k, v]) => {
    k = k.replace(/\u200b/g, '').trim();
    if (k.startsWith('map_sap_')) m.sap[k.slice(8)] = v;
    else if (k.startsWith('map_img_')) m.img[k.slice(8)] = v;
    else if (k.startsWith('map_')) {
      const f = state.files.copy.find(x => k.startsWith(`map_${x.name}_`));
      if (f) (m.copy[f.name] ||= {})[k.slice(`map_${f.name}_`.length)] = v;
    }
  });
  return { version: 1, settings: state.config, mappings: m };
}
const profiles = () => { try { return JSON.parse(localStorage.getItem(LS_PROFILES) || '{}'); } catch (_) { return {}; } };
function renderProfiles() {
  const p = profiles(); const names = Object.keys(p);
  $('#p-list').innerHTML = names.length ? names.map(n => `<option>${esc(n)}</option>`).join('') : '<option value="">No saved profiles</option>';
}
$('#p-save').onclick = () => {
  const n = $('#p-name').value.trim(); if (!n) return toast('Enter a profile name first.');
  const p = profiles(); p[n] = snapshot(); localStorage.setItem(LS_PROFILES, JSON.stringify(p));
  renderProfiles(); $('#p-list').value = n; $('#profilePill').textContent = n; toast(`Saved profile "${n}".`);
};
$('#p-load').onclick = () => {
  const n = $('#p-list').value; const p = profiles()[n]; if (!p) return;
  loadSnapshot(p); $('#profilePill').textContent = n; toast(`Loaded profile "${n}".`);
};
$('#p-del').onclick = () => {
  const n = $('#p-list').value; const p = profiles(); if (!p[n]) return;
  delete p[n]; localStorage.setItem(LS_PROFILES, JSON.stringify(p)); renderProfiles(); toast(`Deleted "${n}".`);
};
$('#p-export').onclick = () => download(new Blob([JSON.stringify(snapshot(), null, 2)], { type: 'application/json' }), `${$('#p-name').value.trim() || 'matrixify-profile'}.json`);
$('#p-reset').onclick = () => { loadSnapshot({ settings: DEFAULTS, mappings: { copy: {}, sap: {}, img: {} } }); $('#profilePill').textContent = 'Unsaved settings'; toast('Settings reset.'); };
$('#p-import').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  try {
    const obj = JSON.parse(await f.text());
    loadSnapshot(obj.settings || obj.mappings ? obj : importLegacy(obj));
    toast('Profile imported.');
  } catch (err) { toast('That file is not a valid profile.'); }
  e.target.value = '';
};

/* ---------- file intake and scanning ---------- */
function renderFiles() {
  const mk = (f, ok = true, key, i) => `<div class="chip ${ok ? 'ok' : 'err'}"><span>${esc(f.name)}</span><button data-rm="${key}" data-i="${i ?? ''}" title="Remove" aria-label="Remove ${esc(f.name)}">remove</button></div>`;
  $('[data-drop=template] .files').innerHTML = state.files.template ? mk(state.files.template, true, 'template') : '';
  $('[data-drop=copy] .files').innerHTML = state.files.copy.map((f, i) => mk(f, true, 'copy', i)).join('');
  $('[data-drop=sap] .files').innerHTML = state.files.sap ? mk(state.files.sap, true, 'sap') : '';
  $('[data-drop=image] .files').innerHTML = state.files.image ? mk(state.files.image, true, 'image') : '';
  $$('[data-rm]').forEach(b => b.onclick = ev => {
    ev.preventDefault(); const { rm, i } = b.dataset;
    if (rm === 'copy') { const [f] = state.files.copy.splice(+i, 1); delete state.scan.copy[f.name]; }
    else { state.files[rm] = null; state.scan[rm] = null; }
    renderFiles(); renderMapping(); renderReady();
  });
}
async function scanOne(kind, file) {
  const fd = new FormData(); fd.append('kind', kind); fd.append('file', file);
  fd.append('header_row', state.config.header_row); fd.append('targets', JSON.stringify(targets()));
  return api('/api/scan', fd);
}
async function ingest(kind, list) {
  try {
    if (kind === 'template') {
      state.files.template = list[0]; state.scan.template = await scanOne('template', list[0]);
      await Promise.all(state.files.copy.map(async f => (state.scan.copy[f.name] = await scanOne('copy', f))));
      for (const k of ['sap', 'image']) if (state.files[k]) state.scan[k] = await scanOne(k, state.files[k]);
    } else if (kind === 'copy') {
      for (const f of list) { if (!state.files.copy.some(x => x.name === f.name)) state.files.copy.push(f); state.scan.copy[f.name] = await scanOne('copy', f); }
    } else { state.files[kind] = list[0]; state.scan[kind] = await scanOne(kind, list[0]); }
    if (kind !== 'template' && !state.scan.template) toast('Upload the template to enable mapping.');
  } catch (e) { toast(e.message, 6000); }
  renderFiles(); renderMapping(); renderReady();
}
$$('.drop').forEach(d => {
  const kind = d.dataset.drop; const inp = $('input', d);
  inp.onchange = () => { if (inp.files.length) ingest(kind, [...inp.files]); inp.value = ''; };
  ['dragover', 'dragenter'].forEach(e => d.addEventListener(e, ev => { ev.preventDefault(); d.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(e => d.addEventListener(e, () => d.classList.remove('over')));
  d.addEventListener('drop', ev => { ev.preventDefault(); if (ev.dataTransfer.files.length) ingest(kind, [...ev.dataTransfer.files]); });
});

/* ---------- mapping UI ---------- */
function mapTable(columns, getT, setT, linkFn) {
  const wrap = document.createElement('div');
  const q = Object.assign(document.createElement('input'), { type: 'text', placeholder: 'Filter columns' });
  q.style.maxWidth = '220px';
  const suggest = Object.assign(document.createElement('button'), { className: 'btn sm', textContent: 'Suggest matches' });
  const clear = Object.assign(document.createElement('button'), { className: 'btn sm', textContent: 'Clear all' });
  const tools = document.createElement('div'); tools.style.cssText = 'display:flex;gap:8px;margin-bottom:10px'; tools.append(q, suggest, clear);
  const tw = document.createElement('div'); tw.className = 'tbl-wrap';
  const rows = columns.map(c => `<tr data-n="${esc(c.name.toLowerCase())}"><td><code>${esc(c.name)}</code></td><td title="${esc(c.sample)}">${esc(c.sample)}</td><td>${c.sheet ? esc(c.sheet) : ''}</td><td><input type="text" list="targets" data-col="${esc(c.name)}" placeholder="${NONE}" value="${esc(getT(c.name) === NONE ? '' : getT(c.name))}"></td></tr>`).join('');
  tw.innerHTML = `<table class="map"><thead><tr><th>Source column</th><th>Sample value</th><th>Sheet</th><th>Template column</th></tr></thead><tbody>${rows}</tbody></table>`;
  wrap.append(tools, tw);
  const paint = () => $$('tbody tr', tw).forEach(tr => tr.classList.toggle('mapped', !!$('input', tr).value));
  paint();
  q.oninput = () => $$('tbody tr', tw).forEach(tr => (tr.hidden = !tr.dataset.n.includes(q.value.toLowerCase())));
  tw.addEventListener('change', e => {
    const inp = e.target; if (!inp.dataset.col) return;
    let v = inp.value.trim();
    if (v && !targets().some(t => t === v)) {
      const hit = targets().find(t => t.toLowerCase() === v.toLowerCase());
      if (hit) v = hit; else { toast('Pick a column from the template list.'); inp.value = getT(inp.dataset.col) === NONE ? '' : getT(inp.dataset.col); return; }
      inp.value = v;
    }
    setT(inp.dataset.col, v || NONE); paint(); onMapChange();
  });
  suggest.onclick = () => {
    let n = 0;
    columns.forEach(c => { if (c.suggest && getT(c.name) === NONE) { setT(c.name, c.suggest); n++; } });
    toast(n ? `Suggested ${n} mapping${n > 1 ? 's' : ''}. Review before running.` : 'No confident matches found.'); renderMapping();
  };
  clear.onclick = () => { columns.forEach(c => setT(c.name, NONE)); renderMapping(); onMapChange(); };
  return wrap;
}
function card(title, badge, bodyEl) {
  const c = document.createElement('div'); c.className = 'card';
  c.innerHTML = `<header><h3>${title}</h3><span class="sp"></span>${badge}</header><div class="pad"></div>`;
  $('.pad', c).append(bodyEl); return c;
}
const badge = (ok, yes, no, warnClass = 'warn') => ok ? `<span class="badge ok">${yes}</span>` : `<span class="badge ${warnClass}">${no}</span>`;
function onMapChange() { persist(); refreshHandleSources(); renderReady(); }

function renderMapping() {
  const root = $('#map-root'); const tpl = state.scan.template;
  if (!tpl) { root.innerHTML = '<div class="empty"><b>Upload a Matrixify template to begin</b>The template defines the columns available for mapping.</div>'; return; }
  root.innerHTML = '';
  let dl = $('#targets'); if (!dl) { dl = document.createElement('datalist'); dl.id = 'targets'; document.body.append(dl); }
  dl.innerHTML = tpl.targets.map(t => `<option value="${esc(t)}">`).join('');

  // template header row
  const th = document.createElement('div');
  const maxIdx = Math.max(0, tpl.rows_available - 1);
  th.innerHTML = `<div class="tbl-wrap" style="max-height:170px"><table>${tpl.preview.map((r, i) => `<tr><td class="mono" style="color:var(--mute)">${i}</td>${r.slice(0, 8).map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>
    <div class="field" style="max-width:260px;margin-top:10px"><label for="hr">Header row (0-based)</label><input id="hr" type="number" min="0" max="${maxIdx}" value="${state.config.header_row}"></div>
    <p class="hint">The row holding column names such as Handle and Title. ${tpl.targets.length} template columns found.</p>`;
  $('#hr', th).onchange = async e => {
    state.config.header_row = Math.min(maxIdx, Math.max(0, +e.target.value || 0)); persist();
    state.scan.template = await scanOne('template', state.files.template); renderMapping(); renderReady();
  };
  root.append(card('Template header', badge(true, `${tpl.targets.length} columns`, ''), th));

  // copy decks
  const names = state.files.copy.map(f => f.name);
  if (names.length) {
    const holder = document.createElement('div');
    const sel = Object.assign(document.createElement('select'), { style: 'max-width:340px;margin-bottom:10px' });
    sel.innerHTML = names.map(n => `<option>${esc(n)}</option>`).join('');
    const body = document.createElement('div'); holder.append(sel, body);
    const show = () => {
      const f = sel.value; const cols = state.scan.copy[f] || []; const m = (state.maps.copy[f] ||= {});
      body.innerHTML = ''; body.append(mapTable(cols, c => m[c] || NONE, (c, v) => (m[c] = v)));
    };
    sel.onchange = show; show();
    const linked = names.some(f => Object.values(state.maps.copy[f] || {}).some(t => t === PARENT_STYLE || /sku|id/i.test(t || '')));
    root.append(card('Copy decks', badge(linked, 'Linked to style or SKU', 'No style or SKU column mapped'), holder));
  } else root.append(card('Copy decks', '<span class="badge">Optional</span>', Object.assign(document.createElement('p'), { className: 'hint', textContent: 'Upload copy decks in the sidebar to map their columns.' })));

  // SAP + images
  [['sap', 'SAP / primary data', 'Linked: drives row iteration', 'Map a column to the master style field'],
   ['image', 'Image export', 'Linked: images attach by style', 'Optional: map a style column to attach images']].forEach(([k, title, yes, no]) => {
    const mk = k === 'image' ? 'img' : 'sap'; const sc = state.scan[k];
    if (!sc) return root.append(card(title, '<span class="badge">Not uploaded</span>', Object.assign(document.createElement('p'), { className: 'hint', textContent: 'Upload the file in the sidebar to map its columns.' })));
    const linked = Object.values(state.maps[mk]).includes(PARENT_STYLE);
    const body = mapTable(sc.columns, c => state.maps[mk][c] || NONE, (c, v) => (state.maps[mk][c] = v));
    root.append(card(`${title} <span class="hint">(${sc.rows.toLocaleString()} rows)</span>`, badge(linked, yes, no, k === 'sap' ? 'bad' : 'warn'), body));
  });
}

/* ---------- readiness and rule tester ---------- */
function checks() {
  const sapLinked = Object.values(state.maps.sap).includes(PARENT_STYLE);
  return [
    ['Template uploaded', !!state.scan.template, true],
    ['SAP file uploaded', !!state.scan.sap, false],
    ['SAP linked to master style', sapLinked, false],
    ['Copy decks (optional)', state.files.copy.length > 0, null],
    ['Image export (optional)', !!state.files.image, null],
  ];
}
function renderReady() {
  const c = checks();
  $('#ready').innerHTML = c.map(([l, ok, req]) => `<div class="${ok ? 'y' : req === null ? '' : 'w'}">${l}</div>`).join('');
  $('#run').disabled = state.running || !state.scan.template || (!state.scan.sap && !state.files.copy.length);
}
let testTimer;
function runTester() {
  clearTimeout(testTimer);
  testTimer = setTimeout(async () => {
    try {
      const r = await fetch('/api/test-rules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: state.config, value: $('#t-style').value, title: $('#t-title').value, parent_sku: $('#t-psku').value }) });
      const d = await r.json();
      $('#t-out').innerHTML = `<i>Cleaned style</i> ${esc(d.style)}<br><i>Handle</i> ${esc(d.handle)}`;
    } catch (_) { $('#t-out').textContent = ''; }
  }, 250);
}
['#t-style', '#t-title', '#t-psku'].forEach(s => $(s).addEventListener('input', runTester));

/* ---------- run pipeline ---------- */
$('#run').onclick = async () => {
  if (!checks()[2][1] && state.scan.sap && !confirm('The SAP file is not linked to the master style field, so copy decks and images will not match. Run anyway?')) return;
  state.running = true; renderReady(); $('#runPill').textContent = 'Running'; $('#runPill').classList.add('live');
  const fd = new FormData();
  fd.append('config', JSON.stringify(state.config));
  fd.append('mappings', JSON.stringify({ copy: state.maps.copy, sap: clean(state.maps.sap), img: clean(state.maps.img) }));
  fd.append('template', state.files.template);
  state.files.copy.forEach(f => fd.append('copy_decks', f));
  if (state.files.sap) fd.append('sap', state.files.sap);
  if (state.files.image) fd.append('image', state.files.image);
  try {
    state.result = await api('/api/process', fd);
    renderResult(); renderAudit(); renderStore(); renderExport(); showTab('result');
    toast(`Pipeline complete: ${state.result.total_rows.toLocaleString()} rows.`);
  } catch (e) { toast(e.message, 8000); }
  state.running = false; renderReady(); $('#runPill').textContent = 'Idle'; $('#runPill').classList.remove('live');
};
const clean = m => Object.fromEntries(Object.entries(m).filter(([, v]) => v && v !== NONE));

/* ---------- result views ---------- */
function showTab(n) {
  $$('.tab').forEach(t => t.setAttribute('aria-selected', t.dataset.tab === n));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `v-${n}`));
}
$$('.tab').forEach(t => (t.onclick = () => showTab(t.dataset.tab)));
function renderResult() {
  const r = state.result; $('#n-result').textContent = r.total_rows.toLocaleString();
  const shown = r.rows.slice(0, 200);
  $('#v-result').innerHTML = `<h2>Completed template</h2><p class="lede">${r.total_rows.toLocaleString()} rows across ${r.columns.length} columns. Showing the first ${shown.length}.</p>
    <div class="card"><div class="tbl-wrap" style="max-height:calc(100vh - 260px)"><table><thead><tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${shown.map(row => `<tr>${row.map(v => `<td>${esc(v).slice(0, 120)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}
function renderAudit() {
  const a = state.result.audit; const issues = x => x['Missing Title'] || x['Is Duplicate Style'] || !x['Copy Deck Integrated'];
  const count = f => a.filter(f).length; $('#n-audit').textContent = count(issues).toLocaleString();
  const list = state.auditFilter === 'issues' ? a.filter(issues) : a;
  const cols = ['Style Code', 'Raw Style', 'Style Changed', 'Title', 'Handle', 'Copy Deck Integrated', 'Images Matched', 'Is Duplicate Style', 'Missing Title'];
  $('#v-audit').innerHTML = `<h2>Data quality audit</h2><p class="lede">Raw Style shows the SAP value before cleanup, so every change is traceable.</p>
    <div class="metrics">
      ${[['SAP rows', a.length], ['Copy deck matched', count(x => x['Copy Deck Integrated'])], ['No copy deck match', count(x => !x['Copy Deck Integrated'])], ['Style codes cleaned', count(x => x['Style Changed'])], ['Duplicate styles', count(x => x['Is Duplicate Style'])], ['Missing titles', count(x => x['Missing Title'])]].map(([l, v]) => `<div class="metric"><div class="v">${v.toLocaleString()}</div><div class="l">${l}</div></div>`).join('')}
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px"><button class="btn sm ${state.auditFilter === 'all' ? 'dark' : ''}" data-af="all">All rows</button><button class="btn sm ${state.auditFilter === 'issues' ? 'dark' : ''}" data-af="issues">Issues only</button></div>
    <div class="card"><div class="tbl-wrap"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>${list.slice(0, 1000).map(x => `<tr>${cols.map(c => `<td class="${/Style|Handle/.test(c) ? 'mono' : ''}">${esc(typeof x[c] === 'boolean' ? (x[c] ? 'Yes' : 'No') : x[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
  $$('[data-af]').forEach(b => (b.onclick = () => { state.auditFilter = b.dataset.af; renderAudit(); }));
}
function renderStore() {
  const ps = state.result.products;
  if (!ps.length) { $('#v-store').innerHTML = '<div class="empty"><b>No products to preview</b>Map a column to Variant SKU to enable the storefront preview.</div>'; return; }
  $('#v-store').innerHTML = `<h2>Storefront preview</h2><p class="lede">Each product as it will read on the storefront, grouped by Variant SKU.</p>
    <div class="sf"><div class="card"><ul>${ps.map((p, i) => `<li><button data-p="${i}" aria-current="${i === 0}"><b>${esc(p.title || 'Untitled')}</b><br><span class="mono">${esc(p.sku)}</span></button></li>`).join('')}</ul></div>
    <div class="card"><iframe sandbox title="Product preview" id="sf-frame"></iframe></div></div>`;
  const show = i => {
    const p = ps[i]; $$('[data-p]').forEach(b => b.setAttribute('aria-current', b.dataset.p == i));
    const price = p.price && p.price !== 'nan' ? `$${esc(p.price)}` : 'Price not mapped';
    const pdp = p.pdp && p.pdp !== 'nan' ? p.pdp : '';
    $('#sf-frame').srcdoc = `<body style="font:15px/1.6 system-ui;margin:24px;color:#18232A">
      <div style="display:flex;gap:10px;overflow-x:auto;margin-bottom:16px">${p.images.map(u => `<img src="${esc(u)}" style="width:96px;height:96px;object-fit:cover;border:1px solid #CBD2D6" onerror="this.style.display='none'">`).join('')}</div>
      <h2 style="margin:0 0 4px">${esc(p.title || 'Untitled')}</h2><p style="color:#4A5A64;margin:0 0 16px">SKU ${esc(p.sku)}. ${price}</p>
      <h4 style="margin:0 0 6px">Description</h4><div>${p.body && p.body !== 'nan' ? p.body : 'No description'}</div>
      ${pdp ? `<h4 style="margin:20px 0 6px;border-top:1px solid #CBD2D6;padding-top:14px">Details</h4>${pdp}` : ''}</body>`;
  };
  $$('[data-p]').forEach(b => (b.onclick = () => show(+b.dataset.p))); show(0);
}
function download(blob, name) {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function renderExport() {
  const r = state.result; const issues = r.audit.filter(x => x['Missing Title'] || !x['Copy Deck Integrated']).length;
  $('#v-export').innerHTML = `<h2>Export</h2><p class="lede">Download the Matrixify import and the audit report.</p>
    ${issues ? `<div class="banner">${issues.toLocaleString()} SAP rows have no copy deck match or no title. Review the Audit tab before importing.</div>` : '<div class="banner ok">All SAP rows matched a copy deck and have a title.</div>'}
    <div style="display:flex;gap:12px"><button class="btn primary" id="dl-x">Download Matrixify import (.xlsx)</button><button class="btn" id="dl-a">Download audit report (.csv)</button></div>`;
  $('#dl-x').onclick = () => {
    const bin = atob(r.xlsx); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    download(new Blob([u], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), 'Matrixify_Import.xlsx');
  };
  $('#dl-a').onclick = () => {
    const cols = Object.keys(r.audit[0] || {}); const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    download(new Blob([[cols.map(q).join(','), ...r.audit.map(x => cols.map(c => q(x[c])).join(','))].join('\n')], { type: 'text/csv' }), 'Audit_Report.csv');
  };
}

/* ---------- boot ---------- */
(function boot() {
  try { const s = JSON.parse(localStorage.getItem(LS_CURRENT) || 'null'); if (s) loadSnapshot(s); else applyConfigToDom(); } catch (_) { applyConfigToDom(); }
  renderProfiles(); renderFiles(); renderReady(); runTester();
})();
