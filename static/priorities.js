/* Groundwork front end, part 5: priorities (a transparent ranking of what to build next). */
'use strict';

const SCORE_PARTS = [
  ['reach', 'Customers', 'var(--c-evidence)'], ['severity', 'Severity', 'var(--c-problem)'], ['corroboration', 'Sources', 'var(--c-theme)'],
  ['recency', 'Recent', 'var(--c-story)'], ['strategy', 'Strategy', 'var(--c-initiative)'],
];

function scoreBar(c) {
  const label = SCORE_PARTS.map(([k, n]) => `${n} ${c[k]}`).join(', ');
  return `<div class="scorebar" role="img" aria-label="Score ${c.total} of 100: ${label}">${SCORE_PARTS.map(([k, n, col]) =>
    c[k] > 0 ? `<i style="width:${c[k]}%;background:${col}" title="${n}: ${c[k]} points"></i>` : '').join('')}</div>`;
}

G.views.priorities = async () => {
  const d = await api('/priorities');
  const head = `<header class="page-head"><div><h1>Priorities</h1>
    <p>What to build next, ranked by evidence rather than opinion. Every score is a sum of points you can see, out of 100.</p></div></header>`;
  const legend = `<div class="map-legend" style="border:0;padding:0 0 14px">${SCORE_PARTS.map(([k, n, col]) =>
    `<span><i style="width:12px;height:12px;border-radius:3px;background:${col}"></i>${n} <span class="muted">up to ${d.weights[k]}</span></span>`).join('')}</div>`;
  if (!d.build_next.length && !d.start_next.length) {
    return head + `<div class="empty"><h2>Nothing to rank yet</h2><p>Priorities come from problems, and problems come from evidence.</p>
      <button class="btn primary" data-act="goto" data-href="#/evidence">Add evidence</button></div>`;
  }
  const row = (x, n, action) => `<div class="prio-row"><div class="rank">${n}</div>
      <div><b>${action === 'open' ? `<a href="#/initiative/${x.id}/spec" style="text-decoration:none">${esc(x.title)}</a>` : `<a href="#/problems/${x.id}" style="text-decoration:none">${esc(x.title)}</a>`}</b>
        <div class="small muted">${esc(x.why)}</div>${scoreBar(x.components)}</div>
      <div class="score" title="Score out of 100">${x.score}</div>
      <div class="actions">${action === 'open'
        ? `<span class="tag initiative">${esc(statusLabel(x.status))}</span>${x.stale ? '<span class="tag stale">Out of date</span>' : ''}`
        : `<button class="btn small" data-act="create-initiative" data-id="${x.id}">Create initiative</button>`}</div></div>`;
  return head + legend
    + `<h2 class="group-title">Build next</h2><p class="small muted" style="margin:-6px 0 10px">Initiatives that are not shipped yet, best first.</p>
       <div class="rows">${d.build_next.length ? d.build_next.map((x, n) => row(x, n + 1, 'open')).join('') : '<p class="muted" style="padding:14px 4px">No open initiatives. Start one below.</p>'}</div>
       <h2 class="group-title">Start next</h2><p class="small muted" style="margin:-6px 0 10px">Problems nobody is working on yet, best first.</p>
       <div class="rows">${d.start_next.length ? d.start_next.map((x, n) => row(x, n + 1, 'start')).join('') : '<p class="muted" style="padding:14px 4px">Every problem has an initiative.</p>'}</div>
       <p class="small muted" style="margin-top:22px;max-width:70ch">How it works: customers counts how many people raised it (saturating at ${20} items), severity is the problem's rating, sources rewards agreement between interviews, tickets and reviews, recent favours the last ${30} days, and strategy adds points when the problem sits in a theme you marked priority 1, 2 or 3 on the Strategy page.</p>`;
};

G.changes['theme-priority'] = async t => {
  try { await api('/themes/' + t.dataset.id, { method: 'PATCH', body: { priority: t.value ? +t.value : null } }); toast('Saved'); } catch (e) { toast(e.message); }
  render();
};
