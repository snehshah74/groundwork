/* Finishes a Google, Microsoft or email-link sign-in: Supabase leaves a session in the URL. */
import { exchange, loadConfig, nextPath, supabaseClient } from '/static/supa.js';

const fail = msg => {
  document.getElementById('t').textContent = 'Sign-in did not finish';
  document.getElementById('m').textContent = '';
  const e = document.getElementById('err'); e.textContent = msg; e.classList.add('show');
  document.getElementById('back').hidden = false;
};
try {
  const cfg = await loadConfig();
  if (cfg.mode !== 'supabase') throw new Error('Sign-in with Google or Microsoft is not set up here.');
  const sb = await supabaseClient(cfg);
  const hashErr = new URLSearchParams(location.hash.slice(1)).get('error_description') || new URLSearchParams(location.search).get('error_description');
  if (hashErr) throw new Error(hashErr);
  const { data, error } = await sb.auth.getSession();   // waits for Supabase to read the code or token from the URL
  if (error || !data.session) throw new Error(error ? error.message : 'No sign-in found. Try again from the log in page.');
  await exchange(sb, data.session.access_token);
  location.replace(nextPath());
} catch (x) { fail(x.message); }
