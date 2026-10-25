# Groundwork

An evidence-to-code workspace for product teams. Customer evidence goes in, and a traceable chain comes out:

Evidence -> Problems -> Strategy themes -> Initiatives -> Stories -> Agent hand-off -> Outcome

## Run it

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=your-key   # optional
    bash run.sh

Open http://127.0.0.1:8000. Without a key it runs in offline mode (TF-IDF clustering and template drafts). With a key, Claude does the grouping, specs, canvas, themes, stories and Ask Velo.

## Pages and accounts

- `/` landing page, `/signup`, `/login`, and `/app` for the workspace (redirects to `/login` without a session)
- Passwords are hashed with scrypt. Sessions are random tokens stored hashed, sent in an HttpOnly, SameSite=Lax cookie. Login attempts are rate limited per IP and email.
- Data lives in `data/` (override with `GROUNDWORK_DATA`): `auth.db` for users and sessions, and one SQLite file per user in `data/workspaces/`, so accounts are isolated from each other.
- Behind HTTPS, set `GROUNDWORK_SECURE_COOKIES=1`.

## What is in it

- Home: pipeline strip, rule-based insights, recent activity
- Canvas: lean product canvas, generated from your evidence or edited by hand
- Evidence: paste or upload feedback, tagged by source
- Problems: SVG problem map and list, with a detail panel
- Strategy: drag problems into themes
- Initiatives: kanban board, drag cards between statuses
- Initiative detail: spec, story map, trace (evidence to outcome), hand-off, outcome
- Ask Velo: chat over your whole workspace
- Ticket CSV export (epic plus stories)

## Agents (MCP)

    GROUNDWORK_TOKEN=<your token> python mcp_server.py            # stdio
    GROUNDWORK_TOKEN=<your token> python mcp_server.py --http     # http on port 8765

The token identifies your account. Copy a ready-made `claude mcp add` command from the Hand off tab of any initiative.

Tools: list_initiatives, get_initiative_context, get_product_context, get_evidence, update_status, report_outcome.

## Upgrading from v1

v1 had one shared `groundwork.db` and no accounts. Accounts start with empty workspaces; to keep v1 data, copy the old file to `data/workspaces/user_1.db` before the first signup.

## Not built

Jira or Linear sync, Gmail or Intercom importers, shared team workspaces, password reset, billing. The pricing on the landing page is placeholder.
