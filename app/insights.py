"""The insights digest: a short list of specific findings about the workspace, on demand or weekly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import ai, core

LEVEL_KIND = {"attention": "risk", "info": "gap", "good": "opportunity"}
INTERVAL_DAYS = 7


def generate() -> dict:
    """Write a new digest and return it. Uses Claude when it is available, and the rule-based findings otherwise."""
    items, mode = ai.generate_digest(core.workspace_digest())
    if not items:
        items = [{"title": i["title"], "detail": i["detail"], "kind": LEVEL_KIND.get(i["level"], "gap"), "evidence_ids": []}
                 for i in core.compute_insights()][:5]
        mode = "offline"
    return core.save_insight_run(items, mode)


def due(days: int = INTERVAL_DAYS) -> bool:
    last = core.last_insight_at()
    return last is None or datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(days=days)
