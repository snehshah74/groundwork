/* Public site: swap the header buttons when the visitor already has a session. */
fetch('/api/auth/me').then(r => r.json()).then(d => {
  const cta = document.getElementById('nav-cta');
  if (d.user && cta) cta.innerHTML = '<a class="btn sm primary" href="/app">Open app</a>';
}).catch(() => {});
