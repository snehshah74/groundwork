/* Groundwork front end, part 7: governance (what agents did, set against the spec and the outcome). */
'use strict';

const FLAG_LABEL = { stale: 'Out of date', no_stories: 'No stories', no_criteria: 'No criteria', no_outcome: 'Not measured', stalled: 'Stalled' };

G.views.governance = async () => {
  const g = await api('/governance');
  const s = g.summary;
  const head = `<header class="page-head"><div><h1>Governance</h1>
    <p>For each initiative: the spec, where it was sent, what agents did with it, and how it turned out. Flags mark the things that tend to go wrong.</p></div></header>`;
  if (!g.items.length) {
    return head + `<div class="empty"><h2>No initiatives to govern yet</h2><p>Once work is planned and sent to an agent or a tracker, you can follow it here.</p>
      <button class="btn primary" data-act="goto" data-href="#/problems">Choose a problem</button></div>`;
  }
  const chips = `<div class="filters" aria-label="Summary"><span class="chip" style="cursor:default"><b>${s.flagged}</b> of ${s.total} need a look</span>
    ${Object.keys(FLAG_LABEL).filter(k => s[k]).map(k => `<span class="chip" style="cursor:default">${FLAG_LABEL[k]} <b>${s[k]}</b></span>`).join('')}</div>`;
  const rows = g.items.map(i => `<tr>
      <td class="who"><b><a href="#/initiative/${i.id}/handoff" style="text-decoration:none">${esc(i.title)}</a></b>
        <span><span class="tag initiative">${esc(statusLabel(i.status))}</span></span></td>
      <td class="small">${i.sent_to.length ? i.sent_to.map(t => `<div>${esc(t.target)} <span class="muted">${ago(t.last_at)}</span></div>`).join('') : '<span class="muted">Not sent</span>'}</td>
      <td class="small">${i.agent_actions ? `${plural(i.agent_actions, 'action')}<div class="muted">last by ${esc(i.last_actor || '')}, ${ago(i.last_agent_at)}</div>` : '<span class="muted">None</span>'}</td>
      <td class="small">${plural(i.stories, 'story', 'stories')}<div class="muted">${plural(i.criteria, 'criterion', 'criteria')}</div></td>
      <td>${i.verdict ? `<span class="tag outcome">${esc(verdictLabel(i.verdict))}</span>` : '<span class="muted small">None</span>'}</td>
      <td>${i.flags.length ? i.flags.map(f => `<span class="tag ${f.level === 'attention' ? 'stale' : 'muted'}" title="${esc(f.text)}" style="margin:2px 4px 2px 0">${esc(FLAG_LABEL[f.code])}</span>`).join('') : '<span class="muted small">All clear</span>'}</td></tr>`).join('');
  return head + chips + `<div style="overflow-x:auto"><table class="tbl"><thead><tr><th>Initiative</th><th>Sent to</th><th>Agent activity</th><th>Spec</th><th>Outcome</th><th>Flags</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted" style="margin-top:14px;max-width:72ch">Agent activity is recorded when an agent connected over MCP reads a brief, reads the evidence, changes a status, records an outcome or pushes work. Hover a flag for the detail.</p>`;
};
