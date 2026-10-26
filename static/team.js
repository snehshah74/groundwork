/* Groundwork front end, part 3: team (members, roles, invites). */
'use strict';

const ROLE_HELP = {
  owner: 'Everything, including owners and deleting the workspace',
  admin: 'Manage members, invites and integrations',
  member: 'Create and edit evidence, problems, initiatives',
  viewer: 'Read only',
};
const roleLabel = r => r[0].toUpperCase() + r.slice(1);
const AUDIT_LABEL = {
  'member.invite': 'invited', 'invite.accept': 'accepted an invite:', 'invite.revoke': 'revoked', 'member.role': 'changed role of',
  'member.remove': 'removed', 'member.leave': 'left the workspace:', 'integration.connect': 'connected', 'integration.disconnect': 'disconnected',
  'integration.push': 'pushed', 'workspace.rename': 'renamed the workspace to', 'workspace.export': 'exported data:', 'workspace.reset': 'reset the workspace:',
  'token.rotate': 'rotated their agent token',
};

G.views.team = async () => {
  const t = await api('/team');
  const role = t.workspace.role;
  const admin = role === 'admin' || role === 'owner';
  const audit = admin ? await api('/team/audit') : [];
  const settings = await api('/settings');
  const roleSelect = (m) => {
    const locked = !admin || (m.role === 'owner' && role !== 'owner');
    const opts = t.roles.filter(r => role === 'owner' || r !== 'owner');
    return `<select data-change="member-role" data-id="${m.user_id}" aria-label="Role for ${esc(m.email)}" ${locked ? 'disabled' : ''}>
      ${opts.map(r => `<option value="${r}" ${m.role === r ? 'selected' : ''}>${roleLabel(r)}</option>`).join('')}</select>`;
  };
  const head = `<header class="page-head"><div><h1>Team</h1>
    <p>Invite people to <b>${esc(t.workspace.name)}</b> and choose what each can do. Your role: ${roleLabel(role)}.</p></div>
    <div class="actions">${admin ? '<button class="btn" data-act="rename-workspace">Rename workspace</button>' : ''}</div></header>`;

  const members = `<table class="tbl"><thead><tr><th>Member</th><th>Role</th><th></th></tr></thead><tbody>
    ${t.members.map(m => `<tr><td class="who"><b>${esc(m.name || m.email.split('@')[0])}${m.user_id === t.you ? ' <span>(you)</span>' : ''}</b><span>${esc(m.email)}</span></td>
      <td>${roleSelect(m)}</td>
      <td style="text-align:right">${m.user_id === t.you
        ? '<button class="btn small" data-act="leave-workspace">Leave</button>'
        : admin ? `<button class="icon-btn" data-act="remove-member" data-id="${m.user_id}" data-email="${esc(m.email)}" aria-label="Remove ${esc(m.email)}">&#10005;</button>` : ''}</td></tr>`).join('')}
    </tbody></table>`;

  const roles = `<div class="panel stack"><h2>Roles</h2>${['owner', 'admin', 'member', 'viewer'].map(r => `<p class="small" style="margin:0"><b>${roleLabel(r)}</b><br><span class="muted">${ROLE_HELP[r]}</span></p>`).join('')}</div>`;

  const invite = admin ? `<div class="panel stack"><h2>Invite someone</h2>
      <label class="field"><span>Email</span><input type="text" id="inv-email" inputmode="email" placeholder="teammate@company.com"></label>
      <label class="field"><span>Role</span><select id="inv-role"><option value="member">Member</option><option value="admin">Admin</option><option value="viewer">Viewer</option></select></label>
      <div><button class="btn primary" data-act="create-invite">Create invite link</button></div>
      <p class="small muted" style="margin:0">Groundwork does not send email. Copy the link and share it. It works once, for that email address, for 7 days.</p>
      <div id="inv-result"></div>
      ${t.invites.length ? `<h3 class="section-title" style="margin-top:8px">Pending (${t.invites.length})</h3>${t.invites.map(i => `<div class="spread" style="padding:6px 0"><span class="small">${esc(i.email)} <span class="muted">${esc(i.role)}</span></span>
        <button class="icon-btn" data-act="revoke-invite" data-id="${i.id}" aria-label="Revoke invite for ${esc(i.email)}">&#10005;</button></div>`).join('')}` : ''}</div>` : '';

  const danger = role === 'owner' ? `<div class="danger-zone" style="margin-top:28px"><div><b>Delete this workspace</b><div class="small muted">Removes all evidence, initiatives and connections for everyone. This cannot be undone.</div></div>
      <button class="btn danger" data-act="delete-workspace">Delete workspace</button></div>` : '';

  const activity = admin ? `<h2 class="group-title">Activity</h2>
    <div class="panel">${audit.length ? audit.slice(0, 30).map(e => `<div class="log-row"><span class="muted small">${ago(e.created_at)}</span>
      <span class="small"><b>${esc(e.email)}</b> ${esc(AUDIT_LABEL[e.action] || e.action)} ${esc(e.detail)}</span></div>`).join('') : '<p class="muted small" style="margin:0">Nothing yet.</p>'}
      <div class="actions" style="margin-top:12px"><a class="btn small" href="/api/team/export" download>Export all workspace data (JSON)</a></div></div>` : '';

  const prefs = `<h2 class="group-title">Workspace settings</h2><div class="panel">
    <label class="switch"><input type="checkbox" data-change="setting-auto" ${settings.auto_analyze ? 'checked' : ''} ${admin ? '' : 'disabled'}>
      <span><b>Group new evidence automatically</b><br><span class="small muted">When evidence arrives, from you, an upload or an integration, it joins the matching problem, or forms a new one once enough has piled up. Turn off to group by hand.</span></span></label></div>`;

  const account = `<h2 class="group-title">Your account</h2><div class="danger-zone"><div><b>Delete my account</b>
    <div class="small muted">Removes your login and any workspace where you are the only member. Workspaces you share with others stay, and you must make someone else an owner first.</div></div>
    <button class="btn danger" data-act="delete-account">Delete my account</button></div>`;

  return head + `<div class="two-col"><div>${members}${activity}</div><div class="stack gap-lg">${invite}${roles}</div></div>${prefs}${danger}${account}`;
};

Object.assign(G.actions, {
  'create-invite': t => busy(t, 'Creating...', async () => {
    const r = await api('/team/invites', { method: 'POST', body: { email: $('#inv-email').value, role: $('#inv-role').value } });
    $('#inv-result').innerHTML = `<p class="small" style="margin:0 0 4px">Invite link for <b>${esc(r.email)}</b>:</p>
      <div class="link-box"><input type="text" readonly value="${esc(r.link)}" aria-label="Invite link"><button class="btn small" data-act="copy-text" data-msg="Link copied" data-text="${esc(r.link)}">Copy</button></div>`;
    $('#inv-email').value = '';
  }),
  'revoke-invite': async t => { await api('/team/invites/' + t.dataset.id, { method: 'DELETE' }); toast('Invite revoked'); render(); },
  'remove-member': async t => {
    if (!confirm(`Remove ${t.dataset.email} from this workspace?`)) return;
    try { await api('/team/members/' + t.dataset.id, { method: 'DELETE' }); toast('Member removed'); } catch (e) { toast(e.message); }
    render();
  },
  'leave-workspace': async () => {
    if (!confirm('Leave this workspace?')) return;
    try { await api(`/auth/workspaces/${G.me.workspace.id}/leave`, { method: 'POST' }); location.href = '/app#/home'; location.reload(); } catch (e) { toast(e.message); }
  },
  'rename-workspace': () => modal({
    title: 'Rename workspace', submit: 'Save', fields: [{ name: 'name', label: 'Name', value: G.me.workspace.name }],
    onSubmit: async v => { await api('/team/workspace', { method: 'PATCH', body: { name: v.name } }); location.reload(); },
  }),
  'delete-account': async () => {
    const email = prompt(`This cannot be undone. Type your email address (${G.me.email}) to delete your account.`);
    if (email === null) return;
    try { await api('/auth/account', { method: 'DELETE', body: { confirm: email } }); location.href = '/'; } catch (e) { toast(e.message); }
  },
  'rotate-token': async () => {
    if (!confirm('Replace your agent token? Agents using the old one stop working until you update them.')) return;
    G.me.api_token = (await api('/team/token/rotate', { method: 'POST' })).api_token; toast('New token created'); render();
  },
  'delete-workspace': async () => {
    if (prompt(`Type the workspace name to delete it: ${G.me.workspace.name}`) !== G.me.workspace.name) return toast('Name did not match. Nothing was deleted.');
    await api('/team/workspace', { method: 'DELETE' }); location.href = '/app#/home'; location.reload();
  },
});
G.changes['setting-auto'] = async t => {
  try { await api('/settings', { method: 'PUT', body: { auto_analyze: t.checked } }); toast(t.checked ? 'Automatic grouping is on' : 'Automatic grouping is off'); } catch (e) { toast(e.message); t.checked = !t.checked; }
};
G.changes['member-role'] = async t => {
  try { await api('/team/members/' + t.dataset.id, { method: 'PATCH', body: { role: t.value } }); toast('Role updated'); } catch (e) { toast(e.message); }
  render();
};
