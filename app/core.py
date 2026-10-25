"""Groundwork core: workspace data access, insights, and the context brief builder.

Shared by the web API and the MCP server so both see the same data. Every function runs against the
workspace schema selected with use_schema().
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from . import db
from .db import conn, one, rows, use_schema  # noqa: F401  (re-exported for the other modules)

SCHEMA = """
CREATE TABLE IF NOT EXISTS product (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '',
    overview TEXT NOT NULL DEFAULT '',
    canvas TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS themes (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium',
    theme_id BIGINT REFERENCES themes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'note',
    text TEXT NOT NULL,
    problem_id BIGINT REFERENCES problems(id) ON DELETE SET NULL,
    external_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS initiatives (
    id BIGSERIAL PRIMARY KEY,
    problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    theme TEXT NOT NULL DEFAULT '',
    theme_id BIGINT REFERENCES themes(id) ON DELETE SET NULL,
    persona TEXT NOT NULL DEFAULT '',
    problem_statement TEXT NOT NULL DEFAULT '',
    success_metric TEXT NOT NULL DEFAULT '',
    out_of_scope TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stories (
    id BIGSERIAL PRIMARY KEY,
    initiative_id BIGINT NOT NULL REFERENCES initiatives(id) ON DELETE CASCADE,
    activity TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatches (
    id BIGSERIAL PRIMARY KEY,
    initiative_id BIGINT NOT NULL REFERENCES initiatives(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    id BIGSERIAL PRIMARY KEY,
    initiative_id BIGINT NOT NULL REFERENCES initiatives(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    metric_value TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    provider TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'connected',
    config TEXT NOT NULL DEFAULT '{}',
    secret TEXT NOT NULL DEFAULT '',
    webhook_token TEXT,
    connected_by BIGINT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_links (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    kind TEXT NOT NULL,
    local_id BIGINT NOT NULL,
    external_id TEXT NOT NULL,
    external_key TEXT NOT NULL DEFAULT '',
    external_url TEXT NOT NULL DEFAULT '',
    external_status TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL,
    UNIQUE (provider, kind, local_id)
);
CREATE TABLE IF NOT EXISTS sync_log (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    direction TEXT NOT NULL,
    ok BOOLEAN NOT NULL DEFAULT TRUE,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_ready: set[str] = set()


def init_workspace(schema: str) -> None:
    """Create the schema and tables for one workspace (idempotent), once per process."""
    if schema in _ready:
        return
    with db.conn(db.SHARED) as c:  # CREATE SCHEMA is not tied to any workspace
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    with db.conn(schema) as c:
        c.execute(SCHEMA)
        db.lock_down(c, schema)
    _ready.add(schema)


def drop_workspace(schema: str) -> None:
    with db.conn(db.SHARED) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    _ready.discard(schema)


STATUSES = ["proposed", "approved", "in_progress", "shipped", "measured"]
VERDICTS = ["worked", "partial", "did_not_work", "too_early"]
CANVAS_KEYS = [
    "customer_segments", "problem", "unique_value", "solution", "channels",
    "key_metrics", "revenue", "costs", "unfair_advantage",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(v, default):
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def reset_workspace() -> None:
    with conn() as c:
        c.execute("TRUNCATE outcomes, dispatches, stories, initiatives, evidence, problems, themes, product, sync_links RESTART IDENTITY CASCADE")


# ---------- product ----------

def get_product() -> dict:
    p = one("SELECT * FROM product WHERE id=1")
    if not p:
        return {"name": "", "overview": "", "canvas": {k: "" for k in CANVAS_KEYS}, "updated_at": None}
    canvas = _loads(p["canvas"], {})
    p["canvas"] = {k: str(canvas.get(k, "")) for k in CANVAS_KEYS}
    return p


def save_product(name: str | None = None, overview: str | None = None, canvas: dict | None = None) -> dict:
    cur = get_product()
    name = cur["name"] if name is None else name.strip()
    overview = cur["overview"] if overview is None else overview.strip()
    merged = dict(cur["canvas"])
    if canvas:
        merged.update({k: str(v) for k, v in canvas.items() if k in CANVAS_KEYS})
    with conn() as c:
        c.execute(
            """INSERT INTO product(id, name, overview, canvas, updated_at) VALUES (1,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, overview=excluded.overview,
               canvas=excluded.canvas, updated_at=excluded.updated_at""",
            (name, overview, json.dumps(merged), now()),
        )
    return get_product()


# ---------- evidence ----------

def add_evidence(text: str, source: str = "note") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Evidence text is empty")
    with conn() as c:
        cur = c.execute(
            "INSERT INTO evidence(source, text, created_at) VALUES (?,?,?) RETURNING id",
            (source.strip() or "note", text, now()),
        )
        eid = cur.fetchone()["id"]
    return one("SELECT * FROM evidence WHERE id=?", (eid,))


def delete_evidence(eid: int) -> bool:
    with conn() as c:
        return c.execute("DELETE FROM evidence WHERE id=?", (eid,)).rowcount > 0


def list_evidence(unassigned_only: bool = False) -> list[dict]:
    sql = "SELECT e.*, p.title AS problem_title FROM evidence e LEFT JOIN problems p ON p.id=e.problem_id"
    if unassigned_only:
        sql += " WHERE e.problem_id IS NULL"
    return rows(sql + " ORDER BY e.id DESC")


# ---------- problems ----------

def reset_ungrounded() -> None:
    """Drop problems that no initiative depends on, so evidence can be regrouped."""
    with conn() as c:
        c.execute(
            "UPDATE evidence SET problem_id=NULL WHERE problem_id IN "
            "(SELECT id FROM problems WHERE id NOT IN (SELECT problem_id FROM initiatives))"
        )
        c.execute("DELETE FROM problems WHERE id NOT IN (SELECT problem_id FROM initiatives)")


def save_problems(clusters: list[dict]) -> list[int]:
    """Persist clusters: [{title, summary, persona, severity, evidence_ids}]."""
    ids: list[int] = []
    with conn() as c:
        for cl in clusters:
            cur = c.execute(
                "INSERT INTO problems(title, summary, persona, severity, created_at) VALUES (?,?,?,?,?) RETURNING id",
                (cl["title"], cl.get("summary", ""), cl.get("persona", ""), cl.get("severity", "medium"), now()),
            )
            pid = cur.fetchone()["id"]
            ids.append(pid)
            for eid in cl.get("evidence_ids", []):
                c.execute("UPDATE evidence SET problem_id=? WHERE id=?", (pid, int(eid)))
    return ids


def update_problem(pid: int, data: dict) -> dict | None:
    allowed = {k: v for k, v in data.items() if k in ("title", "summary", "persona", "severity")}
    if "severity" in allowed and allowed["severity"] not in ("low", "medium", "high"):
        raise ValueError("severity must be low, medium or high")
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        with conn() as c:
            c.execute(f"UPDATE problems SET {sets} WHERE id=?", (*allowed.values(), pid))
    return get_problem(pid)


def set_problem_theme(pid: int, theme_id: int | None) -> dict | None:
    with conn() as c:
        c.execute("UPDATE problems SET theme_id=? WHERE id=?", (theme_id, pid))
        c.execute("UPDATE initiatives SET theme_id=? WHERE problem_id=?", (theme_id, pid))
    return get_problem(pid)


def list_problems() -> list[dict]:
    probs = rows(
        """SELECT p.*, t.title AS theme_title, COUNT(e.id) AS evidence_count,
                  (SELECT COUNT(*) FROM initiatives i WHERE i.problem_id=p.id) AS initiative_count
           FROM problems p
           LEFT JOIN evidence e ON e.problem_id=p.id
           LEFT JOIN themes t ON t.id=p.theme_id
           GROUP BY p.id, t.title ORDER BY evidence_count DESC, p.id"""
    )
    mix: dict[int, dict[str, int]] = {}
    for r in rows(
        "SELECT problem_id, source, COUNT(*) AS n FROM evidence WHERE problem_id IS NOT NULL GROUP BY problem_id, source"
    ):
        mix.setdefault(r["problem_id"], {})[r["source"]] = r["n"]
    for p in probs:
        p["sources"] = mix.get(p["id"], {})
    return probs


def get_problem(pid: int) -> dict | None:
    p = one(
        "SELECT p.*, t.title AS theme_title FROM problems p LEFT JOIN themes t ON t.id=p.theme_id WHERE p.id=?",
        (pid,),
    )
    if not p:
        return None
    p["evidence"] = rows("SELECT * FROM evidence WHERE problem_id=? ORDER BY id", (pid,))
    return p


# ---------- themes ----------

def list_themes() -> list[dict]:
    return rows(
        """SELECT t.*,
              (SELECT COUNT(*) FROM problems p WHERE p.theme_id=t.id) AS problem_count,
              (SELECT COUNT(*) FROM initiatives i WHERE i.theme_id=t.id) AS initiative_count,
              (SELECT COUNT(*) FROM evidence e JOIN problems p ON p.id=e.problem_id WHERE p.theme_id=t.id) AS evidence_count
           FROM themes t ORDER BY t.id"""
    )


def create_theme(title: str, description: str = "") -> dict:
    title = title.strip()
    if not title:
        raise ValueError("Theme title is empty")
    with conn() as c:
        cur = c.execute("INSERT INTO themes(title, description, created_at) VALUES (?,?,?) RETURNING id", (title, description.strip(), now()))
        tid = cur.fetchone()["id"]
    return one("SELECT * FROM themes WHERE id=?", (tid,))


def update_theme(tid: int, data: dict) -> dict | None:
    allowed = {k: str(v).strip() for k, v in data.items() if k in ("title", "description")}
    if "title" in allowed and not allowed["title"]:
        raise ValueError("Theme title is empty")
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        with conn() as c:
            c.execute(f"UPDATE themes SET {sets} WHERE id=?", (*allowed.values(), tid))
    return one("SELECT * FROM themes WHERE id=?", (tid,))


def delete_theme(tid: int) -> bool:
    with conn() as c:
        return c.execute("DELETE FROM themes WHERE id=?", (tid,)).rowcount > 0


def save_themes(themes: list[dict]) -> list[int]:
    """Create themes and attach problems: [{title, description, problem_ids}]."""
    ids: list[int] = []
    for t in themes:
        row = create_theme(t["title"], t.get("description", ""))
        ids.append(row["id"])
        for pid in t.get("problem_ids", []):
            set_problem_theme(int(pid), row["id"])
    return ids


# ---------- initiatives ----------

def _decode(i: dict) -> dict:
    i["out_of_scope"] = _loads(i.get("out_of_scope"), [])
    i["acceptance_criteria"] = _loads(i.get("acceptance_criteria"), [])
    return i


def create_initiative(problem_id: int, spec: dict) -> dict:
    prob = one("SELECT theme_id FROM problems WHERE id=?", (problem_id,)) or {}
    theme_id = prob.get("theme_id")
    theme_text = spec.get("theme", "")
    if theme_id:
        t = one("SELECT title FROM themes WHERE id=?", (theme_id,))
        theme_text = t["title"] if t else theme_text
    with conn() as c:
        cur = c.execute(
            """INSERT INTO initiatives(problem_id, title, theme, theme_id, persona, problem_statement,
               success_metric, out_of_scope, acceptance_criteria, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?, 'proposed', ?) RETURNING id""",
            (
                problem_id,
                spec["title"],
                theme_text,
                theme_id,
                spec.get("persona", ""),
                spec.get("problem_statement", ""),
                spec.get("success_metric", ""),
                json.dumps(spec.get("out_of_scope", [])),
                json.dumps(spec.get("acceptance_criteria", [])),
                now(),
            ),
        )
        iid = cur.fetchone()["id"]
    return get_initiative(iid)


def update_initiative(iid: int, data: dict) -> dict | None:
    scalar = ("title", "persona", "problem_statement", "success_metric")
    sets, args = [], []
    for k in scalar:
        if k in data and data[k] is not None:
            sets.append(f"{k}=?")
            args.append(str(data[k]).strip())
    for k in ("out_of_scope", "acceptance_criteria"):
        if k in data and data[k] is not None:
            sets.append(f"{k}=?")
            args.append(json.dumps([str(x).strip() for x in data[k] if str(x).strip()]))
    if "theme_id" in data:
        sets.append("theme_id=?")
        args.append(data["theme_id"])
    if "status" in data and data["status"] is not None:
        if data["status"] not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        sets.append("status=?")
        args.append(data["status"])
    if sets:
        with conn() as c:
            c.execute(f"UPDATE initiatives SET {', '.join(sets)} WHERE id=?", (*args, iid))
    return get_initiative(iid)


def list_initiatives() -> list[dict]:
    items = rows(
        """SELECT i.*, p.title AS problem_title, t.title AS theme_title,
                  (SELECT COUNT(*) FROM evidence e WHERE e.problem_id=i.problem_id) AS evidence_count,
                  (SELECT COUNT(*) FROM stories s WHERE s.initiative_id=i.id) AS story_count,
                  (SELECT verdict FROM outcomes o WHERE o.initiative_id=i.id ORDER BY o.id DESC LIMIT 1) AS latest_verdict
           FROM initiatives i
           JOIN problems p ON p.id=i.problem_id
           LEFT JOIN themes t ON t.id=i.theme_id
           ORDER BY i.id DESC"""
    )
    return [_decode(i) for i in items]


def get_initiative(iid: int) -> dict | None:
    i = one(
        """SELECT i.*, p.title AS problem_title, p.summary AS problem_summary, t.title AS theme_title
           FROM initiatives i JOIN problems p ON p.id=i.problem_id
           LEFT JOIN themes t ON t.id=i.theme_id WHERE i.id=?""",
        (iid,),
    )
    if not i:
        return None
    i = _decode(i)
    i["evidence"] = rows("SELECT * FROM evidence WHERE problem_id=? ORDER BY id", (i["problem_id"],))
    i["stories"] = list_stories(iid)
    i["dispatches"] = rows("SELECT * FROM dispatches WHERE initiative_id=? ORDER BY id DESC", (iid,))
    i["outcomes"] = rows("SELECT * FROM outcomes WHERE initiative_id=? ORDER BY id DESC", (iid,))
    return i


def set_status(iid: int, status: str) -> dict | None:
    return update_initiative(iid, {"status": status})


def record_dispatch(iid: int, target: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO dispatches(initiative_id, target, created_at) VALUES (?,?,?)", (iid, target, now()))
        c.execute("UPDATE initiatives SET status='in_progress' WHERE id=? AND status IN ('proposed','approved')", (iid,))


def record_outcome(iid: int, verdict: str, metric_value: str = "", note: str = "") -> dict | None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    with conn() as c:
        c.execute(
            "INSERT INTO outcomes(initiative_id, verdict, metric_value, note, created_at) VALUES (?,?,?,?,?)",
            (iid, verdict, metric_value, note, now()),
        )
        c.execute("UPDATE initiatives SET status='measured' WHERE id=?", (iid,))
    return get_initiative(iid)


# ---------- stories (story map) ----------

def _story(s: dict) -> dict:
    s["acceptance"] = _loads(s.get("acceptance"), [])
    return s


def list_stories(iid: int) -> list[dict]:
    return [_story(s) for s in rows("SELECT * FROM stories WHERE initiative_id=? ORDER BY position, id", (iid,))]


def save_stories(iid: int, stories: list[dict], replace: bool = True) -> list[dict]:
    with conn() as c:
        if replace:
            c.execute("DELETE FROM stories WHERE initiative_id=?", (iid,))
        start = c.execute("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM stories WHERE initiative_id=?", (iid,)).fetchone()["n"]
        for n, s in enumerate(stories):
            c.execute(
                "INSERT INTO stories(initiative_id, activity, title, description, acceptance, position, created_at) VALUES (?,?,?,?,?,?,?)",
                (iid, s.get("activity", ""), s["title"], s.get("description", ""),
                 json.dumps(s.get("acceptance_criteria", s.get("acceptance", []))), start + n, now()),
            )
    return list_stories(iid)


def update_story(sid: int, data: dict) -> dict | None:
    sets, args = [], []
    for k in ("activity", "title", "description"):
        if k in data and data[k] is not None:
            sets.append(f"{k}=?")
            args.append(str(data[k]).strip())
    if data.get("acceptance") is not None:
        sets.append("acceptance=?")
        args.append(json.dumps([str(x).strip() for x in data["acceptance"] if str(x).strip()]))
    if sets:
        with conn() as c:
            c.execute(f"UPDATE stories SET {', '.join(sets)} WHERE id=?", (*args, sid))
    s = one("SELECT * FROM stories WHERE id=?", (sid,))
    return _story(s) if s else None


def delete_story(sid: int) -> bool:
    with conn() as c:
        return c.execute("DELETE FROM stories WHERE id=?", (sid,)).rowcount > 0


# ---------- stats, activity, insights ----------

def get_stats() -> dict:
    def n(sql: str) -> int:
        return (one(sql) or {"n": 0})["n"]

    return {
        "evidence": n("SELECT COUNT(*) AS n FROM evidence"),
        "grouped": n("SELECT COUNT(*) AS n FROM evidence WHERE problem_id IS NOT NULL"),
        "problems": n("SELECT COUNT(*) AS n FROM problems"),
        "themes": n("SELECT COUNT(*) AS n FROM themes"),
        "initiatives": n("SELECT COUNT(*) AS n FROM initiatives"),
        "stories": n("SELECT COUNT(*) AS n FROM stories"),
        "dispatched": n("SELECT COUNT(DISTINCT initiative_id) AS n FROM dispatches"),
        "shipped": n("SELECT COUNT(*) AS n FROM initiatives WHERE status IN ('shipped','measured')"),
        "measured": n("SELECT COUNT(DISTINCT initiative_id) AS n FROM outcomes"),
        "worked": n("SELECT COUNT(DISTINCT initiative_id) AS n FROM outcomes WHERE verdict='worked'"),
    }


def recent_activity(limit: int = 8) -> list[dict]:
    return rows(
        """SELECT kind, ref_id, label, created_at FROM (
               SELECT 'initiative' AS kind, id AS ref_id, title AS label, created_at FROM initiatives
               UNION ALL
               SELECT 'dispatch', i.id, i.title || ' sent to ' || d.target, d.created_at
                 FROM dispatches d JOIN initiatives i ON i.id=d.initiative_id
               UNION ALL
               SELECT 'outcome', i.id, i.title || ': ' || REPLACE(o.verdict, '_', ' '), o.created_at
                 FROM outcomes o JOIN initiatives i ON i.id=o.initiative_id
           ) AS a ORDER BY created_at DESC, ref_id DESC LIMIT ?""",
        (limit,),
    )


def compute_insights() -> list[dict]:
    """Rule-based gaps and next actions. Each: level, title, detail, label, view, target."""
    s = get_stats()
    product = get_product()
    probs = list_problems()
    themes = list_themes()
    inits = list_initiatives()
    out: list[dict] = []

    def add(level, title, detail, label, view, target=None):
        out.append({"level": level, "title": title, "detail": detail, "label": label, "view": view, "target": target or {}})

    if not product["overview"]:
        add("attention", "Describe your product",
            "Specs get sharper when Velo knows what you are building and who it is for. Add a short overview on the canvas.",
            "Open canvas", "canvas")
    if s["evidence"] == 0:
        add("attention", "Add customer evidence",
            "Paste interviews, tickets or reviews. Everything else is built from them.", "Add evidence", "evidence")
        return out

    ungrouped = s["evidence"] - s["grouped"]
    if ungrouped:
        add("attention", f"{ungrouped} evidence {'item is' if ungrouped == 1 else 'items are'} not in a problem yet",
            "Run an analysis to group them into customer problems.", "Analyze evidence", "evidence", {"action": "analyze"})

    strong = [p for p in probs if p["evidence_count"] >= 3 and p["initiative_count"] == 0]
    if strong:
        top = strong[0]
        add("attention", f"{len(strong)} well-evidenced {'problem has' if len(strong) == 1 else 'problems have'} no initiative",
            f"\"{top['title']}\" has {top['evidence_count']} pieces of evidence and nobody is working on it.",
            "Open problem", "problems", {"problem_id": top["id"]})

    if probs and not themes:
        add("info", "Group problems under strategy themes",
            "Themes tell you which problems matter to where the product is going.", "Open strategy", "strategy")
    elif themes:
        unthemed = [p for p in probs if not p["theme_id"]]
        if unthemed:
            add("info", f"{len(unthemed)} {'problem has' if len(unthemed) == 1 else 'problems have'} no theme",
                f"\"{unthemed[0]['title']}\" is not tied to any strategy theme.", "Open strategy", "strategy")
        empty = [t for t in themes if t["initiative_count"] == 0 and t["problem_count"] > 0]
        if empty:
            add("info", f"Theme \"{empty[0]['title']}\" has no initiatives",
                "You have evidence under this theme but nothing planned.", "Open strategy", "strategy")

    nostories = [i for i in inits if i["story_count"] == 0 and i["status"] in ("proposed", "approved")]
    if nostories:
        add("info", f"{len(nostories)} {'initiative has' if len(nostories) == 1 else 'initiatives have'} no stories",
            f"Break \"{nostories[0]['title']}\" into a story map so agents can build it in steps.",
            "Open initiative", "initiative", {"initiative_id": nostories[0]["id"], "tab": "stories"})

    unmeasured = [i for i in inits if i["status"] == "shipped" and not i["latest_verdict"]]
    if unmeasured:
        add("attention", f"\"{unmeasured[0]['title']}\" shipped but was never measured",
            f"Success metric: {unmeasured[0]['success_metric'] or 'not set'}. Record whether it moved.",
            "Record outcome", "initiative", {"initiative_id": unmeasured[0]["id"], "tab": "outcome"})

    for p in probs:
        if p["evidence_count"] >= 3 and len(p["sources"]) == 1:
            src = next(iter(p["sources"]))
            add("info", f"\"{p['title']}\" rests on one source",
                f"All {p['evidence_count']} items are from {src}. Check it with a second source before building.",
                "Open problem", "problems", {"problem_id": p["id"]})
            break

    if s["grouped"] >= 8 and probs:
        top = probs[0]
        share = top["evidence_count"] / s["grouped"]
        if share >= 0.4:
            add("info", f"{round(share * 100)}% of your evidence is about one problem",
                f"\"{top['title']}\" dominates. Make sure you are not ignoring quieter problems.",
                "Open problem", "problems", {"problem_id": top["id"]})

    worked = [i for i in inits if i["latest_verdict"] == "worked"]
    if worked:
        add("good", f"\"{worked[0]['title']}\" worked", "The loop closed: evidence, build, and a measured result.",
            "See trace", "initiative", {"initiative_id": worked[0]["id"], "tab": "trace"})

    order = {"attention": 0, "info": 1, "good": 2}
    out.sort(key=lambda x: order[x["level"]])
    return out[:7]


def workspace_digest(max_chars: int = 9000) -> str:
    """Compact text snapshot of the workspace, used to ground the Ask Velo assistant."""
    product = get_product()
    stats = get_stats()
    lines = [
        f"Product: {product['name'] or 'unnamed'}",
        f"Overview: {product['overview'] or 'not provided'}",
    ]
    for k, v in product["canvas"].items():
        if v.strip():
            lines.append(f"Canvas {k}: {v.strip()[:200]}")
    lines.append(
        f"Counts: {stats['evidence']} evidence, {stats['problems']} problems, {stats['themes']} themes, "
        f"{stats['initiatives']} initiatives, {stats['shipped']} shipped, {stats['measured']} measured."
    )
    lines.append("\nProblems (id, severity, evidence count, sources, theme):")
    for p in list_problems():
        srcs = ", ".join(f"{k} {v}" for k, v in p["sources"].items())
        lines.append(f"- P{p['id']} {p['title']} [{p['severity']}, {p['evidence_count']} items, {srcs}, theme: {p['theme_title'] or 'none'}] {p['summary'][:160]}")
        for e in rows("SELECT id, source, text FROM evidence WHERE problem_id=? ORDER BY id LIMIT 2", (p["id"],)):
            lines.append(f"    evidence #{e['id']} ({e['source']}): {e['text'][:170]}")
    lines.append("\nThemes:")
    for t in list_themes():
        lines.append(f"- {t['title']}: {t['problem_count']} problems, {t['initiative_count']} initiatives. {t['description'][:140]}")
    lines.append("\nInitiatives:")
    for i in list_initiatives():
        lines.append(
            f"- I{i['id']} {i['title']} [{i['status']}] for {i['persona'] or 'n/a'}; metric: {i['success_metric'] or 'none'}; "
            f"stories: {i['story_count']}; outcome: {i['latest_verdict'] or 'not measured'}"
        )
    return "\n".join(lines)[:max_chars]


# ---------- context brief + export ----------

def build_context(iid: int, max_quotes: int = 8) -> str | None:
    """Markdown brief a coding agent can follow: the problem, the proof, the target, the boundaries."""
    i = get_initiative(iid)
    if not i:
        return None
    ev = i["evidence"]
    product = get_product()
    lines = [f"# Initiative {i['id']}: {i['title']}", ""]
    if product["name"] or product["overview"]:
        lines += [f"**Product:** {product['name'] or 'unnamed'}" + (f". {product['overview']}" if product["overview"] else ""), ""]
    lines += [
        f"**Status:** {i['status']}" + (f"  |  **Theme:** {i['theme_title']}" if i["theme_title"] else ""),
        "",
        "## Problem",
        i["problem_statement"] or i["problem_title"],
        "",
        "## Who it is for",
        i["persona"] or "Not specified",
        "",
        f"## Evidence ({len(ev)} items)",
    ]
    for e in ev[:max_quotes]:
        snippet = e["text"].replace("\n", " ")
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."
        lines.append(f"- [{e['source']} #{e['id']}] {snippet}")
    if len(ev) > max_quotes:
        lines.append(f"- ...and {len(ev) - max_quotes} more (call get_evidence for the full set)")
    lines += ["", "## Success looks like", i["success_metric"] or "Not specified", "", "## Acceptance criteria"]
    lines += [f"- [ ] {a}" for a in i["acceptance_criteria"]] or ["- Not specified"]
    if i["stories"]:
        lines += ["", "## Story map (build in this order)"]
        activity = None
        for s in i["stories"]:
            if s["activity"] != activity:
                activity = s["activity"]
                lines.append(f"### {activity or 'General'}")
            lines.append(f"- **{s['title']}**" + (f": {s['description']}" if s["description"] else ""))
            lines += [f"  - [ ] {a}" for a in s["acceptance"]]
    lines += ["", "## Out of scope"]
    lines += [f"- {o}" for o in i["out_of_scope"]] or ["- Nothing declared"]
    lines += [
        "",
        "## Reporting back",
        f"When shipped, call `report_outcome` with initiative_id={i['id']} so the team can see whether the metric moved.",
    ]
    return "\n".join(lines)


def build_tickets_csv(iid: int) -> str | None:
    """Generic CSV (epic plus stories) that Jira, Linear and GitHub importers can map."""
    i = get_initiative(iid)
    if not i:
        return None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Issue Type", "Summary", "Description", "Epic Name", "Labels"])
    label = f"groundwork initiative-{i['id']}"
    w.writerow([
        "Epic", i["title"],
        f"{i['problem_statement']}\n\nSuccess metric: {i['success_metric']}\nBacked by {len(i['evidence'])} pieces of customer evidence.",
        i["title"], label,
    ])
    for s in i["stories"]:
        ac = "\n".join(f"- {a}" for a in s["acceptance"])
        desc = (s["description"] + ("\n\nAcceptance criteria:\n" + ac if ac else "")).strip()
        w.writerow(["Story", s["title"], desc, i["title"], label + (" " + s["activity"].lower().replace(" ", "-") if s["activity"] else "")])
    return buf.getvalue()
