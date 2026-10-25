/* Shared sign-in helpers: Supabase client, safe redirect target, and the token exchange with the server. */
export const SUPABASE_JS = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm';

export function nextPath() {
  const n = new URLSearchParams(location.search).get('next') || '/app';
  return n.startsWith('/') && !n.startsWith('//') ? n : '/app';   // same-site paths only
}

export async function loadConfig() {
  return (await fetch('/api/config')).json();
}

export async function supabaseClient(cfg) {
  const { createClient } = await import(SUPABASE_JS);
  return createClient(cfg.supabase_url, cfg.supabase_anon_key);
}

/** Trade the Supabase access token for a Groundwork session, then drop the browser-side Supabase tokens. */
export async function exchange(sb, accessToken) {
  const r = await fetch('/api/auth/exchange', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ access_token: accessToken }) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Sign-in failed. Try again.');
  await sb.auth.signOut({ scope: 'local' });
}
