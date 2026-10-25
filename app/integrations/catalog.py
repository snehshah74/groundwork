"""Every integration Groundwork knows about. 'ready' ones have code; 'soon' ones are listed so the roadmap is visible
and a provider can be added by writing one class and registering it here."""
from __future__ import annotations

from .base import Integration, Meta
from .linear import Linear

REGISTRY: dict[str, type[Integration]] = {"linear": Linear}


def _soon(id: str, name: str, kind: str, description: str) -> Meta:
    return Meta(id=id, name=name, kind=kind, description=description, status="soon")


COMING_SOON: list[Meta] = [
    # work trackers
    _soon("jira", "Jira", "tracker", "Create epics and stories in Jira and sync status back."),
    _soon("trello", "Trello", "tracker", "Create cards with checklists for each story and follow list moves."),
    _soon("asana", "Asana", "tracker", "Create tasks and subtasks and sync completion."),
    _soon("github", "GitHub Issues", "tracker", "Open issues per story and close the loop when they ship."),
    # customer discovery
    _soon("dovetail", "Dovetail", "discovery", "Import research highlights and tagged insights as evidence."),
    _soon("intercom", "Intercom", "discovery", "Pull conversations and feedback from customers."),
    _soon("zendesk", "Zendesk", "discovery", "Import support tickets as evidence."),
    _soon("gong", "Gong", "discovery", "Import call transcripts and key moments."),
    _soon("fathom", "Fathom", "discovery", "Import customer-call notes and transcripts."),
    _soon("typeform", "Typeform", "discovery", "Import survey responses as they arrive."),
    # inboxes and chat
    _soon("gmail", "Gmail", "comms", "Turn labelled customer emails into evidence."),
    _soon("outlook", "Outlook", "comms", "Turn customer emails from a Microsoft 365 mailbox into evidence."),
    _soon("slack", "Slack", "comms", "Capture feedback from channels and notify a channel when work ships."),
]


def all_metas() -> list[Meta]:
    return [cls.meta for cls in REGISTRY.values()] + COMING_SOON


def get(provider: str) -> Integration | None:
    cls = REGISTRY.get(provider)
    return cls() if cls else None
