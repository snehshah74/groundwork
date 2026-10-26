/* Password reset through Supabase: ask for a link, then (arriving from that link) choose a new password. */
import { loadConfig, supabaseClient } from '/static/supa.js';

const $ = id => document.getElementById(id);
const show = (id, msg) => { $(id).textContent = msg; $(id).classList.add('show'); };
const cfg = await loadConfig();

if (cfg.mode !== 'supabase') {
  $('ask').hidden = true;
  show('err', 'Password reset is handled by Supabase and is not set up here. In local development, create a new account instead.');
} else {
  const sb = await supabaseClient(cfg);
  // Arriving from the emailed link, Supabase finds the recovery token in the URL and announces it.
  sb.auth.onAuthStateChange(event => {
    if (event === 'PASSWORD_RECOVERY') {
      $('t').textContent = 'Choose a new password'; $('m').textContent = 'You are almost done.';
      $('ask').hidden = true; $('set').hidden = false; $('pw').focus();
    }
  });
  $('ask').addEventListener('submit', async e => {
    e.preventDefault(); $('err').classList.remove('show');
    const email = $('email').value.trim();
    if (!email) return show('err', 'Enter your email address.');
    const { error } = await sb.auth.resetPasswordForEmail(email, { redirectTo: `${location.origin}/reset` });
    if (error) return show('err', error.message);
    // The same message whether or not the address has an account, so this cannot be used to find out who is registered.
    show('ok', 'If that address has an account, a reset link is on its way. It can take a minute.');
  });
  $('set').addEventListener('submit', async e => {
    e.preventDefault(); $('err').classList.remove('show');
    const pw = $('pw').value;
    if (pw.length < 8) return show('err', 'Use at least 8 characters for your password.');
    const { error } = await sb.auth.updateUser({ password: pw });
    if (error) return show('err', error.message);
    await sb.auth.signOut({ scope: 'local' });
    $('set').hidden = true; $('t').textContent = 'Password updated'; $('m').textContent = '';
    show('ok', 'Your password was changed. You can log in with it now.');
  });
}
