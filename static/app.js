/* Groundwork front end, part 1: helpers, routing, events, home, canvas, evidence, Ask Velo. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;
const STATUSES = [['proposed', 'Proposed'], ['approved', 'Approved'], ['in_progress', 'In progress'], ['shipped', 'Shipped'], ['measured', 'Measured']];
const VERDICTS = [['worked', 'Worked'], ['partial', 'Partly worked'], ['did_not_work', 'Did not work'], ['too_early', 'Too early to tell']];
const statusLabel = s => (STATUSES.find(x => x[0] === s) || [s, s])[1];
const verdictLabel = v => (VERDICTS.find(x => x[0] === v) || [v, v])[1];

const G = { // shared UI state
  views: {}, actions: {}, changes: {}, drops: {},
  evFilter: 'all', evUngrouped: false, composer: false,
  mapMode: 'map', themeFilter: 'all', traceAll: false,
  ask: [], dragging: null, token: 0, me: null,
};

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers = { 'Content-Type': 'application/json' }; o.body = JSON.stringify(o.body); }
  const r = await fetch('/api' + path, o);
  if (r.status === 401) { location.href = '/login'; throw new Error('Please log in to continue.'); }
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || 'Something went wrong. Try again.');
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
}
async function busy(btn, label, fn) {
  const old = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = label; }
  try { return await fn(); } catch (e) { toast(e.message); } finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}
async function copyText(text, msg = 'Copied') {
  try { await navigator.clipboard.writeText(text); toast(msg); } catch { toast('Copy failed. Select the text and copy it by hand.'); }
}
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
const basicNote = (what) => G.status && G.status.ai_mode === 'limited' ? `AI credits are used up this month, so this ${what} is basic.` : `Basic ${what} drafted. Turn on AI for a fuller one.`;
const lines = t => String(t || '').split('\n').map(x => x.trim()).filter(Boolean);

/* ---------- modal ---------- */
function modal({ title, fields, submit = 'Save', onSubmit, onDelete }) {
  const d = $('#dlg');
  d.innerHTML = `<form method="dialog">
    <h2>${esc(title)}</h2>
    ${fields.map(f => `<label class="field"><span>${esc(f.label)}</span>${f.type === 'textarea'
      ? `<textarea name="${f.name}" rows="${f.rows || 3}" placeholder="${esc(f.placeholder || '')}">${esc(f.value || '')}</textarea>`
      : `<input name="${f.name}" type="${f.type === 'password' ? 'password' : 'text'}" autocomplete="off" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}">`}</label>`).join('')}
    <div class="dlg-actions"><span>${onDelete ? '<button class="btn danger" type="button" id="dlg-del">Delete</button>' : ''}</span>
      <span class="actions"><button class="btn quiet" type="button" id="dlg-cancel">Cancel</button><button class="btn primary" type="submit">${esc(submit)}</button></span></div>
  </form>`;
  const form = $('form', d);
  $('#dlg-cancel').onclick = () => d.close();
  if (onDelete) $('#dlg-del').onclick = async () => { d.close(); await onDelete(); };
  form.onsubmit = async e => {
    e.preventDefault();
    const vals = Object.fromEntries(new FormData(form).entries());
    d.close();
    await onSubmit(vals);
  };
  d.showModal();
  const first = $('input, textarea', d); if (first) first.focus();
}

/* ---------- router ---------- */
function parseHash() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { view: parts[0] || 'home', id: parts[1] ? +parts[1] : null, tab: parts[2] || null };
}
const NAV_OF = { home: 'home', canvas: 'canvas', integrations: 'integrations', team: 'team', priorities: 'priorities', governance: 'governance', evidence: 'evidence', problems: 'problems', strategy: 'strategy', initiatives: 'initiatives', initiative: 'initiatives' };

async function refreshRail() {
  const s = await api('/status');
  G.status = s;
  if (!G.me) {
    const d = await api('/auth/me');
    if (!d.user) { location.href = '/login'; return s; }
    G.me = { ...d.user, api_token: d.api_token, workspace: d.workspace, workspaces: d.workspaces };
    $('#acct-name').textContent = G.me.name || G.me.email.split('@')[0];
    $('#acct-email').textContent = G.me.email;
    $('#ws-switch').innerHTML = d.workspaces.map(w => `<option value="${w.id}" ${w.id === d.workspace.id ? 'selected' : ''}>${esc(w.name)}</option>`).join('')
      + '<option value="new">+ New workspace</option>';
  }
  $('#n-evidence').textContent = s.evidence || '';
  $('#n-problems').textContent = s.problems || '';
  $('#n-themes').textContent = s.themes || '';
  $('#n-initiatives').textContent = s.initiatives || '';
  $('#ai-state').innerHTML = s.ai_mode === 'claude' ? `AI is on: <b>Claude</b>`
    : s.ai_mode === 'limited' ? `AI credits are <b>used up</b> for this month, so drafts are basic until they reset.`
    : `AI is off. Set <b>ANTHROPIC_API_KEY</b> and restart for full-quality results.`;
  return s;
}

async function render() {
  const token = ++G.token;
  const r = parseHash();
  const m = $('#main');
  $$('[data-nav]').forEach(a => a.toggleAttribute('aria-current', a.dataset.nav === NAV_OF[r.view]));
  $$('[data-nav]').forEach(a => { if (a.hasAttribute('aria-current')) a.setAttribute('aria-current', 'page'); });
  try {
    await refreshRail();
    const fn = G.views[r.view] || G.views.home;
    const html = await fn(r);
    if (token !== G.token) return;
    if (typeof html === 'string') m.innerHTML = (G.me && G.me.workspace.role === 'viewer' ? '<div class="banner"><span>You have <b>view-only</b> access to this workspace.</span></div>' : '') + html;
    if (G.after) { const f = G.after; G.after = null; f(); }
  } catch (e) {
    if (token !== G.token) return;
    m.innerHTML = `<div class="empty"><h2>This page did not load</h2><p>${esc(e.message)}</p><button class="btn" data-act="reload">Try again</button></div>`;
  }
}
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
document.addEventListener('click', e => { // clicking the page you are already on refreshes it
  const a = e.target.closest('a[href^="#/"]');
  if (a && a.getAttribute('href') === location.hash) render();
});

G.changes['ws-switch'] = async t => {
  if (t.value === 'new') {
    t.value = G.me.workspace.id;
    return modal({ title: 'New workspace', submit: 'Create workspace', fields: [{ name: 'name', label: 'Name', placeholder: 'For example, Payments team' }],
      onSubmit: async v => { await api('/auth/workspaces', { method: 'POST', body: { name: v.name } }); location.href = '/app#/home'; location.reload(); } });
  }
  await api(`/auth/workspaces/${t.value}/switch`, { method: 'POST' }); location.href = '/app#/home'; location.reload();
};

/* ---------- events ---------- */
document.addEventListener('click', e => {
  const t = e.target.closest('[data-act]'); if (!t) return;
  const fn = G.actions[t.dataset.act]; if (!fn) return;
  if (t.tagName === 'A' && !t.hasAttribute('download')) e.preventDefault();
  fn(t, e);
});
document.addEventListener('change', e => {
  const t = e.target.closest('[data-change]'); if (!t) return;
  const fn = G.changes[t.dataset.change]; if (fn) fn(t, e);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches && e.target.matches('textarea[data-oneline]')) { e.preventDefault(); e.target.blur(); return; }
  if (e.key === 'Escape' && $('#ask').classList.contains('open')) closeAsk();
  const n = e.target.closest && e.target.closest('.node[data-act]');
  if (n && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); G.actions[n.dataset.act](n, e); }
});
document.addEventListener('dragstart', e => {
  const d = e.target.closest('[data-drag]'); if (!d) return;
  G.dragging = d.dataset.drag; e.dataTransfer.setData('text/plain', G.dragging); e.dataTransfer.effectAllowed = 'move';
});
document.addEventListener('dragover', e => { const z = e.target.closest('[data-drop]'); if (z && G.dragging) { e.preventDefault(); z.classList.add('drop'); } });
document.addEventListener('dragleave', e => { const z = e.target.closest('[data-drop]'); if (z && !z.contains(e.relatedTarget)) z.classList.remove('drop'); });
document.addEventListener('drop', async e => {
  const z = e.target.closest('[data-drop]'); if (!z || !G.dragging) return;
  e.preventDefault(); z.classList.remove('drop');
  const [kind, id] = G.dragging.split(':'); const [zone, zid] = z.dataset.drop.split(':');
  G.dragging = null;
  const fn = G.drops[`${kind}>${zone}`]; if (fn) { try { await fn(id, zid); } catch (err) { toast(err.message); } }
});
document.addEventListener('dragend', () => { G.dragging = null; $$('.drop').forEach(x => x.classList.remove('drop')); });

/* ---------- common actions ---------- */
Object.assign(G.actions, {
  reload: () => render(),
  logout: async () => { await fetch('/api/auth/logout', { method: 'POST' }); location.href = '/'; },
  goto: t => go(t.dataset.href),
  'copy-text': t => copyText(t.dataset.text, t.dataset.msg || 'Copied'),
  seed: t => busy(t, 'Loading...', async () => { await api('/seed', { method: 'POST' }); toast('Demo workspace loaded'); go('#/home'); }),
  'reset-workspace': async () => {
    if (!confirm('Delete everything in this workspace? Evidence, problems, initiatives and outcomes will be removed.')) return;
    await api('/reset', { method: 'POST' }); toast('Workspace reset'); go('#/home');
  },
  analyze: t => busy(t, 'Analyzing...', async () => {
    const r = await api('/analyze', { method: 'POST', body: { reanalyze: false } });
    toast(`${plural(r.problems_created, 'problem')} found` + (r.remaining ? `. ${plural(r.remaining, 'item')} left: group again to continue` : '')); go('#/problems');
  }),
  regroup: t => busy(t, 'Regrouping...', async () => {
    if (!confirm('Regroup evidence into new problems? Problems that have initiatives are kept. Others are rebuilt and lose their theme.')) return;
    const r = await api('/analyze', { method: 'POST', body: { reanalyze: true } });
    toast(`${plural(r.problems_created, 'problem')} found`); go('#/problems');
  }),
  'gen-digest': t => busy(t, 'Reading your workspace...', async () => { await api('/insights/generate', { method: 'POST' }); render(); }),
  'dismiss-digest': async t => { await api(`/insights/${t.dataset.id}/dismiss`, { method: 'POST' }); render(); },
  insight: async (t) => {
    const view = t.dataset.view; const tg = JSON.parse(t.dataset.target || '{}');
    if (tg.action === 'analyze') return G.actions.analyze(t);
    if (view === 'initiative') return go(`#/initiative/${tg.initiative_id}/${tg.tab || 'spec'}`);
    if (view === 'problems' && tg.problem_id) return go(`#/problems/${tg.problem_id}`);
    go(`#/${view}`);
  },
});

/* ---------- home ---------- */
const INSIGHT_ICON = {
  attention: `<svg class="mark" viewBox="0 0 22 22" aria-label="Needs attention"><circle cx="11" cy="11" r="10" fill="var(--c-problem)"/><rect x="10" y="5.5" width="2" height="7" rx="1" fill="var(--raised)"/><circle cx="11" cy="15.6" r="1.3" fill="var(--raised)"/></svg>`,
  info: `<svg class="mark" viewBox="0 0 22 22" aria-label="Worth a look"><circle cx="11" cy="11" r="9" fill="none" stroke="var(--ink-3)" stroke-width="2"/><rect x="10" y="9.5" width="2" height="6" rx="1" fill="var(--ink-3)"/><circle cx="11" cy="6.6" r="1.2" fill="var(--ink-3)"/></svg>`,
  good: `<svg class="mark" viewBox="0 0 22 22" aria-label="Going well"><circle cx="11" cy="11" r="10" fill="var(--c-theme)"/><path d="M6.5 11.4l3.1 3.1 6-6.4" fill="none" stroke="var(--raised)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};
const ACT_COLOR = { initiative: 'var(--c-initiative)', dispatch: 'var(--c-story)', outcome: 'var(--c-outcome)', problem: 'var(--c-problem)' };
const actHref = a => a.kind === 'problem' ? `#/problems/${a.ref_id}` : `#/initiative/${a.ref_id}/trace`;

const KIND_TAG = { risk: ['stale', 'Risk'], opportunity: ['outcome', 'Opportunity'], gap: ['muted', 'Gap'] };

function digestHTML(dig) {
  const ai = G.status && G.status.ai_mode === 'claude';
  const head = `<div class="spread"><div><h2 class="section-title" style="margin:0">Digest</h2>
      <p class="small muted" style="margin:2px 0 0">${dig.run ? `${dig.run.mode === 'claude' ? 'Written by Claude' : 'Basic findings'}, ${ago(dig.run.created_at)}` : 'A few specific findings about your workspace, written on request or weekly.'}</p></div>
    <button class="btn small ${dig.run ? '' : 'primary'}" data-act="gen-digest">${dig.run ? 'Refresh' : 'Write a digest'}${ai ? ' (1 credit)' : ''}</button></div>`;
  const items = dig.items.map(i => `<div class="insight" style="grid-template-columns:auto 1fr auto">
      <span class="tag ${KIND_TAG[i.kind][0]}">${KIND_TAG[i.kind][1]}</span>
      <div><h3>${esc(i.title)}</h3><p>${esc(i.detail)}</p>${i.evidence_ids.length ? `<p class="small">${i.evidence_ids.map(id => `<a class="cite" href="#/evidence" title="Evidence #${id}">#${id}</a>`).join(' ')}</p>` : ''}</div>
      <button class="icon-btn" data-act="dismiss-digest" data-id="${i.id}" aria-label="Dismiss: ${esc(i.title)}">&#10005;</button></div>`).join('');
  return `<section style="margin-top:30px">${head}<div class="rows" style="margin-top:8px">${items || (dig.run ? '<p class="muted small" style="padding:12px 4px">Everything in this digest has been dismissed.</p>' : '')}</div></section>`;
}

G.views.home = async () => {
  const [d, dig] = await Promise.all([api('/home'), api('/insights')]); const s = d.stats; const p = d.product;
  if (!G.wizChecked && ['owner', 'admin'].includes(G.me.workspace.role) && s.evidence === 0 && !p.name) {
    G.wizChecked = true;   // offered once per page load, so "Skip setup" and the back button never trap anyone
    if (!(await api('/settings')).onboarded) { go('#/welcome'); return ''; }
  }
  const head = `<header class="page-head"><div>
      <h1>${esc(p.name || 'Welcome to Groundwork')}</h1>
      <p>${esc(p.overview || 'Trace every build back to what customers told you, and check whether it worked.')}</p></div>
    <div class="actions"><button class="btn" data-act="goto" data-href="#/evidence">Add evidence</button></div></header>`;
  if (s.evidence === 0) {
    return head + `<div class="empty"><h2>Start with what customers said</h2>
      <p>Paste interview notes, support tickets or reviews. Groundwork groups them into problems, turns the important ones into specs, and hands those to your coding agent.</p>
      <div class="actions"><button class="btn primary" data-act="goto" data-href="#/evidence">Add evidence</button><button class="btn" data-act="seed">Load a demo workspace</button></div></div>`;
  }
  const chain = [
    ['c-evidence', s.evidence, 'evidence', '#/evidence'], ['c-problem', s.problems, 'problems', '#/problems'],
    ['c-theme', s.themes, 'themes', '#/strategy'], ['c-initiative', s.initiatives, 'initiatives', '#/initiatives'],
    ['c-story', s.stories, 'stories', '#/initiatives'], ['c-outcome', s.worked, `worked, of ${s.measured} measured`, '#/initiatives'],
  ];
  const insights = d.insights.length ? d.insights.map(i => `<div class="insight">${INSIGHT_ICON[i.level]}
      <div><h3>${esc(i.title)}</h3><p>${esc(i.detail)}</p></div>
      <button class="btn small" data-act="insight" data-view="${esc(i.view)}" data-target='${esc(JSON.stringify(i.target))}'>${esc(i.label)}</button></div>`).join('')
    : `<p class="muted">Nothing needs attention right now.</p>`;
  const act = d.activity.length ? `<ul>${d.activity.map(a => `<li style="--c:${ACT_COLOR[a.kind]}"><i></i><div><a href="${actHref(a)}">${esc(a.label)}</a><small>${ago(a.created_at)}</small></div></li>`).join('')}</ul>`
    : `<p class="muted">Activity shows up here once you create initiatives.</p>`;
  return head + `<nav class="chain" aria-label="Pipeline summary">${chain.map(c => `<a class="${c[0]}" href="${c[3]}"><b>${c[1]}</b><span>${esc(c[2])}</span></a>`).join('')}</nav>
    <div class="two"><section><h2 class="section-title">What Velo suggests</h2><div class="rows">${insights}</div></section>
    <section class="activity"><h2 class="section-title">Recent activity</h2>${act}</section></div>${digestHTML(dig)}`;
};

/* ---------- canvas ---------- */
const BLOCKS = [
  ['problem', 'Problems', 'b-problem', 'The top three problems your customers have'],
  ['solution', 'Solution', 'b-solution', 'The smallest thing that solves them'],
  ['key_metrics', 'Key metrics', 'b-metrics', 'The numbers that tell you it is working'],
  ['unique_value', 'Value proposition', 'b-uvp', 'Why you, in one clear sentence'],
  ['unfair_advantage', 'Advantage', 'b-adv', 'What others cannot easily copy'],
  ['channels', 'Channels', 'b-channels', 'How customers find you'],
  ['customer_segments', 'Customers', 'b-customers', 'Who you build for, and early adopters'],
  ['costs', 'Costs', 'b-costs', 'What it costs to run and grow'],
  ['revenue', 'Revenue', 'b-revenue', 'How you make money'],
];
G.views.canvas = async () => {
  const p = await api('/product');
  return `<header class="page-head"><div><h1>Product canvas</h1>
    <p>One page on what you are building and for whom. Velo reads this to keep specs on strategy.</p></div></header>
    <div class="canvas-intro">
      <label class="field"><span>Product name</span><input type="text" data-change="product-name" value="${esc(p.name)}" placeholder="Ledgerly"></label>
      <div class="actions"><label class="btn" style="cursor:pointer" title="Read a brief, strategy doc or pitch and draft the canvas from it">Build from a document<input type="file" accept=".pdf,.docx,.md,.txt" hidden data-change="canvas-doc"></label>
        <button class="btn primary" data-act="gen-canvas">${p.updated_at && Object.values(p.canvas).some(v => v) ? 'Rewrite canvas' : 'Draft canvas'}</button></div>
      <label class="field wide"><span>What it does, in a few sentences</span><textarea rows="2" style="min-height:64px" data-change="product-overview" placeholder="Describe the product, who it is for, and the main problem it solves.">${esc(p.overview)}</textarea></label>
    </div>
    <div class="lean">${BLOCKS.map(b => `<div class="blk ${b[2]}"><h3>${b[1]}</h3>
      <textarea data-change="canvas" data-key="${b[0]}" placeholder="${esc(b[3])}" aria-label="${b[1]}">${esc(p.canvas[b[0]])}</textarea></div>`).join('')}</div>`;
};
G.changes['product-name'] = async t => { await api('/product', { method: 'PUT', body: { name: t.value } }); toast('Saved'); refreshRail(); };
G.changes['product-overview'] = async t => { await api('/product', { method: 'PUT', body: { overview: t.value } }); toast('Saved'); };
G.changes.canvas = async t => { await api('/product', { method: 'PUT', body: { canvas: { [t.dataset.key]: t.value } } }); toast('Saved'); };
G.changes['canvas-doc'] = async t => {
  const f = t.files[0]; if (!f) return;
  if (Object.values((await api('/product')).canvas).some(v => v.trim()) && !confirm('Replace the current canvas with one drafted from this document?')) { t.value = ''; return; }
  const fd = new FormData(); fd.append('file', f); t.value = '';
  toast('Reading the document...');
  try { const r = await api('/product/from-doc', { method: 'POST', body: fd }); toast(r.mode === 'claude' ? 'Canvas drafted from your document' : basicNote('canvas')); render(); } catch (x) { toast(x.message); }
};
G.actions['gen-canvas'] = t => busy(t, 'Drafting...', async () => {
  const name = $('[data-change="product-name"]').value, overview = $('[data-change="product-overview"]').value;
  if (!overview.trim()) throw new Error('Describe your product first, in a sentence or two.');
  if (Object.values((await api('/product')).canvas).some(v => v.trim()) && !confirm('Replace the current canvas with a new draft?')) return;
  const r = await api('/product/generate', { method: 'POST', body: { name, overview } });
  toast(r.mode === 'claude' ? 'Canvas drafted' : basicNote('canvas')); render();
});

/* ---------- evidence ---------- */
G.views.evidence = async () => {
  const items = await api('/evidence');
  const ungrouped = items.filter(e => !e.problem_id).length;
  const sources = [...new Set(items.map(e => e.source))].sort();
  let shown = items;
  if (G.evFilter !== 'all') shown = shown.filter(e => e.source === G.evFilter);
  if (G.evUngrouped) shown = shown.filter(e => !e.problem_id);
  const composerOpen = G.composer || items.length === 0;
  return `<header class="page-head"><div><h1>Evidence</h1>
      <p>What customers actually said. Paste it in as it comes: interviews, tickets, reviews, survey answers.</p></div>
      <div class="actions"><button class="btn" data-act="toggle-composer">${composerOpen && items.length ? 'Close' : 'Add evidence'}</button>
        <label class="btn" style="cursor:pointer">Upload a file<input type="file" accept=".csv,.txt,.md,.pdf,.docx" hidden data-change="upload"></label>
        ${items.length && !ungrouped ? '<button class="btn" data-act="regroup">Regroup all</button>' : ''}</div></header>
    ${composerOpen ? `<div class="panel composer"><textarea id="ev-text" rows="4" placeholder="Paste a quote, a ticket or your notes from a call"></textarea>
      <div class="actions"><select id="ev-src" aria-label="Source"><option>interview</option><option>support</option><option>review</option><option>survey</option><option>note</option></select>
      <button class="btn primary" data-act="add-evidence">Add</button>
      ${items.length ? '' : '<button class="btn" data-act="seed">Load demo evidence instead</button>'}</div>
      <p class="small muted" style="margin:0">Upload a <b>PDF or Word</b> document and it is read into passages. <b>CSV</b> files need a <b>text</b> column (and can have <b>source</b>). Text and Markdown are split on blank lines, one item each. Up to 10 MB.</p></div>` : ''}
    ${ungrouped ? `<div class="banner"><span><b>${plural(ungrouped, 'item')}</b> not in a problem yet.</span><button class="btn primary small" data-act="analyze">Group into problems</button></div>` : ''}
    ${items.length ? `<div class="filters" role="group" aria-label="Filter evidence">
      <button class="chip" aria-pressed="${G.evFilter === 'all'}" data-act="ev-filter" data-v="all">All ${items.length}</button>
      ${sources.map(s => `<button class="chip" aria-pressed="${G.evFilter === s}" data-act="ev-filter" data-v="${esc(s)}">${esc(s)} ${items.filter(e => e.source === s).length}</button>`).join('')}
      ${ungrouped ? `<button class="chip" aria-pressed="${G.evUngrouped}" data-act="ev-ungrouped">Not grouped ${ungrouped}</button>` : ''}</div>
    <div class="rows">${shown.map(e => `<div class="row ev-row"><div class="src">${esc(e.source)}<small>#${e.id}</small></div>
      <div class="ev-text">${esc(e.text)}</div>
      <div class="actions">${e.problem_id ? `<a class="tag problem" href="#/problems/${e.problem_id}">${esc(e.problem_title)}</a>` : '<span class="tag muted">Not grouped</span>'}
      <button class="icon-btn row-actions" data-act="delete-evidence" data-id="${e.id}" aria-label="Delete evidence ${e.id}">&#10005;</button></div></div>`).join('') || '<p class="muted" style="padding:20px 4px">No evidence matches this filter.</p>'}</div>` : ''}`;
};
Object.assign(G.actions, {
  'toggle-composer': () => { G.composer = !G.composer; render(); },
  'ev-filter': t => { G.evFilter = t.dataset.v; render(); },
  'ev-ungrouped': () => { G.evUngrouped = !G.evUngrouped; render(); },
  'add-evidence': t => busy(t, 'Adding...', async () => {
    await api('/evidence', { method: 'POST', body: { text: $('#ev-text').value, source: $('#ev-src').value } });
    toast('Evidence added'); G.composer = true; render();
  }),
  'delete-evidence': async t => {
    if (!confirm('Delete this evidence? It will also leave any problem it supports.')) return;
    await api('/evidence/' + t.dataset.id, { method: 'DELETE' }); toast('Deleted'); render();
  },
});
G.changes.upload = async t => {
  const f = t.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  t.value = '';   // so the same file can be chosen again
  try {
    const r = await api('/evidence/upload', { method: 'POST', body: fd });
    toast(`Added ${plural(r.added, 'item')}` + (r.skipped ? `, ${r.skipped} already there` : '') + (r.truncated ? '. Large file: only the first part was read' : '')); render();
  } catch (x) { toast(x.message); }
};

/* ---------- Ask Velo ---------- */
const SUGGESTIONS = ['What should we build next?', 'Which problem has the weakest evidence?', 'Summarize the top customer pains', 'What have we shipped that we never measured?'];
function openAsk() {
  const a = $('#ask'); a.classList.add('open'); a.setAttribute('aria-hidden', 'false');
  if (!G.ask.length) {
    $('#ask-log').innerHTML = `<div class="msg bot">I can read your evidence, problems, themes and initiatives. Ask me what to build next, where the evidence is thin, or what a customer group is struggling with.</div>`;
    $('#ask-suggest').innerHTML = SUGGESTIONS.map(q => `<button class="chip" type="button" data-act="ask-suggest" data-q="${esc(q)}">${esc(q)}</button>`).join('');
  }
  setTimeout(() => $('#ask-input').focus(), 120);
}
function closeAsk() { const a = $('#ask'); a.classList.remove('open'); a.setAttribute('aria-hidden', 'true'); }
async function sendAsk(q) {
  q = q.trim(); if (!q) return;
  const log = $('#ask-log'); $('#ask-suggest').innerHTML = '';
  if (!G.ask.length) log.innerHTML = '';
  G.ask.push({ role: 'user', content: q });
  log.insertAdjacentHTML('beforeend', `<div class="msg user">${esc(q)}</div><div class="msg bot thinking" id="thinking">Reading your workspace...</div>`);
  log.scrollTop = log.scrollHeight; $('#ask-input').value = '';
  try {
    const r = await api('/ask', { method: 'POST', body: { messages: G.ask } });
    G.ask.push({ role: 'assistant', content: r.answer });
    $('#thinking').outerHTML = `<div class="msg bot">${esc(r.answer)}</div>`;
  } catch (e) {
    G.ask.pop(); $('#thinking').outerHTML = `<div class="msg bot">${esc(e.message)}</div>`;
  }
  log.scrollTop = log.scrollHeight;
}
Object.assign(G.actions, { 'ask-open': openAsk, 'ask-close': closeAsk, 'ask-suggest': t => sendAsk(t.dataset.q) });
$('#ask-form').addEventListener('submit', e => { e.preventDefault(); sendAsk($('#ask-input').value); });
