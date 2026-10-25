/* Accept a workspace invite. Anyone not signed in is sent to log in first, then returned here. */
const token = location.pathname.split('/').pop();
const $ = id => document.getElementById(id);
const err = msg => { $('err').textContent = msg; $('err').classList.add('show'); };

const me = (await (await fetch('/api/auth/me')).json()).user;
if (!me) location.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
else {
  const r = await fetch('/api/auth/invites/' + encodeURIComponent(token));
  const inv = await r.json().catch(() => ({}));
  if (!r.ok) { $('t').textContent = 'Invite not available'; $('m').textContent = ''; err(inv.detail || 'This invite link is invalid or has expired.'); }
  else {
    $('t').textContent = `Join ${inv.workspace_name}`;
    $('m').textContent = `You are signed in as ${me.email}. You will join as ${inv.role}.`;
    $('act').hidden = false;
    $('accept').addEventListener('click', async () => {
      $('err').classList.remove('show'); $('accept').disabled = true;
      const a = await fetch('/api/auth/invites/accept', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
      const d = await a.json().catch(() => ({}));
      if (a.ok) return location.replace('/app');
      err(d.detail || 'Could not accept the invite.'); $('accept').disabled = false;
      if (a.status === 403) $('switch').hidden = false;
    });
    $('switch').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST' });
      location.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
    });
  }
}
