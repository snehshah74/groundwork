"""Groundwork MCP server: hands initiative context to coding agents (Claude Code, Cursor, any MCP client).

Run over stdio (default):   GROUNDWORK_TOKEN=... python mcp_server.py
Run over HTTP:              python mcp_server.py --http   (serves on http://127.0.0.1:8765/mcp)
"""
from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from app import auth, core

# The agent acts as one Groundwork user. Copy GROUNDWORK_TOKEN from the Hand off tab of any initiative.
_token = os.environ.get("GROUNDWORK_TOKEN", "")
if _token:
    _user = auth.user_by_token(_token)
    if not _user:
        sys.exit("GROUNDWORK_TOKEN does not match any account.")
    core.use_db(auth.workspace_path(_user["id"]))
elif os.environ.get("GROUNDWORK_DB"):
    core.use_db(os.environ["GROUNDWORK_DB"])
else:
    sys.exit("Set GROUNDWORK_TOKEN (see the Hand off tab in Groundwork) so the agent knows which workspace to use.")

mcp = FastMCP("groundwork", host="127.0.0.1", port=8765)


@mcp.tool()
def list_initiatives(status: str = "") -> str:
    """List initiatives with their id, title, status and evidence count. Optionally filter by status
    (proposed, approved, in_progress, shipped, measured)."""
    items = core.list_initiatives()
    if status:
        items = [i for i in items if i["status"] == status]
    if not items:
        return "No initiatives found."
    return "\n".join(
        f"#{i['id']} [{i['status']}] {i['title']} ({i['evidence_count']} evidence items)" for i in items
    )


@mcp.tool()
def get_initiative_context(initiative_id: int) -> str:
    """Get the full build brief for an initiative: the customer problem, who it is for, supporting evidence,
    success metric, acceptance criteria and what is out of scope. Read this BEFORE writing code."""
    md = core.build_context(initiative_id)
    if md is None:
        return f"Initiative {initiative_id} not found."
    core.record_dispatch(initiative_id, "mcp")
    return md


@mcp.tool()
def get_product_context() -> str:
    """Get what the product is: name, overview, product canvas and the strategy themes. Read this once at the
    start of a session so your work fits the product's direction."""
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
    i = core.get_initiative(initiative_id)
    if not i:
        return f"Initiative {initiative_id} not found."
    return "\n\n".join(f"[{e['source']} #{e['id']}] {e['text']}" for e in i["evidence"]) or "No evidence."


@mcp.tool()
def update_status(initiative_id: int, status: str) -> str:
    """Update an initiative's status: proposed, approved, in_progress, or shipped."""
    try:
        i = core.set_status(initiative_id, status)
    except ValueError as e:
        return str(e)
    return f"Initiative {initiative_id} is now {i['status']}." if i else f"Initiative {initiative_id} not found."


@mcp.tool()
def report_outcome(initiative_id: int, verdict: str, metric_value: str = "", note: str = "") -> str:
    """Report what happened after shipping. verdict is one of: worked, partial, did_not_work, too_early.
    metric_value is the measured number (for example '37% fewer tickets'). note is free text."""
    try:
        i = core.record_outcome(initiative_id, verdict, metric_value, note)
    except ValueError as e:
        return str(e)
    return f"Outcome '{verdict}' recorded for initiative {initiative_id}." if i else f"Initiative {initiative_id} not found."


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
