"""Shared contract for every integration.

Two families share this base:
  tracker    pushes initiatives and stories into Jira, Linear, Trello... and reads status back
  discovery  imports customer evidence from Dovetail, Intercom, Gong, Gmail...
A provider implements only what it can do; the catalog shows the rest as unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class IntegrationError(Exception):
    """A problem the user can act on (bad key, missing team, upstream refused). The message is shown as is."""


@dataclass
class Link:
    """A Groundwork item and its counterpart in the other tool."""
    kind: str            # 'initiative' | 'story'
    local_id: int
    external_id: str
    external_key: str = ""
    external_url: str = ""
    external_status: str = ""


@dataclass
class Field:
    name: str
    label: str
    secret: bool = False
    help: str = ""


@dataclass
class Meta:
    id: str
    name: str
    kind: str            # 'tracker' | 'discovery' | 'comms'
    description: str
    auth: str = "api_key"        # 'api_key' | 'oauth'
    fields: list[Field] = field(default_factory=list)
    status: str = "soon"         # 'ready' | 'soon'
    target_label: str = "team"   # what the user picks to send work to: team, project, list...
    webhooks: bool = False       # can it push status changes to us?
    webhook_help: str = ""       # one sentence telling an admin where to create the webhook in the other tool


class Integration:
    """Subclass, set `meta`, and implement what applies."""
    meta: Meta

    # --- connecting
    def connect(self, credentials: dict) -> tuple[dict, dict]:
        """Validate credentials against the real service. Returns (public_config, secret_data)."""
        raise NotImplementedError

    # --- trackers
    def push_initiative(self, cfg: dict, secret: dict, initiative: dict, stories: list[dict], links: dict[tuple[str, int], Link]) -> list[Link]:
        raise NotImplementedError

    def pull_status(self, cfg: dict, secret: dict, links: list[Link]) -> dict[str, dict]:
        """external_id -> {"state_type", "state_name", "url"}"""
        raise NotImplementedError

    def verify_webhook(self, cfg: dict, secret: dict, body: bytes, headers: dict) -> dict:
        """Check the signature and return the parsed event. Raise IntegrationError when it is not genuine."""
        raise NotImplementedError

    def parse_event(self, event: dict) -> dict | None:
        """Reduce a verified webhook event to {external_id, state_type, state_name, url, key}, or None to ignore it.
        state_type is one of triage, backlog, unstarted, started, completed (the vocabulary of STATE_TO_STATUS)."""
        return None

    # --- discovery
    def import_evidence(self, cfg: dict, secret: dict, since: str | None) -> list[dict]:
        """[{text, source, external_ref}]"""
        raise NotImplementedError


# ---------------------------------------------------------------- shared rendering for trackers

def initiative_markdown(i: dict, problem: dict | None, evidence_count: int) -> str:
    parts = []
    if i.get("problem_statement"):
        parts += ["## Problem", i["problem_statement"], ""]
    if i.get("persona"):
        parts += ["## Who it is for", i["persona"], ""]
    if i.get("success_metric"):
        parts += ["## Success looks like", i["success_metric"], ""]
    if i.get("acceptance_criteria"):
        parts += ["## Acceptance criteria"] + [f"- [ ] {a}" for a in i["acceptance_criteria"]] + [""]
    if i.get("out_of_scope"):
        parts += ["## Out of scope"] + [f"- {o}" for o in i["out_of_scope"]] + [""]
    if problem:
        parts += [f"Customer problem: **{problem['title']}** ({problem['severity']} severity), backed by {evidence_count} pieces of customer evidence.", ""]
    parts.append(f"_Created from Groundwork initiative #{i['id']}._")
    return "\n".join(parts).strip()


def story_markdown(s: dict) -> str:
    parts = []
    if s.get("description"):
        parts += [s["description"], ""]
    if s.get("acceptance"):
        parts += ["## Acceptance criteria"] + [f"- [ ] {a}" for a in s["acceptance"]] + [""]
    if s.get("activity"):
        parts.append(f"_Journey step: {s['activity']}_")
    return "\n".join(parts).strip()


# Groundwork status <- tracker state type. Statuses only ever move forward from a tracker.
STATUS_ORDER = ["proposed", "approved", "in_progress", "shipped", "measured"]
STATE_TO_STATUS = {"triage": "proposed", "backlog": "proposed", "unstarted": "approved", "started": "in_progress", "completed": "shipped"}


def next_status(current: str, state_type: str) -> str | None:
    """Status an initiative should take when its tracker issue is in `state_type`, or None to leave it alone.

    Forward only. A just-pushed issue sits in Backlog while the initiative is already "in progress" (pushing is the
    hand-off), and a reopened or mis-dragged issue should not un-ship or un-measure work. Teams that want to go back
    can still set the status by hand.
    """
    target = STATE_TO_STATUS.get(state_type)
    if not target or current not in STATUS_ORDER:
        return None
    return target if STATUS_ORDER.index(target) > STATUS_ORDER.index(current) else None
