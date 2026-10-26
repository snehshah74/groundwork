/* Groundwork front end, part 2: problem map, strategy, initiative board, initiative detail with story map and trace. */
'use strict';

/* ================= problems ================= */
function wrapText(s, max, maxLines = 2) {
  const words = String(s).split(/\s+/); const out = []; let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > max && cur) { out.push(cur); cur = w; } else cur = (cur + ' ' + w).trim();
  }
  if (cur) out.push(cur);
  if (out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = out[maxLines - 1].replace(/.{0,2}$/, '') + '...'; }
  return out;
}

function problemMapSVG(probs, evByProblem, product, selId) {
  const W = 900, H = 760, cx0 = 450, cy0 = 385;
  const n = probs.length;
  const nodes = probs.map((p, i) => {
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / n;
    const scale = n > 8 && i % 2 ? 0.66 : 1;
    const r = 36 + Math.min(34, Math.sqrt(p.evidence_count) * 11);
    return { p, r, x: cx0 + 300 * scale * Math.cos(ang), y: cy0 + 215 * scale * Math.sin(ang), ang };
  });
  const sw = { high: 5, medium: 3.2, low: 2 };
  const title = wrapText(product.name || 'Your product', 11, 2);
  let svg = `<svg class="map-svg" viewBox="0 0 ${W} ${H}" role="group" aria-label="Problem map. Each circle is a customer problem sized by the evidence behind it.">`;
  nodes.forEach(nd => { svg += `<line x1="${cx0}" y1="${cy0}" x2="${nd.x.toFixed(1)}" y2="${nd.y.toFixed(1)}" style="stroke:var(--line);stroke-width:1.5"/>`; });
  svg += `<circle cx="${cx0}" cy="${cy0}" r="62" style="fill:var(--ink)"/>`;
  title.forEach((l, k) => { svg += `<text x="${cx0}" y="${cy0 + (k - (title.length - 1) / 2) * 19 + 5}" text-anchor="middle" style="fill:var(--on-ink);font-weight:650;font-size:17px">${esc(l)}</text>`; });
  nodes.forEach(nd => {
    const p = nd.p, ev = evByProblem[p.id] || []; const sel = p.id === selId;
    svg += `<g class="node ${sel ? 'sel' : ''}" tabindex="0" role="button" data-act="select-problem" data-id="${p.id}" aria-label="${esc(p.title)}, ${plural(p.evidence_count, 'piece')} of evidence, ${p.severity} severity">`;
    const dots = Math.min(ev.length, 22);
    for (let k = 0; k < dots; k++) {
      const a = (2 * Math.PI * k) / dots - Math.PI / 2;
      svg += `<circle cx="${(nd.x + (nd.r + 12) * Math.cos(a)).toFixed(1)}" cy="${(nd.y + (nd.r + 12) * Math.sin(a)).toFixed(1)}" r="4" style="fill:var(--c-evidence)"/>`;
    }
    svg += `<circle class="body" cx="${nd.x.toFixed(1)}" cy="${nd.y.toFixed(1)}" r="${nd.r}" style="fill:var(--soft-problem);stroke:var(--c-problem);stroke-width:${sw[p.severity] || 3}"/>`;
    svg += `<text x="${nd.x.toFixed(1)}" y="${(nd.y + 8).toFixed(1)}" text-anchor="middle" style="fill:var(--c-problem);font-weight:700;font-size:26px">${p.evidence_count}</text>`;
    if (p.initiative_count) {
      const bx = nd.x + nd.r * 0.74, by = nd.y - nd.r * 0.74;
      svg += `<circle cx="${bx.toFixed(1)}" cy="${by.toFixed(1)}" r="12" style="fill:var(--c-initiative)"/><text x="${bx.toFixed(1)}" y="${(by + 4.5).toFixed(1)}" text-anchor="middle" style="fill:var(--on-ink);font-weight:700;font-size:13px">${p.initiative_count}</text>`;
    }
    // Labels go above nodes in the top half so the lines to the centre never run through text.
    const lab = wrapText(p.title, 22, 2);
    const rows = lab.length + (p.theme_title ? 1 : 0);
    const above = nd.y < cy0 - 40;
    let ly = above ? nd.y - nd.r - 28 - (rows - 1) * 18 : nd.y + nd.r + 36;
    lab.forEach(l => { svg += `<text x="${nd.x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" style="fill:var(--ink);font-weight:${sel ? 700 : 600};font-size:15.5px">${esc(l)}</text>`; ly += 18; });
    if (p.theme_title) svg += `<text x="${nd.x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" style="fill:var(--c-theme);font-weight:600;font-size:13.5px">${esc(wrapText(p.theme_title, 26, 1)[0])}</text>`;
    svg += `</g>`;
  });
  return svg + `</svg>`;
}

function severityTag(s) { return `<span class="sev ${esc(s)}">${esc(s[0].toUpperCase() + s.slice(1))}</span>`; }

G.views.problems = async r => {
  const [probs, evidence, themes, inits] = await Promise.all([api('/problems'), api('/evidence'), api('/themes'), api('/initiatives')]);
  const st = G.status; const ungrouped = st.evidence - st.grouped;
  const head = `<header class="page-head"><div><h1>Problems</h1>
      <p>Customer problems found in your evidence. The number in each circle is how many pieces of evidence back it.</p></div>
      <div class="actions">${probs.length ? `<div class="seg" role="group" aria-label="View">
        <button aria-pressed="${G.mapMode === 'map'}" data-act="map-mode" data-mode="map">Map</button><button aria-pressed="${G.mapMode === 'list'}" data-act="map-mode" data-mode="list">List</button></div>` : ''}
      ${ungrouped ? `<button class="btn primary" data-act="analyze">Group ${plural(ungrouped, 'new item')}</button>` : ''}</div></header>`;
  if (!st.evidence) return head + `<div class="empty"><h2>No evidence to learn from yet</h2><p>Problems come from what customers said. Add some evidence first.</p>
    <div class="actions"><button class="btn primary" data-act="goto" data-href="#/evidence">Add evidence</button><button class="btn" data-act="seed">Load demo workspace</button></div></div>`;
  if (!probs.length) return head + `<div class="empty"><h2>Group your evidence into problems</h2><p>${plural(st.evidence, 'item')} ready. Velo will cluster them by what the customer is struggling with.</p>
    <button class="btn primary" data-act="analyze">Find problems</button></div>`;

  const evBy = {}; evidence.forEach(e => { if (e.problem_id) (evBy[e.problem_id] = evBy[e.problem_id] || []).push(e); });
  const product = await api('/product');
  let detail = '';
  if (r.id) {
    const p = await api('/problems/' + r.id).catch(() => null);
    if (p) {
      const mine = inits.filter(i => i.problem_id === p.id);
      detail = `<aside class="detail" aria-label="Problem detail">
        <div class="spread">${severityTag(p.severity)}<button class="icon-btn" data-act="close-problem" aria-label="Close detail">&#10005;</button></div>
        <textarea class="bare title-sm" rows="2" data-oneline data-change="problem-field" data-id="${p.id}" data-field="title" aria-label="Problem title">${esc(p.title)}</textarea>
        <textarea class="bare" rows="3" data-change="problem-field" data-id="${p.id}" data-field="summary" aria-label="Summary">${esc(p.summary)}</textarea>
        <label class="field"><span>Who has it</span><input type="text" data-change="problem-field" data-id="${p.id}" data-field="persona" value="${esc(p.persona)}" placeholder="For example, finance admins"></label>
        <div class="actions"><label class="field"><span>Severity</span><select data-change="problem-field" data-id="${p.id}" data-field="severity">
          ${['high', 'medium', 'low'].map(s => `<option value="${s}" ${p.severity === s ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}</select></label>
          <label class="field"><span>Theme</span><select data-change="problem-theme" data-id="${p.id}"><option value="">No theme</option>
          ${themes.map(t => `<option value="${t.id}" ${p.theme_id === t.id ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select></label></div>
        <div class="actions"><button class="btn primary" data-act="create-initiative" data-id="${p.id}">Create initiative</button></div>
        ${mine.length ? `<div><h3 class="section-title">Initiatives (${mine.length})</h3>${mine.map(i => `<p style="margin:6px 0"><a class="tag initiative" href="#/initiative/${i.id}/spec">${esc(i.title)}</a></p>`).join('')}</div>` : ''}
        <div><h3 class="section-title">Evidence (${p.evidence.length})</h3>
          ${p.evidence.map(e => `<div class="quote"><small>${esc(e.source)} #${e.id}</small>${esc(e.text)}</div>`).join('')}</div></aside>`;
    }
  }
  if (G.mapMode === 'list') {
    return head + `<div class="rows">${probs.map(p => {
      const total = p.evidence_count || 1;
      return `<div class="row pr-row"><div>${severityTag(p.severity)}</div>
        <div><h3><a href="#/problems/${p.id}" style="text-decoration:none">${esc(p.title)}</a></h3><p>${esc(p.summary)}</p></div>
        <div class="hide-s"><b>${p.evidence_count}</b> <span class="muted">evidence</span>
          <div class="bar" title="${esc(Object.entries(p.sources).map(([k, v]) => k + ' ' + v).join(', '))}">${Object.entries(p.sources).map(([k, v], x) => `<i style="width:${(v / total) * 100}%;background:var(--c-evidence);opacity:${1 - x * 0.28}"></i>`).join('')}</div></div>
        <div class="hide-s">${p.theme_title ? `<span class="tag theme">${esc(p.theme_title)}</span>` : '<span class="muted small">No theme</span>'}</div>
        <div class="actions"><button class="btn small" data-act="create-initiative" data-id="${p.id}">${p.initiative_count ? 'Add initiative' : 'Create initiative'}</button></div></div>`;
    }).join('')}</div>`;
  }
  return head + `<div class="map-wrap ${detail ? 'with-detail' : ''}"><div>${problemMapSVG(probs, evBy, product, r.id)}
    <div class="map-legend"><span><i style="width:14px;height:14px;border-radius:50%;background:var(--soft-problem);border:3px solid var(--c-problem)"></i>Problem, sized by evidence</span>
    <span><i style="width:8px;height:8px;border-radius:50%;background:var(--c-evidence)"></i>One piece of evidence</span>
    <span><i style="width:14px;height:14px;border-radius:50%;background:var(--c-initiative)"></i>Initiatives started</span>
    <span class="muted">Thicker outline means higher severity</span></div></div>${detail}</div>`;
};
Object.assign(G.actions, {
  'map-mode': t => { G.mapMode = t.dataset.mode; render(); },
  'select-problem': t => go(`#/problems/${t.dataset.id}`),
  'close-problem': () => go('#/problems'),
  'create-initiative': t => busy(t, 'Writing spec...', async () => {
    const i = await api('/initiatives', { method: 'POST', body: { problem_id: +t.dataset.id } });
    toast('Initiative created'); go(`#/initiative/${i.id}/spec`);
  }),
});
G.changes['problem-field'] = async t => {
  const p = await api('/problems/' + t.dataset.id, { method: 'PATCH', body: { [t.dataset.field]: t.value } });
  toast('Saved'); if (t.dataset.field === 'severity' || t.dataset.field === 'title') render();
};
G.changes['problem-theme'] = async t => {
  await api(`/problems/${t.dataset.id}/theme`, { method: 'PUT', body: { theme_id: t.value ? +t.value : null } }); toast('Saved'); render();
};

/* ================= strategy ================= */
G.views.strategy = async () => {
  const [themes, probs, inits] = await Promise.all([api('/themes'), api('/problems'), api('/initiatives')]);
  const head = `<header class="page-head"><div><h1>Strategy</h1>
    <p>Themes are the directions you are willing to commit to. Drag problems into the theme they serve, so every initiative traces back to strategy.</p></div>
    <div class="actions">${probs.length ? '<button class="btn" data-act="suggest-themes">Suggest themes</button>' : ''}<button class="btn primary" data-act="new-theme">New theme</button></div></header>`;
  if (!probs.length && !themes.length) return head + `<div class="empty"><h2>Themes come after problems</h2><p>Group your evidence into problems first, then organize them here.</p>
    <button class="btn primary" data-act="goto" data-href="#/problems">Go to problems</button></div>`;
  const miniP = p => `<div class="mini p" draggable="true" data-drag="problem:${p.id}"><b><a href="#/problems/${p.id}" draggable="false" style="text-decoration:none">${esc(p.title)}</a></b>
      <div class="meta">${severityTag(p.severity)}<span>${plural(p.evidence_count, 'item')}</span></div></div>`;
  const miniI = i => `<div class="mini i" data-act="open-initiative" data-id="${i.id}"><b>${esc(i.title)}</b><div class="meta"><span class="tag initiative">${esc(statusLabel(i.status))}</span></div></div>`;
  const un = probs.filter(p => !p.theme_id);
  const unCol = un.length ? `<section class="theme-col un" data-drop="theme:none" aria-label="Problems without a theme"><div><span class="t-title">No theme yet</span>
      <div class="small muted">${plural(un.length, 'problem')}</div></div>${un.map(miniP).join('')}</section>` : '';
  const cols = themes.map(t => `<section class="theme-col" data-drop="theme:${t.id}" aria-label="Theme ${esc(t.title)}">
      <div class="spread"><input class="bare t-title" data-change="theme-field" data-id="${t.id}" data-field="title" value="${esc(t.title)}" aria-label="Theme title">
        <button class="icon-btn" data-act="delete-theme" data-id="${t.id}" aria-label="Delete theme ${esc(t.title)}">&#10005;</button></div>
      <textarea class="bare t-desc" rows="2" data-change="theme-field" data-id="${t.id}" data-field="description" placeholder="Why this matters to the product" aria-label="Theme description">${esc(t.description)}</textarea>
      <label class="field"><span class="small">Priority</span><select data-change="theme-priority" data-id="${t.id}">
        <option value="">Not ranked</option>${[1, 2, 3].map(n => `<option value="${n}" ${t.priority === n ? 'selected' : ''}>${n}${n === 1 ? ' (most important)' : ''}</option>`).join('')}</select></label>
      <div class="small muted">${plural(t.problem_count, 'problem')}, ${plural(t.evidence_count, 'piece')} of evidence, ${plural(t.initiative_count, 'initiative')}</div>
      ${probs.filter(p => p.theme_id === t.id).map(miniP).join('')}
      ${inits.filter(i => i.theme_id === t.id).map(miniI).join('')}
      ${!probs.some(p => p.theme_id === t.id) ? '<p class="small muted" style="margin:0">Drop a problem here.</p>' : ''}</section>`).join('');
  return head + `<div class="themes">${unCol}${cols}</div>`;
};
Object.assign(G.actions, {
  'suggest-themes': t => busy(t, 'Thinking...', async () => {
    const r = await api('/themes/generate', { method: 'POST' }); toast(`${plural(r.themes_created, 'theme')} suggested`); render();
  }),
  'new-theme': () => modal({
    title: 'New theme', submit: 'Create theme',
    fields: [{ name: 'title', label: 'Theme', placeholder: 'For example, Make month-end effortless' }, { name: 'description', label: 'Why it matters', type: 'textarea', rows: 3 }],
    onSubmit: async v => { await api('/themes', { method: 'POST', body: v }); toast('Theme created'); render(); },
  }),
  'delete-theme': async t => {
    if (!confirm('Delete this theme? Its problems and initiatives stay, but lose the theme.')) return;
    await api('/themes/' + t.dataset.id, { method: 'DELETE' }); toast('Theme deleted'); render();
  },
  'open-initiative': t => go(`#/initiative/${t.dataset.id}/spec`),
});
G.changes['theme-field'] = async t => { await api('/themes/' + t.dataset.id, { method: 'PATCH', body: { [t.dataset.field]: t.value } }); toast('Saved'); if (t.dataset.field === 'title') refreshRail(); };
G.drops['problem>theme'] = async (pid, tid) => {
  await api(`/problems/${pid}/theme`, { method: 'PUT', body: { theme_id: tid === 'none' ? null : +tid } }); render();
};

/* ================= initiatives board ================= */
G.views.initiatives = async () => {
  const [inits, themes] = await Promise.all([api('/initiatives'), api('/themes')]);
  const shown = G.themeFilter === 'all' ? inits : inits.filter(i => String(i.theme_id) === G.themeFilter);
  const head = `<header class="page-head"><div><h1>Initiatives</h1>
    <p>Work that is backed by evidence. Drag a card to move it along. Open one to see its spec, story map and full trace.</p></div>
    <div class="actions">${themes.length ? `<label class="field"><span class="muted small">Theme</span><select data-change="theme-filter"><option value="all">All themes</option>
      ${themes.map(t => `<option value="${t.id}" ${G.themeFilter === String(t.id) ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select></label>` : ''}</div></header>`;
  if (!inits.length) return head + `<div class="empty"><h2>No initiatives yet</h2><p>Pick a problem with strong evidence and Velo will write the spec: who it is for, the metric, acceptance criteria and what is out of scope.</p>
    <button class="btn primary" data-act="goto" data-href="#/problems">Choose a problem</button></div>`;
  return head + `<div class="board">${STATUSES.map(([key, label]) => {
    const cards = shown.filter(i => i.status === key);
    return `<section class="col" data-drop="status:${key}" aria-label="${label}"><div class="col-head">${label}<span>${cards.length}</span></div>
      ${cards.map(i => `<div class="icard" draggable="true" data-drag="initiative:${i.id}" data-act="open-initiative" data-id="${i.id}" tabindex="0" role="link">
        <b>${esc(i.title)}</b>
        <div class="meta">${i.theme_title ? `<span class="tag theme">${esc(i.theme_title)}</span>` : ''}
          <span class="tag evidence">${plural(i.evidence_count, 'item')}</span>
          ${i.story_count ? `<span class="tag story">${plural(i.story_count, 'story', 'stories')}</span>` : ''}
          ${i.latest_verdict ? `<span class="tag outcome">${esc(verdictLabel(i.latest_verdict))}</span>` : ''}
          ${isStale(i) ? `<span class="tag stale" title="${esc(i.context.reasons.join(', '))}">Out of date</span>` : ''}</div></div>`).join('')}</section>`;
  }).join('')}</div>`;
};
G.changes['theme-filter'] = t => { G.themeFilter = t.value; render(); };
G.drops['initiative>status'] = async (iid, status) => {
  await api('/initiatives/' + iid, { method: 'PATCH', body: { status } }); render();
};
document.addEventListener('keydown', e => {
  const c = e.target.closest && e.target.closest('.icard');
  if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); go(`#/initiative/${c.dataset.id}/spec`); }
});

/* ================= initiative detail ================= */
const isStale = i => i.context && i.context.stale && !['shipped', 'measured'].includes(i.status);
const TABS = [['spec', 'Spec'], ['stories', 'Story map'], ['trace', 'Trace'], ['handoff', 'Hand off'], ['outcome', 'Outcome']];

function jag(seed) {
  const pts = [];
  for (let k = 0; k <= 10; k++) pts.push(`${k * 10}% ${4 + ((seed * 31 + k * 47 + k * k * 13) % 10)}px`);
  return `polygon(${pts.join(',')},100% 100%,0 100%)`;
}
const fmtDate = iso => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
const listHTML = items => items.length ? `<ul>${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted">Nothing yet.</p>';

function traceHTML(i) {
  const L = [];
  const o = i.outcomes[0];
  L.push({ c: 'outcome', title: 'Result', small: o ? fmtDate(o.created_at) : '', void: !o,
    body: o ? `<p><span class="tag outcome">${esc(verdictLabel(o.verdict))}</span> ${o.metric_value ? `<b>${esc(o.metric_value)}</b>` : ''}</p>${o.note ? `<p>${esc(o.note)}</p>` : ''}
        <p class="small muted">Target was: ${esc(i.success_metric || 'not set')}</p>`
      : `<div class="void-note"><span>Not measured yet. Record whether the success metric moved to close the loop.</span><button class="btn small" data-act="goto" data-href="#/initiative/${i.id}/outcome">Record outcome</button></div>` });
  const d = i.dispatches;
  L.push({ c: 'handoff', title: 'Handed to a coding agent', small: d.length ? `${plural(d.length, 'time')}` : '', void: !d.length,
    body: d.length ? `<p>Last sent to <b>${esc(d[0].target)}</b> on ${fmtDate(d[0].created_at)}.</p>`
      : `<div class="void-note"><span>No agent has received this brief yet.</span><button class="btn small" data-act="goto" data-href="#/initiative/${i.id}/handoff">Hand off</button></div>` });
  const acts = []; i.stories.forEach(s => { if (!acts.includes(s.activity)) acts.push(s.activity); });
  L.push({ c: 'story', title: 'Story map', small: i.stories.length ? plural(i.stories.length, 'story', 'stories') : '', void: !i.stories.length,
    body: i.stories.length ? `<ul>${acts.slice(0, 6).map(a => `<li><b>${esc(a || 'General')}</b>: ${esc(i.stories.filter(s => s.activity === a).map(s => s.title).slice(0, 2).join('; '))}</li>`).join('')}</ul>`
      : `<div class="void-note"><span>The work is not broken into stories.</span><button class="btn small" data-act="goto" data-href="#/initiative/${i.id}/stories">Build story map</button></div>` });
  L.push({ c: 'initiative', title: 'Initiative', small: statusLabel(i.status), void: false,
    body: `<p><b>${esc(i.title)}</b></p><p>${esc(i.problem_statement)}</p><p class="small"><span class="muted">For</span> ${esc(i.persona || 'unspecified users')}.
      <span class="muted">Success:</span> ${esc(i.success_metric || 'not set')}</p>` });
  L.push({ c: 'theme', title: 'Strategy theme', small: '', void: !i.theme_title,
    body: i.theme_title ? `<p><b>${esc(i.theme_title)}</b></p>` : `<div class="void-note"><span>This work is not tied to a theme.</span><button class="btn small" data-act="goto" data-href="#/strategy">Open strategy</button></div>` });
  L.push({ c: 'problem', title: 'Customer problem', small: '', void: false,
    body: `<p><b>${esc(i.problem_title)}</b></p><p>${esc(i.problem_summary || '')}</p>` });
  const show = G.traceAll ? i.evidence : i.evidence.slice(0, 4);
  L.push({ c: 'evidence', title: 'Customer evidence', small: plural(i.evidence.length, 'item'), void: false,
    body: show.map(e => `<p class="q"><small>${esc(e.source)} #${e.id}</small>${esc(e.text)}</p>`).join('')
      + (i.evidence.length > 4 ? `<button class="btn small" data-act="trace-all">${G.traceAll ? 'Show fewer' : `Show all ${i.evidence.length}`}</button>` : '') });
  const layers = L.map((l, n) => `<section class="layer ${l.void ? 'void' : ''}" style="--c:var(--c-${l.c});--soft:var(--soft-${l.c});${n ? `clip-path:${jag(n + i.id)}` : ''}">
      <h3>${esc(l.title)}<small>${esc(l.small)}</small></h3>${l.body}</section>`).join('');
  return `<div class="core"><div class="strata" role="list" aria-label="From measured result at the top down to customer evidence">${layers}</div>
    <div class="ruler" aria-hidden="true"><span>Measured result</span><div class="rule"></div><span>Ground truth</span></div></div>`;
}

function storyMapHTML(i) {
  const order = []; const by = {};
  i.stories.forEach(s => { const a = s.activity || 'General'; if (!by[a]) { by[a] = []; order.push(a); } by[a].push(s); });
  const head = `<div class="spread" style="margin-bottom:16px"><p class="sub" style="margin:0;max-width:60ch">Steps across the top follow the user's journey. Stories underneath are what to build, in order. Agents get this map with the brief.</p>
    <div class="actions"><a class="btn" href="/api/initiatives/${i.id}/tickets.csv" download>Export tickets (CSV)</a>
      <button class="btn" data-act="add-story" data-activity="">Add story</button><button class="btn primary" data-act="gen-stories" data-id="${i.id}">${i.stories.length ? 'Rewrite with Velo' : 'Build story map'}</button></div></div>`;
  if (!i.stories.length) return head + `<div class="empty"><h2>No stories yet</h2><p>Velo will turn this initiative into user stories with acceptance criteria, grouped by the steps of the user's journey.</p></div>`;
  return head + `<div class="storymap">${order.map(a => `<div class="activity-col"><div class="activity-head">${esc(a)}</div>
    ${by[a].map(s => `<button class="story-card" data-act="edit-story" data-id="${s.id}"><b>${esc(s.title)}</b>${s.description ? `<p>${esc(s.description)}</p>` : ''}
      ${s.acceptance.length ? `<ul>${s.acceptance.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</button>`).join('')}
    <button class="add-story" data-act="add-story" data-activity="${esc(a)}">Add story</button></div>`).join('')}</div>`;
}

G.views.initiative = async r => {
  const tab = TABS.some(t => t[0] === r.tab) ? r.tab : 'spec';
  const [i, themes, ilinks] = await Promise.all([api('/initiatives/' + r.id), api('/themes'), api(`/initiatives/${r.id}/links`)]);
  G.cur = i;
  const header = `<a class="crumb" href="#/initiatives">Initiatives</a>
    <header><input class="bare title-input" data-change="init-field" data-id="${i.id}" data-field="title" value="${esc(i.title)}" aria-label="Initiative title">
    <div class="actions" style="margin-top:10px"><span class="sub">From <a href="#/problems/${i.problem_id}">${esc(i.problem_title)}</a></span>
      ${isStale(i) ? `<a class="tag stale" href="#/initiative/${i.id}/handoff" title="${esc(i.context.reasons.join(', '))}">Out of date</a>` : ''}
      ${ilinks.filter(l => l.kind === 'initiative').map(l => `<a class="tag initiative" href="${esc(l.external_url)}" target="_blank" rel="noopener" title="${esc(l.provider)}">${esc(l.external_key)} &middot; ${esc(l.external_status)}</a>`).join('')}
      <label class="actions" style="gap:6px"><span class="muted small">Theme</span><select data-change="init-theme" data-id="${i.id}"><option value="">None</option>
      ${themes.map(t => `<option value="${t.id}" ${i.theme_id === t.id ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select></label></div>
    <div class="steps" role="group" aria-label="Status">${STATUSES.map(([k, l]) => `<button class="step" aria-pressed="${i.status === k}" data-act="set-status" data-id="${i.id}" data-status="${k}">${l}</button>`).join('')}</div></header>
    <nav class="tabs" aria-label="Initiative sections">${TABS.map(([k, l]) => `<a href="#/initiative/${i.id}/${k}" ${tab === k ? 'aria-current="page"' : ''}>${l}</a>`).join('')}</nav>`;
  let body = '';
  if (tab === 'spec') {
    body = `<div class="spec-grid"><div class="stack">
        <label class="field"><span>The problem</span><textarea rows="4" data-change="init-field" data-id="${i.id}" data-field="problem_statement">${esc(i.problem_statement)}</textarea></label>
        <label class="field"><span>Who it is for</span><input type="text" data-change="init-field" data-id="${i.id}" data-field="persona" value="${esc(i.persona)}"></label>
        <label class="field"><span>Success looks like</span><textarea rows="2" data-change="init-field" data-id="${i.id}" data-field="success_metric">${esc(i.success_metric)}</textarea></label>
        <label class="field"><span>Acceptance criteria, one per line</span><textarea class="list-edit" data-change="init-list" data-id="${i.id}" data-field="acceptance_criteria">${esc(i.acceptance_criteria.join('\n'))}</textarea></label>
        <label class="field"><span>Out of scope, one per line</span><textarea data-change="init-list" data-id="${i.id}" data-field="out_of_scope">${esc(i.out_of_scope.join('\n'))}</textarea></label></div>
      <div><h2 class="section-title">Why we are building this</h2><p class="sub small" style="margin-top:-6px">${plural(i.evidence.length, 'piece')} of customer evidence</p>
        ${i.evidence.map(e => `<div class="quote"><small>${esc(e.source)} #${e.id}</small>${esc(e.text)}</div>`).join('')}</div></div>`;
  } else if (tab === 'stories') {
    body = storyMapHTML(i);
  } else if (tab === 'trace') {
    body = `<p class="sub" style="max-width:62ch;margin-top:0">Read from the bottom up: what customers said, the problem it revealed, the theme it serves, the initiative, the stories, the hand-off, and the measured result. Hatched layers are missing.</p>${traceHTML(i)}`;
  } else if (tab === 'handoff') {
    const [ctx, integs, links] = await Promise.all([api(`/initiatives/${i.id}/context`), api('/integrations'), api(`/initiatives/${i.id}/links`)]);
    const prompt = `Implement Groundwork initiative ${i.id}. Call get_product_context, then get_initiative_context with initiative_id ${i.id}, build it story by story, and call report_outcome when it ships.`;
    const mcpCmd = `claude mcp add groundwork -e GROUNDWORK_TOKEN=${G.me.api_token} -- ${'${PWD}'}/.venv/bin/python ${'${PWD}'}/mcp_server.py`;
    G.cur.ctx = ctx; G.cur.prompt = prompt;
    const staleBanner = isStale(i) ? `<div class="banner" style="grid-column:1/-1;margin:0"><span><b>This brief is out of date.</b>
        Since it was last sent (${ago(i.context.sent_at)}): ${esc(i.context.reasons.join(' and '))}.
        Agents get the new version the next time they read it. Trackers need <b>Update in ...</b> below.</span></div>` : '';
    body = `<div class="spec-grid">${staleBanner}<div class="stack">
        <div class="panel"><h2>1. Connect your agent once</h2>
          <p class="sub small">Run this once from the folder where you installed Groundwork. The token tells the agent which workspace is yours, so keep it private.</p>
          <pre class="brief" style="max-height:none">${esc(mcpCmd)}</pre>
          <div class="actions" style="margin-top:10px"><button class="btn small" data-act="copy-text" data-msg="Command copied" data-text="${esc(mcpCmd)}">Copy command</button>
            <button class="btn small quiet" data-act="rotate-token">Rotate token</button></div></div>
        <div class="panel"><h2>2. Tell the agent what to build</h2><pre class="brief" style="max-height:none">${esc(prompt)}</pre>
          <div class="actions" style="margin-top:10px"><button class="btn small" data-act="copy-prompt">Copy prompt</button></div></div>
        ${trackerPanel(i, integs, links)}
        <div class="panel"><h2>No agent connected? Paste the brief</h2><p class="sub small">Works in any tool. Copying counts as a hand-off.</p>
          <div class="actions"><button class="btn primary" data-act="copy-brief" data-id="${i.id}">Copy brief</button><a class="btn" href="/api/initiatives/${i.id}/tickets.csv" download>Export tickets (CSV)</a></div></div></div>
      <div><h2 class="section-title">What the agent receives</h2><pre class="brief">${esc(ctx)}</pre>
        ${i.dispatches.length ? `<h2 class="section-title" style="margin-top:22px">Deliveries</h2>${i.dispatches.map(d => `<p class="small" style="margin:4px 0">${esc(d.target)} <span class="muted">${ago(d.created_at)}</span></p>`).join('')}` : ''}</div></div>`;
  } else {
    body = `<div class="spec-grid"><div class="panel stack"><h2>Did it work?</h2>
        <p class="sub small" style="margin:0">Target: ${esc(i.success_metric || 'no metric set')}</p>
        <label class="field"><span>Verdict</span><select id="o-verdict">${VERDICTS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('')}</select></label>
        <label class="field"><span>What you measured</span><input type="text" id="o-metric" placeholder="For example, 37% fewer support tickets after 4 weeks"></label>
        <label class="field"><span>Notes</span><textarea id="o-note" rows="3"></textarea></label>
        <div><button class="btn primary" data-act="record-outcome" data-id="${i.id}">Record outcome</button></div></div>
      <div><h2 class="section-title">History</h2>${i.outcomes.length ? i.outcomes.map(o => `<div class="quote" style="border-color:var(--c-outcome)"><small style="color:var(--c-outcome)">${esc(verdictLabel(o.verdict))}, ${fmtDate(o.created_at)}</small>
        ${o.metric_value ? `<b>${esc(o.metric_value)}</b><br>` : ''}${esc(o.note)}</div>`).join('') : '<p class="muted">Nothing recorded yet.</p>'}</div></div>`;
  }
  return header + body;
};

Object.assign(G.actions, {
  'set-status': t => api('/initiatives/' + t.dataset.id, { method: 'PATCH', body: { status: t.dataset.status } }).then(() => render()).catch(e => toast(e.message)),
  'trace-all': () => { G.traceAll = !G.traceAll; render(); },
  'copy-prompt': () => copyText(G.cur.prompt, 'Prompt copied'),
  'copy-brief': async t => {
    const r = await api(`/initiatives/${t.dataset.id}/dispatch`, { method: 'POST', body: { target: 'clipboard' } });
    await copyText(r.context, 'Brief copied'); render();
  },
  'record-outcome': t => busy(t, 'Saving...', async () => {
    await api(`/initiatives/${t.dataset.id}/outcome`, { method: 'POST', body: { verdict: $('#o-verdict').value, metric_value: $('#o-metric').value, note: $('#o-note').value } });
    toast('Outcome recorded'); render();
  }),
  'gen-stories': t => busy(t, 'Building...', async () => {
    if (G.cur.stories.length && !confirm('Replace the current stories with a new draft?')) return;
    const r = await api(`/initiatives/${t.dataset.id}/stories/generate`, { method: 'POST' });
    toast(r.mode === 'claude' ? `${plural(r.stories.length, 'story', 'stories')} drafted` : 'Basic story map drafted. Turn on AI for a fuller one.'); render();
  }),
  'add-story': t => modal({
    title: 'Add story', submit: 'Add story',
    fields: [{ name: 'activity', label: 'Step in the journey', value: t.dataset.activity, placeholder: 'For example, Set up' }, { name: 'title', label: 'Story', placeholder: 'As a finance admin, I can export every report at once' },
      { name: 'description', label: 'Context', type: 'textarea', rows: 2 }, { name: 'acceptance', label: 'Acceptance criteria, one per line', type: 'textarea', rows: 3 }],
    onSubmit: async v => {
      if (!v.title.trim()) return toast('A story needs a title.');
      await api(`/initiatives/${G.cur.id}/stories`, { method: 'POST', body: { title: v.title, activity: v.activity, description: v.description, acceptance: lines(v.acceptance) } }); render();
    },
  }),
  'edit-story': t => {
    const s = G.cur.stories.find(x => x.id === +t.dataset.id); if (!s) return;
    modal({
      title: 'Edit story', submit: 'Save',
      fields: [{ name: 'activity', label: 'Step in the journey', value: s.activity }, { name: 'title', label: 'Story', value: s.title },
        { name: 'description', label: 'Context', type: 'textarea', rows: 2, value: s.description }, { name: 'acceptance', label: 'Acceptance criteria, one per line', type: 'textarea', rows: 4, value: s.acceptance.join('\n') }],
      onSubmit: async v => { await api('/stories/' + s.id, { method: 'PATCH', body: { title: v.title, activity: v.activity, description: v.description, acceptance: lines(v.acceptance) } }); render(); },
      onDelete: async () => { await api('/stories/' + s.id, { method: 'DELETE' }); toast('Story deleted'); render(); },
    });
  },
});
G.changes['init-field'] = async t => { await api('/initiatives/' + t.dataset.id, { method: 'PATCH', body: { [t.dataset.field]: t.value } }); toast('Saved'); };
G.changes['init-list'] = async t => { await api('/initiatives/' + t.dataset.id, { method: 'PATCH', body: { [t.dataset.field]: lines(t.value) } }); toast('Saved'); };
G.changes['init-theme'] = async t => { await api('/initiatives/' + t.dataset.id, { method: 'PATCH', body: { theme_id: t.value ? +t.value : null } }); toast('Saved'); };

/* ================= boot ================= */
if (!location.hash) location.hash = '#/home';
render();
