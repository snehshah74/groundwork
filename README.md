# Groundwork

An evidence-to-code workspace for product teams. Customer evidence goes in, and a traceable chain comes out:

Evidence -> Problems -> Strategy themes -> Initiatives -> Stories -> Agent hand-off -> Outcome

Teams share a workspace, sign in with Google, Microsoft or email, push work to Linear, and let coding agents read briefs and report results over MCP.

## Run it

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env      # then fill it in (see Setup)
    bash run.sh

Open http://127.0.0.1:8000 (if that port is busy, `run.sh` says what is using it; use another with `PORT=8001 bash run.sh`). Without `ANTHROPIC_API_KEY` it runs in offline mode (TF-IDF clustering and template drafts); with one, Claude does the grouping, specs, canvas, themes, stories and Ask Velo.

## Setup

**Database and sign-in: Supabase.** Dashboard labels move around, so treat these as a map rather than exact clicks.

1. Create a Supabase project. Under Project Settings > Database copy the connection string into `DATABASE_URL`. Use the transaction pooler (port 6543) when deploying. Tables are created on first start.
2. Under Project Settings > API copy the project URL into `SUPABASE_URL` and the public key (labelled anon, or publishable and starting `sb_publishable_`) into `SUPABASE_ANON_KEY`. Never use the service_role or secret key. If sign-in reports an invalid key, raise `SUPABASE_JS_VERSION`. Only projects still on the legacy shared secret also need `SUPABASE_JWT_SECRET`.
3. Authentication > Providers: enable **Google** and **Azure (Microsoft)**. Each needs an OAuth app (Google Cloud console, Microsoft Entra) whose redirect URI is the callback URL Supabase shows you. Email sign-in is on by default; keep "Confirm email" on.
4. Authentication > URL Configuration: set the Site URL to your app's address and add `https://YOUR-DOMAIN/auth/callback` and `https://YOUR-DOMAIN/reset` (and the `http://localhost:8000` versions for development) to the redirect URLs. The first finishes Google, Microsoft and email-link sign-in; the second is where password-reset emails land.
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

Open **Integrations**. Connections are per workspace and credentials are encrypted at rest. Admins connect; members push and sync.

**Work trackers.** Pick where work should go, then use **Push to ...** on an initiative's Hand off tab. The initiative becomes the parent item and each story a child; pushing again updates them rather than duplicating. Status flows back and **only ever moves forward** (proposed, approved, in progress, shipped), so a freshly pushed item sitting in Backlog never undoes "in progress". A measured initiative is never moved.

| Tool | Parent / child | Status comes from | Live updates |
| --- | --- | --- | --- |
| **Linear** | issue / sub-issues | workflow state type | webhook (HMAC-signed) or Sync now |
| **Jira Cloud** | Epic / Stories | status category (To Do, In Progress, Done) | webhook (HMAC-signed) or Sync now |
| **Trello** | card / "Stories" checklist | the name of the list the card is in (Done, Doing, To Do, ...) | Sync now |

Linear needs a personal API key, Jira an email plus API token (site must be `*.atlassian.net`; Data Center is not supported), Trello an API key plus token. Webhooks are optional: each card shows the URL and where to paste the signing secret.

**Customer discovery.** **Zendesk** imports support tickets as evidence (subject and first message). Re-importing never duplicates: every item has a stable reference enforced by a unique index. The first import looks back 90 days, later ones continue from where the last stopped (up to 200 tickets per run). **Emails and phone numbers are masked** on the way in, because evidence ends up in specs and in briefs sent to agents and to Claude.

**Scheduled runs.** `POST /api/cron/run` (or `python -m app.cron`) syncs every tracker and imports from every discovery tool in every workspace; one failing connection never stops the rest. The endpoint is off unless `GROUNDWORK_CRON_TOKEN` is set (16+ characters) and answers 404 otherwise. `.github/workflows/scheduled-sync.yml` calls it every 30 minutes once you add `APP_URL` and `CRON_TOKEN` as repository secrets.

**Listed as coming soon:** Asana, GitHub Issues; Dovetail, Intercom, Gong, Fathom, Typeform; Gmail, Outlook, Slack. A new tool is one class in `app/integrations/` implementing `Integration` (see `base.py`) and one line in `catalog.py`. Trackers implement `connect`, `push_initiative`, `pull_status` and optionally `verify_webhook` and `parse_event`; importers implement `import_evidence`.

## Working with evidence

- **Automatic grouping.** New evidence (typed, uploaded or imported) joins the closest problem, or forms a new one once enough has piled up. Uses Claude when `ANTHROPIC_API_KEY` is set, otherwise a TF-IDF match with a similarity floor. Turn off under **Team > Workspace settings**.
- **Priorities.** Themes carry a priority; the Strategy view shows each problem's score as its parts (evidence, customers, recency) and a ranked "build next" list.
- **Out-of-date briefs.** If evidence or the spec changes after an agent or tracker last received it, the initiative is flagged.
- **Uploads.** PDF, Word (.docx) and Markdown/text files become evidence or a product source. Files are size, page and decompression limited and parsed as untrusted.
- **Documents.** PRD, launch plan and HTML prototype per initiative. Prototypes are stored sanitised with a strict CSP and only shown in a sandboxed iframe, never served as a page.
- **Digest.** Findings such as stale briefs and what to build next, on demand or weekly via the scheduled run (**Team > Workspace settings**).
- **Onboarding.** New workspaces get a short guided setup that can load a demo.
- **AI credits.** `GROUNDWORK_AI_CREDITS` sets a monthly per-workspace limit (empty = unlimited). One credit is one AI action, bigger ones cost more. When used up, drafts fall back to basic offline mode. Usage shows under **Team**.

## Public pages

`/` landing, `/features`, `/privacy` (how the software handles data; it is not a legal policy, add your own before launch), `/login`, `/signup`.

## Agents (MCP)

    python mcp_server.py            # stdio
    python mcp_server.py --http     # http on port 8765

The Hand off tab shows a ready-made `claude mcp add` command containing your token. The agent acts as you in that workspace, with your role. Tools: `list_initiatives`, `list_problems`, `get_initiative_context`, `get_product_context`, `get_evidence`, `add_evidence`, `update_status`, `report_outcome`, `list_integrations`, `push_initiative`, `whats_changed`. Agent reads, writes and denied attempts are recorded and shown on each initiative. It reads `DATABASE_URL` from the project's `.env`. Rotate a leaked token from the Hand off tab.

## Operations

- **Migrations.** Schemas are versioned (`core.MIGRATIONS` for workspaces, `auth.SHARED_MIGRATIONS` for the shared schema). Never edit a released migration: append a new `(version, description, sql)`. Workspaces upgrade themselves on first use after a deploy, each migration all-or-nothing. `python -m app.migrate` upgrades every workspace ahead of time and prints what moved.
- **Health.** `GET /api/health` returns 200 with the schema version, or 503 when the database is unreachable.
- **Audit log and data.** Admins see who invited, changed roles, removed members, connected or pushed under **Team > Activity**, and can download the whole workspace as JSON (credentials excluded). Anyone can delete their own account: workspaces only they belonged to go with it, and a shared workspace needs another owner first.
- **Abuse limits.** Failed logins are limited per address and email, counted in Postgres so they hold across server processes. Browser writes from another site are refused (Origin check, on top of SameSite cookies). Behind a proxy, run uvicorn with `--proxy-headers` so the real client address is used, and only when the proxy is trusted (the Render blueprint does).
- **Passwords.** With Supabase, `/reset` sends the reset email and lets people choose a new password. Deleting an account removes it from Groundwork but the identity in Supabase Auth remains until you delete it in the Supabase dashboard.

## Tests

    pip install -r requirements-dev.txt
    TEST_DATABASE_URL=postgresql://user@localhost/throwaway python -m pytest tests

The database is wiped every run, so point it at a throwaway one. The trackers and Zendesk are tested against in-memory fakes of their APIs; the fakes follow each tool's documented API, so run one real push per tool with a test account before relying on it.

## Deploy

GitHub stores the code but cannot run it. `render.yaml` is a Render Blueprint: New > Blueprint > pick this repo, then fill the Supabase values. Set `GROUNDWORK_SECURE_COOKIES=1` (the blueprint does).

## Not built

Linear OAuth (API keys only), Trello webhooks, Asana, GitHub, and every discovery importer except Zendesk (listed, not working), Gmail and Outlook as inbox sources, SAML single sign-on, email delivery for invites, billing. Stories deleted in Groundwork are not closed in the tracker. The pricing on the landing page is a placeholder.

## Upgrading from the SQLite versions

Those versions kept data in local `.db` files and are not migrated. Start fresh on Postgres.
