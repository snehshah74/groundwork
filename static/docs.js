/* Groundwork front end, part 8: the Documents tab of an initiative (PRD, launch plan, prototype). */
'use strict';

/* Minimal Markdown to HTML. Everything is escaped first and tags are added afterwards, so document text can never inject markup. */
function mdToHtml(src) {
  const inline = t => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[\s(])_(.+?)_(?=[\s).,;:]|$)/g, '$1<i>$2</i>')
    .replace(/`(.+?)`/g, '<code>$1</code>').replace(/\[#(\d+)\]/g, '<span class="cite" title="Evidence #$1">#$1</span>');
  const out = []; let list = null, para = [];
  const endPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const endList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(src).replace(/\r\n/g, '\n').split('\n')) {
    const line = raw.replace(/\s+$/, '');
    let m;
    if (!line.trim()) { endPara(); endList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { endPara(); endList(); const n = Math.min(m[1].length + 1, 5); out.push(`<h${n}>${inline(m[2])}</h${n}>`); continue; }
    if ((m = line.match(/^(\s*)[-*]\s+(?:\[( |x)\]\s+)?(.*)$/i))) {
      endPara(); if (list !== 'ul') { endList(); out.push('<ul>'); list = 'ul'; }
      const box = m[2] === undefined ? '' : (m[2].toLowerCase() === 'x' ? '&#9746; ' : '&#9744; ');
      out.push(`<li style="margin-left:${Math.min(m[1].length, 6) * 8}px">${box}${inline(m[3])}</li>`); continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { endPara(); if (list !== 'ol') { endList(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    endList(); para.push(line.trim());
  }
  endPara(); endList();
  return out.join('');
}

const DOC_KINDS = [
  ['prd', 'Product requirements (PRD)', 'A written spec your team and coding agent can build from: problem, evidence, requirements, scope and open questions.', 2],
  ['launch', 'Launch plan', 'Audience, positioning, an announcement draft, release notes and a checklist, tied to the success metric.', 2],
  ['prototype', 'Clickable prototype', 'A sample flow drawn from the story map, to react to before anything is built. It runs in an isolated frame and cannot reach the network.', 3],
];

async function docsTab(i) {
  const docs = await api(`/initiatives/${i.id}/documents`);
  const by = Object.fromEntries(docs.map(d => [d.kind, d]));
  const ai = G.status && G.status.ai_mode === 'claude';
  return `<p class="sub" style="margin-top:0;max-width:68ch">Turn this initiative into documents. ${ai ? 'Claude writes them from your evidence and spec; each costs AI credits.' : 'Template drafts built from your evidence and spec. Turn on AI for fuller ones.'}</p>
    <div class="stack gap-lg">${DOC_KINDS.map(([kind, name, blurb, cost]) => {
      const d = by[kind];
      const editing = d && G.docEdit === d.id;
      const head = `<div class="spread"><div><h2 style="margin:0">${esc(name)}</h2><p class="small muted" style="margin:2px 0 0">${esc(blurb)}</p></div>
        <div class="actions">${d ? `<span class="tag ${d.mode === 'claude' ? 'initiative' : 'muted'}">${d.mode === 'claude' ? 'Written by Claude' : 'Template'}</span>` : ''}
          <button class="btn small ${d ? '' : 'primary'}" data-act="gen-doc" data-id="${i.id}" data-kind="${kind}">${d ? 'Regenerate' : 'Generate'}${ai ? ` (${cost} credits)` : ''}</button></div></div>`;
      if (!d) return `<div class="panel stack">${head}</div>`;
      const tools = `<div class="actions"><a class="btn small" href="/api/documents/${d.id}/download" download>Download</a>
        ${kind !== 'prototype' ? (editing ? `<button class="btn small primary" data-act="save-doc" data-id="${d.id}">Save</button><button class="btn small quiet" data-act="cancel-doc">Cancel</button>`
          : `<button class="btn small" data-act="edit-doc" data-id="${d.id}">Edit</button>`) : ''}
        <span class="small muted">Version from ${fmtDate(d.created_at)}</span></div>`;
      const body = kind === 'prototype'
        ? `<iframe class="proto-frame" title="Prototype preview" sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc="${esc(d.body)}"></iframe>`
        : editing ? `<textarea id="doc-body" class="doc-edit">${esc(d.body)}</textarea>` : `<div class="doc">${mdToHtml(d.body)}</div>`;
      return `<div class="panel stack">${head}${tools}${body}</div>`;
    }).join('')}</div>`;
}

Object.assign(G.actions, {
  'gen-doc': t => busy(t, 'Writing...', async () => {
    await api(`/initiatives/${t.dataset.id}/documents/${t.dataset.kind}`, { method: 'POST' });
    toast('Document ready'); G.docEdit = null; render();
  }),
  'edit-doc': t => { G.docEdit = +t.dataset.id; render(); },
  'cancel-doc': () => { G.docEdit = null; render(); },
  'save-doc': t => busy(t, 'Saving...', async () => {
    await api('/documents/' + t.dataset.id, { method: 'PUT', body: { body: $('#doc-body').value } });
    toast('Saved'); G.docEdit = null; render();
  }),
});
