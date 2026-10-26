/* Groundwork front end, part 6: first-run setup in three steps. */
'use strict';

G.wiz = { step: 1 };

async function wizFinish(go_to = '#/home') {
  try { await api('/settings', { method: 'PUT', body: { onboarded: true } }); } catch (e) { /* not an admin: nothing to mark */ }
  G.wiz = { step: 1 }; go(go_to);
}

G.views.welcome = async () => {
  const [product, status, probs] = await Promise.all([api('/product'), api('/status'), api('/problems')]);
  const step = G.wiz.step;
  const bar = `<div class="wiz-steps" aria-label="Step ${step} of 3"><i class="${step >= 1 ? 'on' : ''}"></i><i class="${step >= 2 ? 'on' : ''}"></i><i class="${step >= 3 ? 'on' : ''}"></i></div>
    <p class="small muted" style="margin:6px 0 22px">Step ${step} of 3</p>`;
  const skip = '<button class="btn quiet" data-act="wiz-skip">Skip setup</button>';

  if (step === 1) {
    return `<div class="wizard">${bar}<h1>Name your product</h1>
      <p class="sub">A workspace holds everything for one product: what customers said, the problems found, and the work planned. Add more workspaces later for other products.</p>
      <div class="stack" style="margin-top:22px">
        <label class="field"><span>Product name</span><input type="text" id="w-name" value="${esc(product.name)}" placeholder="For example, Ledgerly" autofocus></label>
        <label class="field"><span>Workspace name</span><input type="text" id="w-ws" value="${esc(G.me.workspace.name)}"></label>
        <label class="field"><span>What does it do? <span class="muted">(optional, a sentence or two)</span></span>
          <textarea id="w-overview" rows="3" placeholder="Who it is for and the main problem it solves.">${esc(product.overview)}</textarea></label>
        <div class="actions"><button class="btn primary" data-act="wiz-step1">Next</button>${skip}</div></div></div>`;
  }
  if (step === 2) {
    const n = status.evidence;
    return `<div class="wizard">${bar}<h1>Add what customers have said</h1>
      <p class="sub">Interviews, support tickets, reviews, survey answers, documents. Paste a few quotes to start; more can come later from files or integrations. You have <b>${plural(n, 'piece')}</b> of evidence.</p>
      <div class="stack" style="margin-top:22px">
        <div class="panel stack"><h2>Paste feedback</h2>
          <textarea id="w-text" rows="4" placeholder="Paste a quote, a ticket or your notes. Separate several with a blank line."></textarea>
          <div class="actions"><select id="w-src" aria-label="Source"><option>interview</option><option>support</option><option>review</option><option>survey</option><option>note</option></select>
            <button class="btn primary" data-act="wiz-paste">Add</button></div></div>
        <div class="panel stack"><h2>Upload a file</h2><p class="small muted" style="margin:0">PDF, Word, Markdown, text or CSV, up to 10 MB.</p>
          <div><label class="btn" style="cursor:pointer">Choose a file<input type="file" accept=".csv,.txt,.md,.pdf,.docx" hidden data-change="wiz-upload"></label></div></div>
        ${n === 0 ? `<div class="panel stack"><h2>Just exploring?</h2><p class="small muted" style="margin:0">Load a made-up invoicing product with 24 pieces of feedback, and walk the whole flow.</p>
          <div><button class="btn" data-act="wiz-seed">Load a demo workspace</button></div></div>` : ''}
        <div class="actions"><button class="btn" data-act="wiz-back">Back</button><button class="btn primary" data-act="wiz-step2" ${n ? '' : 'disabled'}>Next</button>${skip}</div></div></div>`;
  }
  const ungrouped = status.evidence - status.grouped;
  return `<div class="wizard">${bar}<h1>Here is what your evidence says</h1>
    <p class="sub">${probs.length ? `Groundwork found ${plural(probs.length, 'customer problem')}. Each circle on the problem map is one of them, sized by how much evidence backs it.` : 'Groundwork groups evidence into the problems customers are struggling with.'}</p>
    <div class="stack" style="margin-top:22px">
      ${probs.length ? `<div class="rows">${probs.slice(0, 8).map(p => `<div class="row" style="grid-template-columns:auto 1fr auto;align-items:center">${severityTag(p.severity)}<b>${esc(p.title)}</b><span class="muted small">${plural(p.evidence_count, 'item')}</span></div>`).join('')}</div>` : ''}
      ${ungrouped > 0 ? `<div class="banner"><span><b>${plural(ungrouped, 'item')}</b> not grouped yet.</span><button class="btn primary small" data-act="wiz-analyze">Find problems</button></div>` : ''}
      <div class="actions"><button class="btn" data-act="wiz-back">Back</button>
        <button class="btn primary" data-act="wiz-finish">${probs.length ? 'Open the problem map' : 'Finish'}</button></div></div></div>`;
};

Object.assign(G.actions, {
  'wiz-step1': t => busy(t, 'Saving...', async () => {
    const name = $('#w-name').value.trim(), ws = $('#w-ws').value.trim();
    if (!name) throw new Error('Give your product a name.');
    await api('/product', { method: 'PUT', body: { name, overview: $('#w-overview').value } });
    if (ws && ws !== G.me.workspace.name) { await api('/team/workspace', { method: 'PATCH', body: { name: ws } }); G.me.workspace.name = ws; }
    G.wiz.step = 2; render();
  }),
  'wiz-back': () => { G.wiz.step = Math.max(1, G.wiz.step - 1); render(); },
  'wiz-step2': () => { G.wiz.step = 3; render(); },
  'wiz-skip': () => wizFinish('#/home'),
  'wiz-finish': () => wizFinish('#/problems'),
  'wiz-paste': t => busy(t, 'Adding...', async () => {
    const chunks = $('#w-text').value.split(/\n\s*\n/).map(x => x.trim()).filter(Boolean);
    if (!chunks.length) throw new Error('Paste some feedback first.');
    for (const c of chunks) await api('/evidence', { method: 'POST', body: { text: c, source: $('#w-src').value } });
    toast(`Added ${plural(chunks.length, 'item')}`); render();
  }),
  'wiz-seed': t => busy(t, 'Loading...', async () => {
    await api('/seed', { method: 'POST' }); toast('Demo loaded'); G.wiz.step = 3; render();
  }),
  'wiz-analyze': t => busy(t, 'Analyzing...', async () => { await api('/analyze', { method: 'POST', body: { reanalyze: false } }); render(); }),
});
G.changes['wiz-upload'] = async t => {
  const f = t.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f); t.value = '';
  try { const r = await api('/evidence/upload', { method: 'POST', body: fd }); toast(`Added ${plural(r.added, 'item')}`); render(); } catch (e) { toast(e.message); }
};
