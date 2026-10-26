"""AI usage metering. One credit is one AI action; heavier actions cost more. Counted per workspace per calendar month (UTC).

GROUNDWORK_AI_CREDITS sets the monthly allowance for every workspace (unset or 0 = unlimited). When it is spent, AI features
fall back to offline mode instead of failing, and nothing more is sent to Anthropic.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from . import core, db

CREDIT_COST = {"prototype": 3, "prd": 2, "launch": 2}      # everything else costs 1


def cost(kind: str) -> int:
    return CREDIT_COST.get(kind, 1)


def limit() -> int:
    try:
        return max(0, int(os.environ.get("GROUNDWORK_AI_CREDITS", "0") or 0))
    except ValueError:
        return 0


def _month_bounds() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=start.year + (start.month == 12), month=1 if start.month == 12 else start.month + 1)
    return start.isoformat(timespec="milliseconds"), nxt.isoformat(timespec="milliseconds")


def used() -> int:
    start, _ = _month_bounds()
    return core.one("SELECT COALESCE(SUM(credits), 0) AS n FROM ai_usage WHERE created_at >= ?", (start,))["n"]


def has_credit() -> bool:
    """Is any allowance left? True when there is no workspace in scope (scripts) or no limit is set."""
    if db.current_schema() is None or limit() == 0:
        return True
    return used() < limit()


def allowed(kind: str) -> bool:
    if db.current_schema() is None or limit() == 0:
        return True
    return used() + cost(kind) <= limit()


def record(kind: str, model: str, input_tokens: int, output_tokens: int) -> None:
    if db.current_schema() is None:
        return
    with core.conn() as c:
        c.execute("INSERT INTO ai_usage(kind, model, input_tokens, output_tokens, credits, created_at) VALUES (?,?,?,?,?,?)",
                  (kind, model, int(input_tokens or 0), int(output_tokens or 0), cost(kind), core.now()))


def summary() -> dict:
    start, nxt = _month_bounds()
    rows = core.rows("SELECT kind, SUM(credits) AS credits, SUM(input_tokens) AS i, SUM(output_tokens) AS o FROM ai_usage WHERE created_at >= ? GROUP BY kind", (start,))
    total, lim = sum(r["credits"] for r in rows), limit()
    return {"limit": lim, "used": total, "remaining": max(0, lim - total) if lim else None, "resets_at": nxt,
            "by_kind": {r["kind"]: r["credits"] for r in rows}, "input_tokens": sum(r["i"] for r in rows), "output_tokens": sum(r["o"] for r in rows)}
