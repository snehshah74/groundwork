/* Groundwork front end, part 4: integrations page. */
'use strict';

const INTEGRATION_GROUPS = [
  ['tracker', 'Work trackers', 'Send initiatives and stories where your team builds, and read status back.'],
  ['discovery', 'Customer discovery', 'Bring customer evidence in automatically instead of pasting it.'],
  ['comms', 'Inboxes and chat', 'Capture feedback from email and chat, and tell people when work ships.'],
];

function integrationCard(p, admin) {
  const head = `<div class="top"><h3>${esc(p.name)}</h3>${p.status !== 'ready' ? '<span class="pill soon">Coming soon</span>' : p.connected ? '<span class="pill ok">Connected</span>' : ''}</div><p>${esc(p.description)}</p>`;
  if (p.status !== 'ready') return `<div class="pcard">${head}</div>`;
  if (!p.connected) {
    return `<div class="pcard">${head}${admin ? `<div><button class="btn primary small" data-act="connect-integration" data-id="${p.id}">Connect ${esc(p.name)}</button></div>`
      : '<p class="small muted">An admin can connect this.</p>'}</div>`;
  }
  const c = p.config || {};
  const teams = c.targets || [];
  const label = c.target_label || 'team';
  const last = p.last_log ? `<p class="small ${p.last_log.ok ? 'muted' : ''}" style="${p.last_log.ok ? '' : 'color:var(--danger)'}">Last activity: ${esc(p.last_log.summary)} <span class="muted">${ago(p.last_log.created_at)}</span></p>` : '';
  return `<div class="pcard connected">${head}
    <p class="small"><span class="muted">Account</span> ${esc(c.account || '')}</p>
    ${teams.length ? `<label class="field"><span>Send work to ${esc(label)}</span><select data-change="integration-team" data-id="${p.id}" ${admin ? '' : 'disabled'}>
      ${c.target_id ? '' : `<option value="">Choose a ${esc(label)}...</option>`}${teams.map(t => `<option value="${esc(t.id)}" ${t.id === c.target_id ? 'selected' : ''}>${esc(t.name)}${t.key ? ` (${esc(t.key)})` : ''}</option>`).join('')}</select></label>` : ''}
    ${p.webhooks && p.webhook_url ? `<details class="small"><summary style="cursor:pointer;font-weight:600">Live status updates (webhook)</summary>
      <ol style="margin:8px 0 0;padding-left:18px">
        <li>${esc(p.webhook_help)}</li>
        <li>Use this URL:<div class="link-box"><input type="text" readonly value="${esc(p.webhook_url)}" aria-label="Webhook URL"><button class="btn small" data-act="copy-text" data-msg="URL copied" data-text="${esc(p.webhook_url)}">Copy</button></div></li>
        <li>Paste the signing secret ${esc(p.name)} shows you:
          <div class="link-box"><input type="password" id="whsec-${p.id}" placeholder="${p.has_webhook_secret ? 'Saved. Paste to replace' : 'Signing secret'}" autocomplete="off" ${admin ? '' : 'disabled'}>
          <button class="btn small" data-act="save-webhook-secret" data-id="${p.id}" ${admin ? '' : 'disabled'}>Save</button></div></li>
      </ol></details>` : ''}
    ${last}
    <div class="actions"><button class="btn small" data-act="sync-integration" data-id="${p.id}">Sync now</button>
      ${admin ? `<button class="btn small" data-act="test-integration" data-id="${p.id}">Test</button><button class="btn small danger" data-act="disconnect-integration" data-id="${p.id}" data-name="${esc(p.name)}">Disconnect</button>` : ''}</div></div>`;
}

G.views.integrations = async () => {
  const items = await api('/integrations');
  const admin = ['admin', 'owner'].includes(G.me.workspace.role);
  return `<header class="page-head"><div><h1>Integrations</h1>
    <p>Connect the tools around Groundwork. Credentials are encrypted and only ever used to talk to that tool.</p></div></header>
    ${INTEGRATION_GROUPS.map(([kind, title, sub]) => `<h2 class="group-title" title="${esc(sub)}">${title}</h2><p class="small muted" style="margin:-6px 0 12px">${esc(sub)}</p>
      <div class="cards">${items.filter(p => p.kind === kind).map(p => integrationCard(p, admin)).join('')}</div>`).join('')}`;
};

Object.assign(G.actions, {
  'connect-integration': async t => {
    const p = (await api('/integrations')).find(x => x.id === t.dataset.id);
    modal({
      title: `Connect ${p.name}`, submit: 'Connect',
      fields: p.fields.map(f => ({ name: f.name, label: f.label, type: f.secret ? 'password' : 'text', placeholder: f.help || '' })),
      onSubmit: async v => {
        try { await api(`/integrations/${p.id}/connect`, { method: 'POST', body: v }); toast('Connected'); } catch (e) { toast(e.message); }
        render();
      },
    });
  },
  'disconnect-integration': async t => {
    if (!confirm(`Disconnect ${t.dataset.name}? Links to its items are forgotten here. Nothing is deleted in ${t.dataset.name}.`)) return;
    await api('/integrations/' + t.dataset.id, { method: 'DELETE' }); toast('Disconnected'); render();
  },
  'test-integration': t => busy(t, 'Testing...', async () => { await api(`/integrations/${t.dataset.id}/test`, { method: 'POST' }); toast('Connection works'); render(); }),
  'sync-integration': t => busy(t, 'Syncing...', async () => {
    const r = await api(`/integrations/${t.dataset.id}/sync`, { method: 'POST' }); toast(`Checked ${plural(r.checked, 'item')}, ${r.changed} changed`); render();
  }),
  'save-webhook-secret': t => busy(t, 'Saving...', async () => {
    await api('/integrations/' + t.dataset.id, { method: 'PATCH', body: { webhook_secret: $('#whsec-' + t.dataset.id).value } }); toast('Signing secret saved'); render();
  }),
  'push-tracker': t => busy(t, 'Sending...', async () => {
    const r = await api(`/initiatives/${t.dataset.id}/push/${t.dataset.provider}`, { method: 'POST' });
    toast(r.created ? `Created ${plural(r.created, 'issue')}` : `Updated ${plural(r.updated, 'issue')}`); render();
  }),
});
G.changes['integration-team'] = async t => {
  try { await api('/integrations/' + t.dataset.id, { method: 'PATCH', body: { target_id: t.value } }); toast('Team saved'); } catch (e) { toast(e.message); }
  render();
};

/* Used by the initiative hand-off tab. */
function trackerPanel(i, integrations, links) {
  const trackers = integrations.filter(p => p.kind === 'tracker' && p.connected);
  if (!trackers.length) {
    return `<div class="panel"><h2>Send to a work tracker</h2><p class="sub small">Connect Linear (more coming) to create issues for this initiative and its stories.</p>
      <div class="actions"><button class="btn small" data-act="goto" data-href="#/integrations">Open integrations</button></div></div>`;
  }
  return `<div class="panel"><h2>Send to a work tracker</h2>${trackers.map(p => {
    const mine = links.filter(l => l.provider === p.id);
    const parent = mine.find(l => l.kind === 'initiative');
    const ready = (p.config || {}).target_id;
    return `<div class="stack" style="margin-bottom:10px"><div class="spread"><b>${esc(p.name)}</b>
        ${parent ? `<a class="tag initiative" href="${esc(parent.external_url)}" target="_blank" rel="noopener">${esc(parent.external_key)} &middot; ${esc(parent.external_status)}</a>` : ''}</div>
      ${ready ? '' : `<p class="small muted" style="margin:0">Choose where to send work on the <a href="#/integrations">integrations page</a> first.</p>`}
      <div class="actions"><button class="btn primary small" data-act="push-tracker" data-id="${i.id}" data-provider="${p.id}" ${ready ? '' : 'disabled'}>${parent ? `Update in ${esc(p.name)}` : `Push to ${esc(p.name)}`}</button></div>
      ${mine.filter(l => l.kind === 'story').length ? `<p class="small muted" style="margin:0">${plural(mine.filter(l => l.kind === 'story').length, 'story', 'stories')} linked as sub-issues.</p>` : ''}</div>`;
  }).join('')}</div>`;
}
