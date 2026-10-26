"""Groundwork MCP server: gives coding agents (Claude Code, Cursor, any MCP client) the same workspace the team sees.

The agent acts as one member of one workspace, so it has exactly that member's role: viewers can read, members can
change things. Get the token from the Hand off tab of any initiative.

Run over stdio (default):   DATABASE_URL=... GROUNDWORK_TOKEN=... python mcp_server.py
Run over HTTP:              ... python mcp_server.py --http   (serves on http://127.0.0.1:8765/mcp)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def _load_env_file() -> None:
    """Read DATABASE_URL and friends from the project's .env so they are not pasted into the agent's config."""
    f = Path(__file__).resolve().parent / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file()

from app import core, db, teams  # noqa: E402  (after .env is loaded: app.db reads DATABASE_URL at import)
from app.integrations import service as integrations  # noqa: E402
from app.integrations.base import IntegrationError  # noqa: E402

_token = os.environ.get("GROUNDWORK_TOKEN", "")
if not _token:
    sys.exit("Set GROUNDWORK_TOKEN (see the Hand off tab in Groundwork) so the agent knows which workspace to use.")
if not os.environ.get("DATABASE_URL"):
    sys.exit("Set DATABASE_URL to the same Postgres database Groundwork uses.")
_member = teams.member_by_token(_token)
if not _member:
    sys.exit("GROUNDWORK_TOKEN does not match any workspace member. Copy a fresh one from the Hand off tab.")

mcp = FastMCP("groundwork", host="127.0.0.1", port=8765)


def _ws() -> str:
    """Point this call at the token's workspace. Done per call so it holds in whichever thread runs the tool."""
    schema = db.schema_name(_member["workspace_id"])
    core.init_workspace(schema)
    db.use_schema(schema)
    return schema


def _can_write() -> bool:
    return teams.rank(_member["role"]) >= teams.rank("member")


_READ_ONLY = "Your token is for a view-only member, so this is not allowed."


@mcp.tool()
def list_initiatives(status: str = "") -> str:
    """List initiatives with their id, title, status and evidence count. Optionally filter by status
    (proposed, approved, in_progress, shipped, measured)."""
    _ws()
    items = core.list_initiatives()
    if status:
        items = [i for i in items if i["status"] == status]
    if not items:
        return "No initiatives found."
    return "\n".join(f"#{i['id']} [{i['status']}] {i['title']} ({i['evidence_count']} evidence items)" for i in items)


@mcp.tool()
def list_problems() -> str:
    """List customer problems with severity, how much evidence backs each, and whether anyone is working on it."""
    _ws()
    probs = core.list_problems()
    if not probs:
        return "No problems yet. Evidence has to be analyzed first."
    return "\n".join(
        f"P{p['id']} [{p['severity']}] {p['title']}: {p['evidence_count']} evidence, {p['initiative_count']} initiatives"
        + (f", theme: {p['theme_title']}" if p["theme_title"] else "")
        for p in probs
    )


@mcp.tool()
def get_initiative_context(initiative_id: int) -> str:
    """Get the full build brief for an initiative: the customer problem, who it is for, supporting evidence,
    success metric, acceptance criteria, story map and what is out of scope. Read this BEFORE writing code."""
    _ws()
    md = core.build_context(initiative_id)
    if md is None:
        return f"Initiative {initiative_id} not found."
    if _can_write():
        core.record_dispatch(initiative_id, "mcp")
    return md


@mcp.tool()
def whats_changed(initiative_id: int) -> str:
    """Check whether an initiative's evidence or spec changed since its brief was last sent. Call this before continuing
    long-running work, and call get_initiative_context again if it says the brief is out of date."""
    _ws()
    i = core.get_initiative(initiative_id)
    if not i:
        return f"Initiative {initiative_id} not found."
    c = i["context"]
    if not c["sent_at"]:
        return "No brief has been sent for this initiative yet. Call get_initiative_context to get it."
    if c["stale"]:
        return f"Out of date: {' and '.join(c['reasons'])} since the brief was last sent ({c['sent_at']}). Call get_initiative_context again."
    return f"Up to date as of {c['sent_at']}."


@mcp.tool()
def get_product_context() -> str:
    """Get what the product is: name, overview, product canvas and the strategy themes. Read this once at the
    start of a session so your work fits the product's direction."""
    _ws()
    p = core.get_product()
    lines = [f"# {p['name'] or 'Product'}", "", p["overview"] or "No overview yet.", ""]
    for key, value in p["canvas"].items():
        if value.strip():
            lines += [f"## {key.replace('_', ' ').title()}", value.strip(), ""]
    themes = core.list_themes()
    if themes:
        lines.append("## Strategy themes")
        lines += [f"- {t['title']}: {t['description']}" for t in themes]
    return "\n".join(lines)


@mcp.tool()
def get_evidence(initiative_id: int) -> str:
    """Get every raw customer quote behind an initiative, to understand the real situation and edge cases."""
    _ws()
    i = core.get_initiative(initiative_id)
    if not i:
        return f"Initiative {initiative_id} not found."
    return "\n\n".join(f"[{e['source']} #{e['id']}] {e['text']}" for e in i["evidence"]) or "No evidence."


@mcp.tool()
def add_evidence(text: str, source: str = "note") -> str:
    """Add a piece of customer evidence (a quote, ticket or note). source is for example interview, support, review,
    survey or note. It is grouped into a problem the next time the team analyzes evidence."""
    _ws()
    if not _can_write():
        return _READ_ONLY
    try:
        e = core.add_evidence(text, source)
    except ValueError as err:
        return str(err)
    return f"Added evidence #{e['id']} ({e['source']})."


@mcp.tool()
def update_status(initiative_id: int, status: str) -> str:
    """Update an initiative's status: proposed, approved, in_progress, or shipped."""
    _ws()
    if not _can_write():
        return _READ_ONLY
    try:
        i = core.set_status(initiative_id, status)
    except ValueError as e:
        return str(e)
    return f"Initiative {initiative_id} is now {i['status']}." if i else f"Initiative {initiative_id} not found."


@mcp.tool()
def report_outcome(initiative_id: int, verdict: str, metric_value: str = "", note: str = "") -> str:
    """Report what happened after shipping. verdict is one of: worked, partial, did_not_work, too_early.
    metric_value is the measured number (for example '37% fewer tickets'). note is free text."""
    _ws()
    if not _can_write():
        return _READ_ONLY
    try:
        i = core.record_outcome(initiative_id, verdict, metric_value, note)
    except ValueError as e:
        return str(e)
    return f"Outcome '{verdict}' recorded for initiative {initiative_id}." if i else f"Initiative {initiative_id} not found."


@mcp.tool()
def list_integrations() -> str:
    """List the connected work trackers (for example Linear) that initiatives can be pushed to."""
    _ws()
    connected = [p for p in integrations.catalog_state() if p["connected"]]
    if not connected:
        return "No integrations are connected. An admin can connect one in Groundwork under Integrations."
    return "\n".join(f"{p['id']}: {p['name']} ({p['kind']})" for p in connected)


@mcp.tool()
def push_initiative(initiative_id: int, provider: str = "linear") -> str:
    """Create or update the issues for an initiative and its stories in a connected work tracker such as Linear."""
    _ws()
    if not _can_write():
        return _READ_ONLY
    try:
        r = integrations.push(provider, initiative_id)
    except IntegrationError as e:
        return str(e)
    except LookupError:
        return f"Initiative {initiative_id} not found."
    keys = ", ".join(l["external_key"] for l in r["links"] if l["kind"] == "initiative")
    return f"Sent to {provider}: {r['created']} created, {r['updated']} updated. Parent issue {keys}."


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
