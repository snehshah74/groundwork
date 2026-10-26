"""Automatic grouping: runs when evidence arrives, so problems stay current without anyone clicking Analyze."""
from __future__ import annotations

from . import ai, core, db

AUTO_MIN_UNGROUPED = 3   # fewer ungrouped items than this and there is nothing worth running for


def enabled() -> bool:
    return core.get_setting("auto_analyze", "1") == "1"


def auto_analyze(schema: str | None = None) -> dict:
    """Group ungrouped evidence into existing problems, or into new ones when enough has piled up.

    Safe to call after every change: it does nothing when switched off, when there is too little to do, or when another run
    is already in progress for this workspace. `schema` lets a background task say which workspace it is for.
    """
    if schema:
        db.use_schema(schema)
    if not enabled():
        return {"skipped": "off"}
    items = core.list_evidence(unassigned_only=True)
    if len(items) < AUTO_MIN_UNGROUPED and not (items and core.list_problems()):
        return {"skipped": "too few"}
    if not core.try_lock("analysis"):
        return {"skipped": "busy"}
    try:
        problems = core.list_problems()
        items = core.list_evidence(unassigned_only=True)       # read again under the lock: another run may just have finished
        if not items:
            return {"skipped": "nothing to do"}
        texts: dict[int, str] = {}
        for r in core.rows("SELECT problem_id, text FROM evidence WHERE problem_id IS NOT NULL ORDER BY id"):
            if len(texts.get(r["problem_id"], "")) < 1200:
                texts[r["problem_id"]] = texts.get(r["problem_id"], "") + " " + r["text"][:300]
        if problems:
            assigned, clusters, mode = ai.assign_evidence(items, problems, texts)
        elif len(items) >= ai.CLUSTER_MIN or ai.ai_enabled():
            assigned, (clusters, mode) = {}, ai.cluster_evidence(items)
        else:
            return {"skipped": "too few"}
        by_problem: dict[int, list[int]] = {}
        for eid, pid in assigned.items():
            by_problem.setdefault(pid, []).append(eid)
        for pid, eids in by_problem.items():
            core.assign_evidence(eids, pid)
        created = core.save_problems(clusters) if clusters else []
        return {"mode": mode, "assigned": len(assigned), "problems_created": len(created)}
    finally:
        core.release_lock("analysis")
