"""Initiative documents: a PRD, a launch plan and a clickable prototype.

The templates here are the offline versions, built only from what is in the workspace. The prototype is HTML, and
HTML written by a model is untrusted code (customer evidence in the prompt can try to steer it), so harden_html()
strips what can reach the network and adds a Content-Security-Policy; the UI shows it only in a sandboxed iframe.
"""
from __future__ import annotations

import html
import re

MAX_HTML_BYTES = 200_000
CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'"
KINDS = ("prd", "launch", "prototype")


def _bullets(items: list[str], box: bool = False) -> str:
    return "\n".join(f"- {'[ ] ' if box else ''}{x}" for x in items) or "- Not specified"


def _snippet(text: str, n: int = 200) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 3] + "..."


def prd_markdown(i: dict, evidence: list[dict], product: dict) -> str:
    ctx = f"Product: {product['name']}. {product['overview']}".strip() if product.get("name") or product.get("overview") else ""
    quotes = [f'- [#{e["id"]}] ({e["source"]}) "{_snippet(e["text"])}"' for e in evidence[:8]]
    more = f"\n- ...and {len(evidence) - 8} more in Groundwork" if len(evidence) > 8 else ""
    groups: dict[str, list[dict]] = {}
    for s in i.get("stories", []):
        groups.setdefault(s["activity"] or "General", []).append(s)
    reqs = []
    for activity, stories in groups.items():
        reqs.append(f"### {activity}")
        for s in stories:
            reqs.append(f"- **{s['title']}**" + (f": {s['description']}" if s["description"] else ""))
            reqs += [f"  - [ ] {a}" for a in s["acceptance"]]
    if not reqs:
        reqs = ["No stories yet. Build the story map, or use the acceptance criteria below."]
    metric = i["success_metric"] or "Not set"
    return "\n".join([
        f"# PRD: {i['title']}", "", f"_Initiative {i['id']}, status {i['status']}. Drafted from Groundwork evidence; edit freely._", "",
        "## Overview", i["problem_statement"] or i["problem_title"], *([ "", ctx] if ctx else []), "",
        "## Problem and evidence", i.get("problem_summary") or i["problem_title"], "", f"Evidence ({len(evidence)} items):", *quotes, more.strip("\n"), "",
        "## Who it is for", i["persona"] or "Not specified", "",
        "## Success metric", metric, "",
        "## Requirements", *reqs, "",
        "### Acceptance criteria", _bullets(i["acceptance_criteria"], box=True), "",
        "## Out of scope", _bullets(i["out_of_scope"]), "",
        "## Open questions",
        f"- Who should see this first? (Assumption: everyone in {i['persona'] or 'the target group'}.)",
        f"- How will \"{metric}\" be measured, and who owns that number?",
        "- What is the smallest release that tests whether this solves the problem?", "",
    ])


def launch_markdown(i: dict, product: dict) -> str:
    n = len(i.get("evidence", []))
    sources = ", ".join(sorted({e["source"] for e in i.get("evidence", [])})) or "no sources yet"
    notes = [s["title"] for s in i.get("stories", [])][:10] or [i["title"]]
    metric = i["success_metric"] or "the success metric (not set yet)"
    return "\n".join([
        f"# Launch plan: {i['title']}", "",
        "## Audience", f"{i['persona'] or 'The people who raised this problem'}. {n} pieces of customer evidence behind it, from {sources}.", "",
        "## Positioning", f"Problem: {i['problem_statement'] or i['problem_title']}", f"Promise: {i['title']}.", "",
        "## Announcement draft", f"**{i['title']}**", "",
        f"We heard from customers that {(i['problem_statement'] or i['problem_title']).rstrip('.')}. [Describe what is new and how to use it.]", "",
        "## Release notes", _bullets(notes), "",
        "## Checklist",
        "- [ ] Help-center and documentation updated",
        "- [ ] Support briefed on what changed and the known limits",
        f"- [ ] Usage events in place to measure: {metric}",
        "- [ ] Rollout agreed: who sees it first, and how to roll back",
        "- [ ] Announcement sent to the customers who raised this", "",
        "## How we will know it worked", f"Target: {metric}.", "Record the result in Groundwork (Outcome tab) once there is enough data, so the loop closes.", "",
    ])


_PROTO_STYLE = ("body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f3f4f6;color:#16191f}header{background:#16191f;color:#fff;padding:14px 20px;"
                "display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}header span{opacity:.75;font-size:13px}nav{display:flex;gap:8px;padding:14px 20px;"
                "flex-wrap:wrap;background:#fff;border-bottom:1px solid #dde0e6}nav button{border:1px solid #c3c9d3;background:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}"
                "nav button[aria-pressed=true]{background:#074ce0;color:#fff;border-color:#074ce0}main{padding:20px;max-width:880px;margin:0 auto}"
                "section[hidden]{display:none}.card{background:#fff;border:1px solid #dde0e6;border-radius:12px;padding:16px 18px;margin-bottom:14px}"
                ".card h3{margin:0 0 6px;font-size:16px}.card p{margin:0 0 8px;color:#454c58}.card ul{margin:0 0 10px;padding-left:20px;color:#454c58}"
                ".card button{border:0;background:#074ce0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer}.done{border-color:#15803d}"
                ".ok{color:#15803d;font-weight:600;margin-left:8px}")
_PROTO_SCRIPT = ("document.querySelectorAll('nav button').forEach(function(b){b.addEventListener('click',function(){var i=b.dataset.i;"
                 "document.querySelectorAll('nav button').forEach(function(x){x.setAttribute('aria-pressed',x===b)});"
                 "document.querySelectorAll('main section').forEach(function(s){s.hidden=s.dataset.i!==i})})});"
                 "document.querySelectorAll('.card button').forEach(function(b){b.addEventListener('click',function(){var c=b.closest('.card');"
                 "c.classList.toggle('done');b.textContent=c.classList.contains('done')?'Undo':'Try it';"
                 "var m=c.querySelector('.ok');m.textContent=c.classList.contains('done')?'Done (sample)':''})})")


def prototype_html(i: dict) -> str:
    e = html.escape
    groups: dict[str, list[dict]] = {}
    for s in i.get("stories", []):
        groups.setdefault(s["activity"] or "Flow", []).append(s)
    if not groups:
        groups = {"Flow": [{"title": i["title"], "description": i["problem_statement"], "acceptance": i["acceptance_criteria"]}]}
    names = list(groups)
    nav = "".join(f'<button type="button" data-i="{n}" aria-pressed="{str(n == 0).lower()}">{e(a)}</button>' for n, a in enumerate(names))
    secs = ""
    for n, a in enumerate(names):
        cards = "".join(
            f'<div class="card"><h3>{e(s["title"])}</h3>' + (f'<p>{e(s["description"])}</p>' if s.get("description") else "")
            + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in s.get("acceptance", [])) + "</ul>" if s.get("acceptance") else "")
            + '<button type="button">Try it</button><span class="ok"></span></div>' for s in groups[a])
        secs += f'<section data-i="{n}"{"" if n == 0 else " hidden"}>{cards}</section>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{CSP}">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(i["title"])}</title><style>{_PROTO_STYLE}</style></head><body>'
            f'<header><b>{e(i["title"])}</b><span>Prototype from your story map, with sample interactions only</span></header><nav>{nav}</nav>'
            f"<main>{secs}</main><script>{_PROTO_SCRIPT}</script></body></html>")


_DROP = [
    r"<base\b[^>]*>",
    r"<meta\b[^>]*http-equiv\s*=\s*[\"']?(?:refresh|content-security-policy)[^>]*>",
    r"<iframe\b.*?</iframe\s*>", r"<iframe\b[^>]*>",
    r"<object\b.*?</object\s*>", r"<object\b[^>]*>",
    r"<embed\b[^>]*>", r"<link\b[^>]*>",
    r"<script\b[^>]*\bsrc\s*=[^>]*>.*?</script\s*>", r"<script\b[^>]*\bsrc\s*=[^>]*/?>",
]


def harden_html(raw: str) -> str:
    """Make model-written HTML safe to show: no external loads, no navigation, no base or refresh, and a policy that forbids
    network access even if something slips through. Inline CSS and JS (the prototype's own behaviour) are kept."""
    text = raw.strip()
    fence = re.match(r"^```(?:html)?\s*(.*?)\s*```$", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1)
    text = text[: MAX_HTML_BYTES - 4000]
    for pattern in _DROP:
        text = re.sub(pattern, "", text, flags=re.S | re.I)
    text = re.sub(r"\saction\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text, flags=re.I)
    csp = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    if re.search(r"<head\b[^>]*>", text, flags=re.I):
        text = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + csp, text, count=1, flags=re.I)
    elif re.search(r"<html\b[^>]*>", text, flags=re.I):
        text = re.sub(r"(<html\b[^>]*>)", lambda m: m.group(1) + f"<head>{csp}</head>", text, count=1, flags=re.I)
    else:
        text = f'<!doctype html><html lang="en"><head><meta charset="utf-8">{csp}</head><body>{text}</body></html>'
    if not re.match(r"\s*<!doctype", text, flags=re.I):
        text = "<!doctype html>" + text
    return text[:MAX_HTML_BYTES]
