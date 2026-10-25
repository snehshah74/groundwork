/* Login and signup forms. */
'use strict';
const form = document.getElementById('auth-form');
const errBox = document.getElementById('form-error');
const submit = document.getElementById('submit');
const toggle = document.getElementById('pw-toggle');

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

form.addEventListener('submit', async e => {
  e.preventDefault();
  errBox.classList.remove('show');
  const body = Object.fromEntries(new FormData(form).entries());
  if (!body.email.trim()) return fail('Enter your email address.', form.email);
  if (!body.password) return fail('Enter your password.', form.password);
  if (form.dataset.mode === 'signup' && body.password.length < 8) return fail('Use at least 8 characters for your password.', form.password);
  const label = submit.textContent;
  submit.disabled = true; submit.textContent = 'One moment...';
  try {
    const r = await fetch('/api/auth/' + form.dataset.mode, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Something went wrong. Try again.');
    location.href = '/app';
  } catch (x) {
    fail(x.message);
    submit.disabled = false; submit.textContent = label;
  }
});
