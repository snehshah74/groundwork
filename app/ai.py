"""AI layer: cluster evidence into problems and draft initiative specs.

Uses Claude when ANTHROPIC_API_KEY is set. Without a key it falls back to a local
TF-IDF clustering and template specs, so the whole product still runs offline.
"""
from __future__ import annotations

import json
import os
import re

MODEL = os.environ.get("GROUNDWORK_MODEL", "claude-sonnet-5-5")
MAX_CLUSTER_ITEMS = 250   # per analysis run; anything beyond stays ungrouped for the next run


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    import anthropic

    return anthropic.Anthropic()


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = min([i for i in (text.find("["), text.find("{")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON found in model output")
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    end = text.rfind(closer)
    return json.loads(text[start : end + 1])


def _ask(system: str, user: str, max_tokens: int = 4000):
    msg = _client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return _extract_json("".join(b.text for b in msg.content if b.type == "text"))


# ---------------------------------------------------------------- clustering

CLUSTER_SYSTEM = (
    "You are a product discovery analyst. You receive raw customer evidence (interviews, support tickets, "
    "reviews, notes). Group items into distinct customer PROBLEMS, not feature requests. Each problem should "
    "describe what the customer is struggling with and why. Return ONLY a JSON array. Each element: "
    '{"title": short problem name, "summary": 1-2 sentences, "persona": who has this problem, '
    '"severity": "low"|"medium"|"high", "evidence_ids": [ids]}. Every evidence id must appear in exactly one '
    "problem. Prefer 3 to 8 problems. Do not use em dashes."
)


def cluster_evidence(items: list[dict]) -> tuple[list[dict], str]:
    """items: [{id, source, text}] -> (clusters, mode)"""
    if not items:
        return [], "none"
    if ai_enabled():
        try:
            batch = items[:MAX_CLUSTER_ITEMS]
            payload = [{"id": e["id"], "source": e["source"], "text": e["text"][:500]} for e in batch]
            data = _ask(CLUSTER_SYSTEM, json.dumps(payload))
            clusters = _validate_clusters(data, {e["id"] for e in batch})   # only what was sent, or the rest would land in "Other feedback"
            if clusters:
                return clusters, "claude"
        except Exception as exc:  # fall through to offline mode
            print(f"[groundwork] Claude clustering failed, using offline mode: {exc}")
    try:
        return _offline_cluster(items), "offline"
    except ValueError:  # e.g. text made only of stop words
        return [_cluster_from(items, items_label(items), [e["id"] for e in items], len(items))], "offline"


def _validate_clusters(data, valid_ids: set[int]) -> list[dict]:
    out, seen = [], set()
    for cl in data if isinstance(data, list) else []:
        ids = [int(i) for i in cl.get("evidence_ids", []) if int(i) in valid_ids and int(i) not in seen]
        if not ids or not cl.get("title"):
            continue
        seen.update(ids)
        sev = cl.get("severity", "medium")
        out.append(
            {
                "title": str(cl["title"])[:120],
                "summary": str(cl.get("summary", ""))[:500],
                "persona": str(cl.get("persona", ""))[:120],
                "severity": sev if sev in ("low", "medium", "high") else "medium",
                "evidence_ids": ids,
            }
        )
    leftover = sorted(valid_ids - seen)
    if leftover and out:
        out.append(
            {
                "title": "Other feedback",
                "summary": "Evidence that did not fit a clear theme yet.",
                "persona": "",
                "severity": "low",
                "evidence_ids": leftover,
            }
        )
    return out


def _offline_cluster(items: list[dict]) -> list[dict]:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
    from sklearn.preprocessing import normalize

    n = len(items)
    texts = [e["text"] for e in items]
    if n < 4:
        return [_cluster_from(items, items_label(items), [e["id"] for e in items], n)]

    stop = list(ENGLISH_STOP_WORDS | _FILLER)
    vec = TfidfVectorizer(
        stop_words=stop, ngram_range=(1, 2), sublinear_tf=True, min_df=2 if n >= 10 else 1, max_df=0.6
    )
    X = vec.fit_transform(texts)
    terms = vec.get_feature_names_out()
    comps = min(12, X.shape[1] - 1, n - 1)
    Z = normalize(TruncatedSVD(n_components=comps, random_state=7).fit_transform(X)) if comps >= 2 else X.toarray()
    k = max(2, min(8, round(n / 5), n))
    km = KMeans(n_clusters=k, n_init=20, random_state=7).fit(Z)

    clusters = []
    for label in range(k):
        idx = [i for i in range(n) if km.labels_[i] == label]
        if not idx:
            continue
        mean = np.asarray(X[idx].mean(axis=0)).ravel()
        title = " / ".join(terms[t] for t in mean.argsort()[::-1][:3]).title()
        members = [items[i] for i in idx]
        clusters.append(_cluster_from(members, title, [m["id"] for m in members], n))
    return clusters


_FILLER = {
    "way", "like", "just", "need", "wish", "want", "love", "great", "really", "every", "times", "twice",
    "time", "month", "months", "know", "help", "use", "using", "took", "got", "going", "did", "does",
    "hour", "hours", "please", "new", "right", "half", "day", "lot", "thing", "things", "app",
}


def items_label(items: list[dict]) -> str:
    return "General feedback"


def _cluster_from(members: list[dict], title: str, ids: list[int], total: int) -> dict:
    share = len(ids) / max(total, 1)
    severity = "high" if share >= 0.3 or len(ids) >= 6 else "medium" if len(ids) >= 3 else "low"
    summary = members[0]["text"].replace("\n", " ")
    if len(summary) > 200:
        summary = summary[:197] + "..."
    return {
        "title": title,
        "summary": summary,
        "persona": "",
        "severity": severity,
        "evidence_ids": ids,
    }


# ---------------------------------------------------------------- spec

SPEC_SYSTEM = (
    "You are a senior product manager writing a spec that a coding agent will implement. Given a customer "
    "problem and its evidence, return ONLY a JSON object with keys: title (an initiative name, outcome oriented), "
    "theme (strategy theme, 2-4 words), persona (who it is for), problem_statement (2-3 sentences grounded in "
    "the evidence), success_metric (one measurable outcome with a number and a timeframe), out_of_scope "
    "(array of 2-4 strings), acceptance_criteria (array of 3-6 testable strings). Do not use em dashes."
)


def generate_spec(problem: dict, evidence: list[dict]) -> tuple[dict, str]:
    if ai_enabled():
        try:
            payload = {
                "problem": {k: problem.get(k) for k in ("title", "summary", "persona", "severity")},
                "evidence": [{"source": e["source"], "text": e["text"][:500]} for e in evidence[:40]],
            }
            spec = _ask(SPEC_SYSTEM, json.dumps(payload), max_tokens=1500)
            if isinstance(spec, dict) and spec.get("title"):
                return _normalize_spec(spec, problem), "claude"
        except Exception as exc:
            print(f"[groundwork] Claude spec failed, using offline mode: {exc}")
    return _offline_spec(problem, evidence), "offline"


def _normalize_spec(spec: dict, problem: dict) -> dict:
    def as_list(v):
        return [str(x) for x in v] if isinstance(v, list) else []

    return {
        "title": str(spec.get("title") or problem["title"])[:140],
        "theme": str(spec.get("theme", ""))[:60],
        "persona": str(spec.get("persona") or problem.get("persona", ""))[:140],
        "problem_statement": str(spec.get("problem_statement", "")),
        "success_metric": str(spec.get("success_metric", "")),
        "out_of_scope": as_list(spec.get("out_of_scope")),
        "acceptance_criteria": as_list(spec.get("acceptance_criteria")),
    }


def _offline_spec(problem: dict, evidence: list[dict]) -> dict:
    n = len(evidence)
    sources = sorted({e["source"] for e in evidence})
    return {
        "title": f"Resolve: {problem['title']}",
        "theme": "Reduce customer friction",
        "persona": problem.get("persona") or "Customers affected by this problem",
        "problem_statement": (
            f"{n} pieces of evidence across {', '.join(sources) or 'multiple sources'} point to this problem. "
            f"{problem.get('summary', '')}"
        ).strip(),
        "success_metric": "Cut related support contacts by 40% within 6 weeks of release",
        "out_of_scope": ["Redesigning unrelated flows", "New pricing or packaging changes"],
        "acceptance_criteria": [
            "The core scenario described in the evidence can be completed without workarounds",
            "Error and empty states are handled with clear messages",
            "Usage of the new flow is tracked so the success metric can be measured",
        ],
    }


# ---------------------------------------------------------------- canvas

CANVAS_KEYS = [
    "customer_segments", "problem", "unique_value", "solution", "channels",
    "key_metrics", "revenue", "costs", "unfair_advantage",
]

CANVAS_SYSTEM = (
    "You are a product strategist. From a product overview (and customer problems if provided) fill a lean "
    "product canvas. Return ONLY a JSON object with exactly these keys: customer_segments, problem, "
    "unique_value, solution, channels, key_metrics, revenue, costs, unfair_advantage. Each value is a string "
    "of 1 to 4 short lines separated by newline characters, no bullets. Be concrete and specific to this "
    "product. If something is unknown, make a labelled best guess starting with 'Assumption:'. "
    "Do not use em dashes."
)


def generate_canvas(name: str, overview: str, problems: list[dict]) -> tuple[dict, str]:
    if ai_enabled():
        try:
            payload = {
                "name": name,
                "overview": overview,
                "customer_problems": [
                    {"title": p["title"], "summary": p.get("summary", ""), "evidence_count": p.get("evidence_count", 0)}
                    for p in problems[:10]
                ],
            }
            data = _ask(CANVAS_SYSTEM, json.dumps(payload), max_tokens=1500)
            if isinstance(data, dict):
                return {k: str(data.get(k, "")).strip() for k in CANVAS_KEYS}, "claude"
        except Exception as exc:
            print(f"[groundwork] Claude canvas failed, using offline mode: {exc}")
    first = (overview.strip().split(". ")[0] if overview.strip() else "")[:200]
    canvas = {k: "" for k in CANVAS_KEYS}
    canvas["customer_segments"] = first
    canvas["problem"] = "\n".join(p["title"] for p in problems[:3])
    canvas["key_metrics"] = "Activation rate\nWeekly active accounts\nSupport contacts per account"
    return canvas, "offline"


# ---------------------------------------------------------------- themes

THEMES_SYSTEM = (
    "You are a product strategist. Group customer problems into 2 to 5 strategy themes. A theme is a "
    "direction the product could commit to (for example 'Make month-end effortless'), not a category label. "
    "Return ONLY a JSON array. Each element: {\"title\": 2-6 words, \"description\": one sentence on why this "
    "matters to the product, \"problem_ids\": [ids]}. Every problem id appears in exactly one theme. "
    "Do not use em dashes."
)


def generate_themes(product: dict, problems: list[dict]) -> tuple[list[dict], str]:
    if not problems:
        return [], "none"
    valid = {p["id"] for p in problems}
    if ai_enabled():
        try:
            payload = {
                "product": {"name": product.get("name"), "overview": product.get("overview")},
                "problems": [
                    {"id": p["id"], "title": p["title"], "summary": p.get("summary", ""),
                     "severity": p.get("severity"), "evidence_count": p.get("evidence_count", 0)}
                    for p in problems
                ],
            }
            data = _ask(THEMES_SYSTEM, json.dumps(payload), max_tokens=1500)
            out, seen = [], set()
            for t in data if isinstance(data, list) else []:
                ids = [int(i) for i in t.get("problem_ids", []) if int(i) in valid and int(i) not in seen]
                if ids and t.get("title"):
                    seen.update(ids)
                    out.append({"title": str(t["title"])[:80], "description": str(t.get("description", ""))[:300], "problem_ids": ids})
            left = sorted(valid - seen)
            if out and left:
                out[-1]["problem_ids"] += left
            if out:
                return out, "claude"
        except Exception as exc:
            print(f"[groundwork] Claude themes failed, using offline mode: {exc}")
    ranked = sorted(problems, key=lambda p: -p.get("evidence_count", 0))
    core_ids = [p["id"] for p in ranked[:2]]
    rest = [p["id"] for p in ranked[2:]]
    themes = [{"title": "Fix the biggest pains", "description": "The problems with the most customer evidence behind them.", "problem_ids": core_ids}]
    if rest:
        themes.append({"title": "Remove everyday friction", "description": "Smaller problems that add up to a poor experience.", "problem_ids": rest})
    return themes, "offline"


# ---------------------------------------------------------------- story map

STORIES_SYSTEM = (
    "You are a senior product manager building a user story map for a coding agent. Given an initiative, "
    "return ONLY a JSON array of stories ordered left to right along the user's journey. Each element: "
    "{\"activity\": backbone step name (2-4 words, shared by stories in the same step), \"title\": story as "
    "'As a <user>, I can <goal>' (max 100 chars), \"description\": one sentence of context, "
    "\"acceptance_criteria\": [2-4 testable strings]}. Use 3 to 5 activities and 1 to 3 stories each, "
    "6 to 12 stories total. Stay inside the initiative's scope and respect what is out of scope. "
    "Do not use em dashes."
)


def generate_stories(initiative: dict, evidence: list[dict]) -> tuple[list[dict], str]:
    if ai_enabled():
        try:
            payload = {
                "initiative": {k: initiative.get(k) for k in (
                    "title", "persona", "problem_statement", "success_metric", "out_of_scope", "acceptance_criteria")},
                "evidence": [e["text"][:300] for e in evidence[:15]],
            }
            data = _ask(STORIES_SYSTEM, json.dumps(payload), max_tokens=3500)
            out = []
            for s in data if isinstance(data, list) else []:
                if s.get("title"):
                    ac = s.get("acceptance_criteria", [])
                    out.append({
                        "activity": str(s.get("activity", ""))[:60],
                        "title": str(s["title"])[:160],
                        "description": str(s.get("description", ""))[:400],
                        "acceptance_criteria": [str(a) for a in ac] if isinstance(ac, list) else [],
                    })
            if out:
                return out, "claude"
        except Exception as exc:
            print(f"[groundwork] Claude stories failed, using offline mode: {exc}")
    stories = []
    for a in initiative.get("acceptance_criteria", []):
        stories.append({"activity": "Core flow", "title": a[:120], "description": "", "acceptance_criteria": [a]})
    stories.append({
        "activity": "Edge cases", "title": "Clear messages when something goes wrong",
        "description": "Cover empty, error and loading states.", "acceptance_criteria": ["Errors say what happened and what to do next"],
    })
    stories.append({
        "activity": "Measure", "title": "See whether the success metric moved",
        "description": initiative.get("success_metric", ""), "acceptance_criteria": ["Usage events are tracked for the new flow"],
    })
    return stories, "offline"


# ---------------------------------------------------------------- Ask Velo

VELO_SYSTEM = (
    "You are Velo, the product discovery copilot inside Groundwork. You answer questions about the user's "
    "workspace using ONLY the workspace snapshot below. Be concise and direct. When you rely on evidence, "
    "cite it like #12. Point out gaps (missing evidence, weak sources, unmeasured launches) when relevant "
    "and suggest the single next best action. If the snapshot does not contain the answer, say what to add. "
    "Do not use em dashes.\n\nWORKSPACE SNAPSHOT\n"
)


def ask_velo(messages: list[dict], digest: str) -> str:
    msgs = [
        {"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content", ""))[:4000]}
        for m in messages[-10:]
        if str(m.get("content", "")).strip()
    ]
    while msgs and msgs[0]["role"] == "assistant":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("Ask a question first")
    reply = _client().messages.create(model=MODEL, max_tokens=900, system=VELO_SYSTEM + digest, messages=msgs)
    return "".join(b.text for b in reply.content if b.type == "text").strip()
