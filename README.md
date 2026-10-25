# Groundwork

An evidence-to-code workspace for product teams. Customer evidence goes in, and a traceable chain comes out:

Evidence -> Problems -> Strategy themes -> Initiatives -> Stories -> Agent hand-off -> Outcome

Teams share a workspace, sign in with Google, Microsoft or email, push work to Linear, and let coding agents read briefs and report results over MCP.

## Run it

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env      # then fill it in (see Setup)
    bash run.sh

Open http://127.0.0.1:8000. Without `ANTHROPIC_API_KEY` it runs in offline mode (TF-IDF clustering and template drafts); with one, Claude does the grouping, specs, canvas, themes, stories and Ask Velo.

## Setup

**Database and sign-in: Supabase.** Dashboard labels move around, so treat these as a map rather than exact clicks.

1. Create a Supabase project. Under Project Settings > Database copy the connection string into `DATABASE_URL`. Use the transaction pooler (port 6543) when deploying. Tables are created on first start.
2. Under Project Settings > API copy the project URL into `SUPABASE_URL` and the anon public key into `SUPABASE_ANON_KEY`. Only projects still on the legacy shared secret also need `SUPABASE_JWT_SECRET`.
3. Authentication > Providers: enable **Google** and **Azure (Microsoft)**. Each needs an OAuth app (Google Cloud console, Microsoft Entra) whose redirect URI is the callback URL Supabase shows you. Email sign-in is on by default; keep "Confirm email" on.
4. Authentication > URL Configuration: set the Site URL to your app's address and add `https://YOUR-DOMAIN/auth/callback` (and `http://localhost:8000/auth/callback` for development) to the redirect URLs.
5. Set `GROUNDWORK_SECRET_KEY` to 32+ random characters. It encrypts integration credentials.

Without `SUPABASE_URL` the login page falls back to a plain email and password form so you can develop locally. You still need `DATABASE_URL`; any Postgres 14+ works.

**How data is kept apart.** Each workspace is its own Postgres schema (`ws_<id>`), so one workspace's queries cannot see another's rows. Users, memberships, invites and sessions live in a private `gw` schema. Neither is `public`, because Supabase publishes `public` through its REST API with the anon key. The API roles are also revoked from both, and a test checks it.

## Teams

Create more workspaces from the switcher in the sidebar. Under **Team**, admins create single-use invite links (7 days, tied to the invited email address) and set roles:

| Role | Can |
| --- | --- |
| Owner | everything, including owners and deleting the workspace |
| Admin | manage members, invites and integrations |
| Member | create and edit evidence, problems, initiatives |
| Viewer | read only |

Groundwork does not send email, so copy the link and share it.

## Integrations

Open **Integrations**. Each connection is per workspace, and credentials are encrypted at rest.

**Linear (ready).** Paste a personal API key (Linear > Settings > Account > Security & access), pick the team, then use **Push to Linear** on an initiative's Hand off tab. The initiative becomes a parent issue and each story a sub-issue; pushing again updates them rather than duplicating. For live status, add a webhook in Linear for Issues pointing at the URL shown on the card and paste its signing secret. **Sync now** pulls status without a webhook. Status mapping: backlog is proposed, unstarted is approved, started is in progress, completed is shipped. A measured initiative is never moved back.

**Listed as coming soon:** Jira, Trello, Asana, GitHub Issues; Dovetail, Intercom, Zendesk, Gong, Fathom, Typeform; Gmail, Outlook, Slack. A new tracker is one class in `app/integrations/` implementing `Integration` and one line in `catalog.py`.

## Agents (MCP)

    python mcp_server.py            # stdio
    python mcp_server.py --http     # http on port 8765

The Hand off tab shows a ready-made `claude mcp add` command containing your token. The agent acts as you in that workspace, with your role. Tools: `list_initiatives`, `list_problems`, `get_initiative_context`, `get_product_context`, `get_evidence`, `add_evidence`, `update_status`, `report_outcome`, `list_integrations`, `push_initiative`. It reads `DATABASE_URL` from the project's `.env`.

## Tests

    pip install -r requirements-dev.txt
    TEST_DATABASE_URL=postgresql://user@localhost/throwaway python -m pytest tests

The database is wiped every run, so point it at a throwaway one. Linear is tested against an in-memory fake of its API.

## Deploy

GitHub stores the code but cannot run it. `render.yaml` is a Render Blueprint: New > Blueprint > pick this repo, then fill the Supabase values. Set `GROUNDWORK_SECURE_COOKIES=1` (the blueprint does).

## Not built

Linear OAuth (API keys only), Jira, Trello, Asana, GitHub, and every discovery or inbox importer (listed, not working), password reset page, email delivery for invites, billing. The pricing on the landing page is a placeholder.

## Upgrading from the SQLite versions

Those versions kept data in local `.db` files and are not migrated. Start fresh on Postgres.
