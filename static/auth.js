/* Login and signup. Supabase handles Google, Microsoft and email when configured; otherwise a local password form. */
import { exchange, loadConfig, nextPath, supabaseClient } from '/static/supa.js';

const form = document.getElementById('auth-form');
const errBox = document.getElementById('form-error');
const submit = document.getElementById('submit');
const toggle = document.getElementById('pw-toggle');
const signup = form.dataset.mode === 'signup';
let sb = null;

toggle.addEventListener('click', () => {
  const pw = document.getElementById('password');
  const show = pw.type === 'password';
  pw.type = show ? 'text' : 'password';
  toggle.textContent = show ? 'Hide' : 'Show';
  toggle.setAttribute('aria-pressed', String(show));
});

function fail(msg, field) {
  errBox.textContent = msg; errBox.classList.add('show');
  form.querySelectorAll('.input').forEach(i => i.removeAttribute('aria-invalid'));
  if (field) { field.setAttribute('aria-invalid', 'true'); field.focus(); }
}
function done() { location.href = nextPath(); }

// carry ?next through the links between login and signup
const qs = location.search;
document.querySelectorAll('.alt a').forEach(a => { a.href += qs; });

const cfg = await loadConfig();
if (cfg.mode === 'supabase') {
  sb = await supabaseClient(cfg);
  document.getElementById('oauth').hidden = false;
  document.querySelectorAll('[data-provider]').forEach(btn => btn.addEventListener('click', async () => {
    errBox.classList.remove('show');
    const provider = btn.dataset.provider;
    const { error } = await sb.auth.signInWithOAuth({
      provider,
      options: { redirectTo: `${location.origin}/auth/callback?next=${encodeURIComponent(nextPath())}`, scopes: provider === 'azure' ? 'email' : undefined },
    });
    if (error) fail(error.message);
  }));
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  errBox.classList.remove('show');
  const body = Object.fromEntries(new FormData(form).entries());
  if (!body.email.trim()) return fail('Enter your email address.', form.email);
  if (!body.password) return fail('Enter your password.', form.password);
  if (signup && body.password.length < 8) return fail('Use at least 8 characters for your password.', form.password);
  const label = submit.textContent;
  submit.disabled = true; submit.textContent = 'One moment...';
  try {
    if (sb) {
      if (signup) {
        const { data, error } = await sb.auth.signUp({
          email: body.email.trim(), password: body.password,
          options: { data: { full_name: body.name || '' }, emailRedirectTo: `${location.origin}/auth/callback?next=${encodeURIComponent(nextPath())}` },
        });
        if (error) throw error;
        if (!data.session) {   // email confirmation is on: the link in the email finishes sign-in
          form.innerHTML = `<div class="form-note show" role="status">Check <b></b> for a confirmation link, then come back here.</div>`;
          form.querySelector('b').textContent = body.email.trim();
          return;
        }
        await exchange(sb, data.session.access_token);
      } else {
        const { data, error } = await sb.auth.signInWithPassword({ email: body.email.trim(), password: body.password });
        if (error) throw error;
        await exchange(sb, data.session.access_token);
      }
      return done();
    }
    const r = await fetch('/api/auth/' + form.dataset.mode, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Something went wrong. Try again.');
    done();
  } catch (x) {
    fail(x.message || 'Something went wrong. Try again.');
    submit.disabled = false; submit.textContent = label;
  }
});
