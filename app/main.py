"""Groundwork web API and UI host. Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import hmac
import os

from fastapi import APIRouter, BackgroundTasks, Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from starlette.concurrency import run_in_threadpool

from . import ai, analysis, auth, core, db, teams
from .integrations import service as integrations
from .integrations.base import IntegrationError
from .seed_data import SEED_EVIDENCE, SEED_PRODUCT

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Groundwork")
auth.init_shared()


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_origin(request: Request) -> None:
    """Browsers attach an Origin header to cross-site writes. Refuse any that is not this site, on top of SameSite cookies.
    Requests with no Origin (curl, scripts) are not browser sessions being tricked, so they pass."""
    origin = request.headers.get("origin")
    if origin is None or request.method in SAFE_METHODS:
        return
    host = request.headers.get("host", "")
    allowed = {f"http://{host}", f"https://{host}", *[o.strip() for o in os.environ.get("GROUNDWORK_ALLOWED_ORIGINS", "").split(",") if o.strip()]}
    if origin not in allowed:
        raise HTTPException(403, "Cross-site request blocked.")


def _resolve(session_token: str | None, ws_cookie: str | None):
    """Session -> (user, active workspace membership). Creates a first workspace when there is none."""
    user = auth.user_by_session(session_token)
    if not user:
        return None, None
    ws = teams.membership(user["id"], int(ws_cookie)) if ws_cookie and ws_cookie.isdigit() else None
    if not ws:
        mine = teams.workspaces_for(user["id"])
        made = mine[0] if mine else teams.create_workspace(user["id"], (user["name"] or user["email"].split("@")[0]) + "'s workspace")
        ws = teams.membership(user["id"], made["id"])
    core.init_workspace(db.schema_name(ws["id"]))
    return user, ws


async def require_user(request: Request) -> dict:
    """Every workspace route runs as a signed-in member, against the active workspace's own schema."""
    _same_origin(request)
    user, ws = await run_in_threadpool(_resolve, request.cookies.get(auth.COOKIE), request.cookies.get(auth.WS_COOKIE))
    if not user:
        raise HTTPException(401, "Please log in to continue.")
    if request.method not in SAFE_METHODS and teams.rank(ws["role"]) < teams.rank("member"):
        raise HTTPException(403, "You have view-only access to this workspace.")
    db.use_schema(db.schema_name(ws["id"]))  # set here, in the request's own context, so the endpoint sees it
    return {"user": user, "ws": ws}


def needs(role: str):
    async def dep(ctx: dict = Depends(require_user)) -> dict:
        if teams.rank(ctx["ws"]["role"]) < teams.rank(role):
            raise HTTPException(403, f"This needs {role} access.")
        return ctx
    return dep


api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
public = APIRouter(prefix="/api/auth")


# ---------------------------------------------------------------- models
class SettingsIn(BaseModel):
    target_id: str | None = None
    webhook_secret: str | None = None


class ExchangeIn(BaseModel):
    access_token: str


class WorkspaceIn(BaseModel):
    name: str


class InviteIn(BaseModel):
    email: str
    role: str = "member"


class SettingsPatch(BaseModel):
    auto_analyze: bool | None = None


class EvidenceMove(BaseModel):
    problem_id: int | None = None


class DeleteAccountIn(BaseModel):
    confirm: str


class AcceptIn(BaseModel):
    token: str


class RolePatch(BaseModel):
    role: str


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class EvidenceIn(BaseModel):
    text: str
    source: str = "note"


class AnalyzeIn(BaseModel):
    reanalyze: bool = False


class ProblemPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    persona: str | None = None
    severity: str | None = None


class ThemeAssign(BaseModel):
    theme_id: int | None = None


class ThemeIn(BaseModel):
    title: str
    description: str = ""


class ThemePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: int | None = None


class ProductIn(BaseModel):
    name: str | None = None
    overview: str | None = None
    canvas: dict[str, str] | None = None


class InitiativeIn(BaseModel):
    problem_id: int


class InitiativePatch(BaseModel):
    status: str | None = None
    title: str | None = None
    persona: str | None = None
    problem_statement: str | None = None
    success_metric: str | None = None
    out_of_scope: list[str] | None = None
    acceptance_criteria: list[str] | None = None
    theme_id: int | None = None


class StoryIn(BaseModel):
    title: str
    activity: str = ""
    description: str = ""
    acceptance: list[str] = []


class StoryPatch(BaseModel):
    title: str | None = None
    activity: str | None = None
    description: str | None = None
    acceptance: list[str] | None = None


class DispatchIn(BaseModel):
    target: str = "claude-code"


class OutcomeIn(BaseModel):
    verdict: str
    metric_value: str = ""
    note: str = ""


class Message(BaseModel):
    role: str
    content: str


class AskIn(BaseModel):
    messages: list[Message]


def _bad(e: Exception):
    raise HTTPException(400, str(e))


# ---------------------------------------------------------------- shell
def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-store"})


def _session_user(request: Request) -> dict | None:
    return auth.user_by_session(request.cookies.get(auth.COOKIE))


@app.get("/")
def landing():
    return _page("landing.html")


@app.get("/login")
def login_page(request: Request):
    return RedirectResponse("/app") if _session_user(request) else _page("login.html")


@app.get("/signup")
def signup_page(request: Request):
    return RedirectResponse("/app") if _session_user(request) else _page("signup.html")


@app.get("/reset")
def reset_page():
    return _page("reset.html")


@app.get("/auth/callback")
def auth_callback():
    return _page("callback.html")


@app.get("/invite/{token}")
def invite_page(token: str):
    return _page("invite.html")


@app.get("/app")
def app_page(request: Request):
    return _page("app.html") if _session_user(request) else RedirectResponse("/login")


@app.get("/api/health")
def health():
    """For load balancers and uptime checks. 503 when the database cannot be reached."""
    try:
        return {"ok": True, "schema_version": db.schema_version(db.SHARED)}
    except Exception:
        return JSONResponse({"ok": False}, status_code=503)


@app.get("/api/config")
def config():
    """Public settings the sign-in pages need. The anon key is designed to be public."""
    return {
        "mode": "supabase" if auth.supabase_enabled() else "local",
        "supabase_url": auth.SUPABASE_URL if auth.supabase_enabled() else "",
        "supabase_anon_key": auth.SUPABASE_ANON_KEY if auth.supabase_enabled() else "",
        "supabase_js_version": os.environ.get("SUPABASE_JS_VERSION", "2.45.4"),   # browser library; bump if Supabase rejects it
    }


# ---------------------------------------------------------------- accounts
MAX_FAILURES, WINDOW = 8, 600     # per key, in seconds; counted in Postgres so every server process shares them


def _throttled(key: str) -> bool:
    return auth.rate_blocked(key, MAX_FAILURES, WINDOW)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _start_session(user: dict) -> JSONResponse:
    resp = JSONResponse({"user": user})
    resp.set_cookie(
        auth.COOKIE, auth.create_session(user["id"]), max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=auth.SECURE_COOKIES, path="/",
    )
    return resp


def _local_only():
    if auth.supabase_enabled():
        raise HTTPException(404, "Use the sign-in options on the login page.")


@public.post("/exchange")
def exchange(body: ExchangeIn, request: Request):
    """Trade a Supabase access token (Google, Microsoft or email sign-in) for a Groundwork session."""
    _same_origin(request)
    key = f"exchange:{_ip(request)}"
    if _throttled(key):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    try:
        claims = auth.verify_supabase_token(body.access_token)
    except ValueError as e:
        auth.rate_hit(key)          # only bad tokens count, so people sharing an address can all sign in
        raise HTTPException(401, str(e))
    meta = claims.get("user_metadata") or {}
    user = auth.upsert_supabase_user(claims["sub"], claims["email"], meta.get("full_name") or meta.get("name") or "")
    return _start_session(user)


@public.post("/signup")
def signup(body: SignupIn, request: Request):
    _local_only()
    _same_origin(request)
    key = f"signup:{_ip(request)}"
    if _throttled(key):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    auth.rate_hit(key)
    try:
        user = auth.create_user(body.email, body.password, body.name)
    except ValueError as e:
        _bad(e)
    return _start_session(user)


@public.post("/login")
def login(body: LoginIn, request: Request):
    _local_only()
    _same_origin(request)
    key = f"login:{_ip(request)}:{body.email.strip().lower()}"
    if _throttled(key):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    user = auth.authenticate(body.email, body.password)
    if not user:
        auth.rate_hit(key)
        raise HTTPException(401, "That email and password do not match.")
    auth.rate_clear(key)
    return _start_session(user)


@public.post("/logout")
def logout(request: Request):
    _same_origin(request)
    auth.delete_session(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    resp.delete_cookie(auth.WS_COOKIE, path="/")
    return resp


@public.get("/me")
def me(request: Request):
    """Always 200 so public pages can check for a session without a console error."""
    user, ws = _resolve(request.cookies.get(auth.COOKIE), request.cookies.get(auth.WS_COOKIE))
    if not user:
        return {"user": None}
    return {"user": user, "workspace": {"id": ws["id"], "name": ws["name"], "role": ws["role"]},
            "workspaces": teams.workspaces_for(user["id"]), "api_token": ws["api_token"]}


def _need_session(request: Request) -> dict:
    _same_origin(request)
    user = _session_user(request)
    if not user:
        raise HTTPException(401, "Please log in to continue.")
    return user


@public.post("/workspaces")
def create_workspace(body: WorkspaceIn, request: Request):
    user = _need_session(request)
    try:
        ws = teams.create_workspace(user["id"], body.name)
    except ValueError as e:
        _bad(e)
    resp = JSONResponse(ws)
    resp.set_cookie(auth.WS_COOKIE, str(ws["id"]), httponly=True, samesite="lax", secure=auth.SECURE_COOKIES, path="/")
    return resp


@public.post("/workspaces/{wid}/switch")
def switch_workspace(wid: int, request: Request):
    user = _need_session(request)
    if not teams.membership(user["id"], wid):
        raise HTTPException(404, "Workspace not found.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.WS_COOKIE, str(wid), httponly=True, samesite="lax", secure=auth.SECURE_COOKIES, path="/")
    return resp


@public.post("/workspaces/{wid}/leave")
def leave_workspace(wid: int, request: Request):
    user = _need_session(request)
    m = teams.membership(user["id"], wid)
    if not m:
        raise HTTPException(404, "Workspace not found.")
    try:
        teams.remove_member(wid, user["id"], m["role"])
    except ValueError as e:
        _bad(e)
    teams.audit(wid, user, "member.leave", user["email"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.WS_COOKIE, path="/")
    return resp


@public.delete("/account")
def delete_account(body: DeleteAccountIn, request: Request):
    user = _need_session(request)
    if body.confirm.strip().lower() != user["email"].lower():
        raise HTTPException(400, "Type your email address to confirm.")
    try:
        teams.delete_user(user["id"])
    except ValueError as e:
        _bad(e)
    resp = JSONResponse({"deleted": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    resp.delete_cookie(auth.WS_COOKIE, path="/")
    return resp


@public.get("/invites/{token}")
def invite_preview(token: str):
    inv = teams.invite_preview(token)
    if not inv:
        raise HTTPException(404, "This invite link is invalid or has expired.")
    return inv


@public.post("/invites/accept")
def accept_invite(body: AcceptIn, request: Request):
    user = _need_session(request)
    try:
        ws = teams.accept_invite(body.token, user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        _bad(e)
    teams.audit(ws["id"], user, "invite.accept", f"joined as {ws['role']}")
    resp = JSONResponse(ws)
    resp.set_cookie(auth.WS_COOKIE, str(ws["id"]), httponly=True, samesite="lax", secure=auth.SECURE_COOKIES, path="/")
    return resp


@api.get("/status")
def status():
    s = core.get_stats()
    return {"ai_mode": "claude" if ai.ai_enabled() else "offline", "model": ai.MODEL, **s}


@api.get("/home")
def home():
    return {
        "stats": core.get_stats(),
        "insights": core.compute_insights(),
        "activity": core.recent_activity(),
        "product": core.get_product(),
    }


# ---------------------------------------------------------------- product canvas
@api.get("/priorities")
def get_priorities():
    return core.priorities()


@api.get("/settings")
def get_settings():
    return {"auto_analyze": analysis.enabled(), "onboarded": core.get_setting("onboarded", "0") == "1"}


@api.put("/settings")
def put_settings(body: SettingsPatch, ctx: dict = Depends(needs("admin"))):
    if body.auto_analyze is not None:
        core.set_setting("auto_analyze", "1" if body.auto_analyze else "0")
        teams.audit(ctx["ws"]["id"], ctx["user"], "workspace.settings", f"automatic analysis {'on' if body.auto_analyze else 'off'}")
        if body.auto_analyze:
            analysis.auto_analyze()       # catch up on anything that arrived while it was off
    return get_settings()


@api.get("/product")
def get_product():
    return core.get_product()


@api.put("/product")
def put_product(body: ProductIn):
    return core.save_product(body.name, body.overview, body.canvas)


@api.post("/product/generate")
def generate_product(body: ProductIn):
    cur = core.get_product()
    name = body.name if body.name is not None else cur["name"]
    overview = body.overview if body.overview is not None else cur["overview"]
    if not (overview or "").strip():
        raise HTTPException(400, "Describe your product first, in a sentence or two.")
    canvas, mode = ai.generate_canvas(name, overview, core.list_problems())
    p = core.save_product(name, overview, canvas)
    return {**p, "mode": mode}


# ---------------------------------------------------------------- evidence
@api.get("/evidence")
def get_evidence():
    return core.list_evidence()


@api.post("/evidence")
def post_evidence(body: EvidenceIn, background: BackgroundTasks):
    try:
        e = core.add_evidence(body.text, body.source)
    except ValueError as err:
        _bad(err)
    background.add_task(analysis.auto_analyze, db.current_schema())     # after the response, so adding evidence stays instant
    return e


@api.delete("/evidence/{eid}")
def remove_evidence(eid: int):
    if not core.delete_evidence(eid):
        raise HTTPException(404, "Evidence not found")
    return {"deleted": eid}


@api.put("/evidence/{eid}/problem")
def move_evidence(eid: int, body: EvidenceMove):
    """Move a piece of evidence to another problem, or out of its problem with null."""
    if not core.one("SELECT 1 AS x FROM evidence WHERE id=?", (eid,)):
        raise HTTPException(404, "Evidence not found")
    if body.problem_id is not None and not core.one("SELECT 1 AS x FROM problems WHERE id=?", (body.problem_id,)):
        raise HTTPException(404, "Problem not found")
    core.assign_evidence([eid], body.problem_id)
    return core.one("SELECT * FROM evidence WHERE id=?", (eid,))


def _ingest_upload(filename: str, raw: bytes) -> int:
    text = raw.decode("utf-8", errors="replace")
    added = 0
    if filename.lower().endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        text_col = cols.get("text") or cols.get("feedback") or cols.get("message") or cols.get("body")
        if not text_col:
            raise HTTPException(400, "CSV needs a column named text, feedback, message, or body")
        src_col = cols.get("source")
        for r in reader:
            if (r.get(text_col) or "").strip():
                core.add_evidence(r[text_col], (r.get(src_col) if src_col else "") or filename or "csv")
                added += 1
    else:
        for chunk in [c for c in text.split("\n\n") if c.strip()]:
            core.add_evidence(chunk, filename or "upload")
            added += 1
    return added


@api.post("/evidence/upload")
async def upload_evidence(background: BackgroundTasks, file: UploadFile = File(...)):
    """CSV with a 'text' column (optional 'source'), or a plain text file split on blank lines."""
    raw = await file.read()
    added = await run_in_threadpool(_ingest_upload, file.filename or "", raw)   # many inserts: keep them off the event loop
    background.add_task(analysis.auto_analyze, db.current_schema())
    return {"added": added}


# ---------------------------------------------------------------- analysis and problems
@api.post("/analyze")
def analyze(body: AnalyzeIn = AnalyzeIn()):
    if body.reanalyze:
        core.reset_ungrounded()
    items = core.list_evidence(unassigned_only=True)
    if not items:
        raise HTTPException(400, "No ungrouped evidence to analyze. Add some first.")
    clusters, mode = ai.cluster_evidence(items)
    ids = core.save_problems(clusters)
    analyzed = sum(len(c["evidence_ids"]) for c in clusters)
    return {"mode": mode, "problems_created": len(ids), "evidence_analyzed": analyzed, "remaining": len(items) - analyzed}


@api.get("/problems")
def get_problems():
    return core.list_problems()


@api.get("/problems/{pid}")
def get_problem(pid: int):
    p = core.get_problem(pid)
    if not p:
        raise HTTPException(404, "Problem not found")
    return p


@api.patch("/problems/{pid}")
def patch_problem(pid: int, body: ProblemPatch):
    try:
        p = core.update_problem(pid, body.model_dump(exclude_none=True))
    except ValueError as e:
        _bad(e)
    if not p:
        raise HTTPException(404, "Problem not found")
    return p


@api.put("/problems/{pid}/theme")
def put_problem_theme(pid: int, body: ThemeAssign):
    p = core.set_problem_theme(pid, body.theme_id)
    if not p:
        raise HTTPException(404, "Problem not found")
    return p


# ---------------------------------------------------------------- strategy themes
@api.get("/themes")
def get_themes():
    return core.list_themes()


@api.post("/themes")
def post_theme(body: ThemeIn):
    try:
        return core.create_theme(body.title, body.description)
    except ValueError as e:
        _bad(e)


@api.patch("/themes/{tid}")
def patch_theme(tid: int, body: ThemePatch):
    try:
        t = core.update_theme(tid, body.model_dump(exclude_unset=True))
    except ValueError as e:
        _bad(e)
    if not t:
        raise HTTPException(404, "Theme not found")
    return t


@api.delete("/themes/{tid}")
def remove_theme(tid: int):
    if not core.delete_theme(tid):
        raise HTTPException(404, "Theme not found")
    return {"deleted": tid}


@api.post("/themes/generate")
def generate_themes():
    probs = core.list_problems()
    if not probs:
        raise HTTPException(400, "Analyze some evidence first so there are problems to group.")
    themes, mode = ai.generate_themes(core.get_product(), [p for p in probs if not p["theme_id"]] or probs)
    ids = core.save_themes(themes)
    return {"mode": mode, "themes_created": len(ids)}


# ---------------------------------------------------------------- initiatives
@api.post("/initiatives")
def create_initiative(body: InitiativeIn):
    p = core.get_problem(body.problem_id)
    if not p:
        raise HTTPException(404, "Problem not found")
    spec, mode = ai.generate_spec(p, p["evidence"])
    init = core.create_initiative(body.problem_id, spec)
    init["mode"] = mode
    return init


@api.get("/initiatives")
def get_initiatives():
    return core.list_initiatives()


@api.get("/initiatives/{iid}")
def get_initiative(iid: int):
    i = core.get_initiative(iid)
    if not i:
        raise HTTPException(404, "Initiative not found")
    return i


@api.patch("/initiatives/{iid}")
def patch_initiative(iid: int, body: InitiativePatch):
    try:
        i = core.update_initiative(iid, body.model_dump(exclude_unset=True))
    except ValueError as e:
        _bad(e)
    if not i:
        raise HTTPException(404, "Initiative not found")
    return i


@api.get("/initiatives/{iid}/context", response_class=PlainTextResponse)
def initiative_context(iid: int):
    md = core.build_context(iid)
    if md is None:
        raise HTTPException(404, "Initiative not found")
    return md


@api.get("/initiatives/{iid}/tickets.csv")
def initiative_tickets(iid: int):
    data = core.build_tickets_csv(iid)
    if data is None:
        raise HTTPException(404, "Initiative not found")
    return Response(data, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="initiative-{iid}-tickets.csv"'})


@api.post("/initiatives/{iid}/dispatch")
def dispatch(iid: int, body: DispatchIn):
    md = core.build_context(iid)
    if md is None:
        raise HTTPException(404, "Initiative not found")
    core.record_dispatch(iid, body.target)
    return {"target": body.target, "context": md}


@api.post("/initiatives/{iid}/outcome")
def outcome(iid: int, body: OutcomeIn):
    try:
        i = core.record_outcome(iid, body.verdict, body.metric_value, body.note)
    except ValueError as e:
        _bad(e)
    if not i:
        raise HTTPException(404, "Initiative not found")
    return i


# ---------------------------------------------------------------- stories
@api.post("/initiatives/{iid}/stories/generate")
def generate_stories(iid: int):
    i = core.get_initiative(iid)
    if not i:
        raise HTTPException(404, "Initiative not found")
    stories, mode = ai.generate_stories(i, i["evidence"])
    saved = core.save_stories(iid, stories, replace=True)
    return {"mode": mode, "stories": saved}


@api.post("/initiatives/{iid}/stories")
def add_story(iid: int, body: StoryIn):
    if not core.get_initiative(iid):
        raise HTTPException(404, "Initiative not found")
    if not body.title.strip():
        raise HTTPException(400, "Story title is empty")
    core.save_stories(iid, [{"activity": body.activity, "title": body.title.strip(), "description": body.description,
                             "acceptance_criteria": body.acceptance}], replace=False)
    return core.list_stories(iid)


@api.patch("/stories/{sid}")
def patch_story(sid: int, body: StoryPatch):
    s = core.update_story(sid, body.model_dump(exclude_none=True))
    if not s:
        raise HTTPException(404, "Story not found")
    return s


@api.delete("/stories/{sid}")
def remove_story(sid: int):
    if not core.delete_story(sid):
        raise HTTPException(404, "Story not found")
    return {"deleted": sid}


# ---------------------------------------------------------------- Ask Velo
@api.post("/ask")
def ask(body: AskIn):
    msgs = [m.model_dump() for m in body.messages]
    if ai.ai_enabled():
        try:
            return {"mode": "claude", "answer": ai.ask_velo(msgs, core.workspace_digest())}
        except ValueError as e:
            _bad(e)
        except Exception as exc:
            print(f"[groundwork] Ask Velo failed: {exc}")
            return {"mode": "error", "answer": "I could not reach Claude just now. Check your API key and connection, then ask again."}
    s = core.get_stats()
    top = core.compute_insights()[:3]
    lines = [
        "I am running in offline mode, so I can only summarize. Set ANTHROPIC_API_KEY and restart to ask me anything about your evidence.",
        "",
        f"Right now: {s['evidence']} evidence items, {s['problems']} problems, {s['initiatives']} initiatives, {s['measured']} measured.",
    ]
    if top:
        lines += ["", "What needs attention:"] + [f"- {t['title']}. {t['detail']}" for t in top]
    return {"mode": "offline", "answer": "\n".join(lines)}


# ---------------------------------------------------------------- team
@api.get("/team")
def team_overview(ctx: dict = Depends(require_user)):
    ws = ctx["ws"]
    admin = teams.rank(ws["role"]) >= teams.rank("admin")
    return {
        "workspace": {"id": ws["id"], "name": ws["name"], "role": ws["role"]},
        "members": teams.list_members(ws["id"]),
        "invites": teams.list_invites(ws["id"]) if admin else [],
        "roles": teams.ROLES,
        "you": ctx["user"]["id"],
    }


@api.patch("/team/workspace")
def rename_workspace(body: WorkspaceIn, ctx: dict = Depends(needs("admin"))):
    try:
        teams.rename_workspace(ctx["ws"]["id"], body.name)
    except ValueError as e:
        _bad(e)
    teams.audit(ctx["ws"]["id"], ctx["user"], "workspace.rename", body.name.strip())
    return {"ok": True}


@api.delete("/team/workspace")
def delete_workspace(ctx: dict = Depends(needs("owner"))):
    teams.delete_workspace(ctx["ws"]["id"])
    resp = JSONResponse({"deleted": True})
    resp.delete_cookie(auth.WS_COOKIE, path="/")
    return resp


@api.post("/team/invites")
def create_invite(body: InviteIn, request: Request, ctx: dict = Depends(needs("admin"))):
    try:
        inv = teams.create_invite(ctx["ws"]["id"], body.email, body.role, ctx["user"]["id"])
    except ValueError as e:
        _bad(e)
    link = f"{str(request.base_url).rstrip('/')}/invite/{inv.pop('token')}"
    teams.audit(ctx["ws"]["id"], ctx["user"], "member.invite", f"{inv['email']} as {inv['role']}")
    return {**inv, "link": link}


@api.delete("/team/invites/{invite_id}")
def revoke_invite(invite_id: int, ctx: dict = Depends(needs("admin"))):
    if not teams.revoke_invite(ctx["ws"]["id"], invite_id):
        raise HTTPException(404, "Invite not found")
    teams.audit(ctx["ws"]["id"], ctx["user"], "invite.revoke", f"invite #{invite_id}")
    return {"deleted": invite_id}


@api.patch("/team/members/{user_id}")
def change_role(user_id: int, body: RolePatch, ctx: dict = Depends(needs("admin"))):
    before = teams.get_member(ctx["ws"]["id"], user_id)
    try:
        teams.set_role(ctx["ws"]["id"], user_id, body.role, ctx["ws"]["role"])
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        _bad(e)
    teams.audit(ctx["ws"]["id"], ctx["user"], "member.role", f"{before['email']}: {before['role']} -> {body.role}")
    return {"ok": True}


@api.delete("/team/members/{user_id}")
def remove_member(user_id: int, ctx: dict = Depends(needs("admin"))):
    before = teams.get_member(ctx["ws"]["id"], user_id)
    try:
        teams.remove_member(ctx["ws"]["id"], user_id, ctx["ws"]["role"])
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        _bad(e)
    teams.audit(ctx["ws"]["id"], ctx["user"], "member.remove", before["email"])
    return {"deleted": user_id}


@api.post("/team/token/rotate")
def rotate_token(ctx: dict = Depends(require_user)):
    token = teams.rotate_token(ctx["ws"]["id"], ctx["user"]["id"])
    teams.audit(ctx["ws"]["id"], ctx["user"], "token.rotate", "")
    return {"api_token": token}


@api.get("/team/audit")
def team_audit(ctx: dict = Depends(needs("admin"))):
    return teams.list_audit(ctx["ws"]["id"])


@api.get("/team/export")
def team_export(ctx: dict = Depends(needs("admin"))):
    """Everything the team entered, as a download. Credentials for integrations are not included."""
    data = core.export_workspace()
    teams.audit(ctx["ws"]["id"], ctx["user"], "workspace.export", "downloaded a full export")
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="groundwork-workspace-{ctx["ws"]["id"]}.json"'})


# ---------------------------------------------------------------- integrations
def _integration_call(fn, *args):
    try:
        return fn(*args)
    except IntegrationError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


def _with_urls(state: dict, request: Request) -> dict:
    if state.get("connected") and state.get("webhook_token"):
        state["webhook_url"] = f"{str(request.base_url).rstrip('/')}/api/webhooks/{state['id']}/{state.pop('webhook_token')}"
    state.pop("webhook_token", None)
    return state


@api.get("/integrations")
def list_integrations(request: Request):
    return [_with_urls(s, request) for s in integrations.catalog_state()]


@api.post("/integrations/{provider}/connect")
def connect_integration(provider: str, request: Request, body: dict = Body(...), ctx: dict = Depends(needs("admin"))):
    st = _integration_call(integrations.connect, provider, body, ctx["user"]["id"], ctx["ws"]["id"])
    teams.audit(ctx["ws"]["id"], ctx["user"], "integration.connect", provider)
    return _with_urls(st, request)


@api.patch("/integrations/{provider}")
def update_integration(provider: str, body: SettingsIn, request: Request, ctx: dict = Depends(needs("admin"))):
    return _with_urls(_integration_call(integrations.update_settings, provider, body.target_id, body.webhook_secret), request)


@api.post("/integrations/{provider}/test")
def test_integration(provider: str, request: Request, ctx: dict = Depends(needs("admin"))):
    return _with_urls(_integration_call(integrations.test, provider), request)


@api.delete("/integrations/{provider}")
def disconnect_integration(provider: str, ctx: dict = Depends(needs("admin"))):
    integrations.disconnect(provider)
    teams.audit(ctx["ws"]["id"], ctx["user"], "integration.disconnect", provider)
    return {"disconnected": provider}


@api.post("/integrations/{provider}/sync")
def sync_integration(provider: str):
    return _integration_call(integrations.sync, provider)


@api.post("/integrations/{provider}/import")
def import_integration(provider: str, background: BackgroundTasks):
    result = _integration_call(integrations.import_now, provider)
    background.add_task(analysis.auto_analyze, db.current_schema())
    return result


@api.get("/integrations/{provider}/log")
def integration_log(provider: str):
    return integrations.recent_log(provider)


@api.post("/initiatives/{iid}/push/{provider}")
def push_initiative(iid: int, provider: str, ctx: dict = Depends(require_user)):
    result = _integration_call(integrations.push, provider, iid)
    teams.audit(ctx["ws"]["id"], ctx["user"], "integration.push", f"initiative #{iid} to {provider}")
    return result


@api.get("/initiatives/{iid}/links")
def initiative_links(iid: int):
    return integrations.links_for(iid)


@app.post("/api/cron/run")
async def cron_run(request: Request):
    """For an external scheduler (Render cron, GitHub Actions, cron-job.org). Off unless GROUNDWORK_CRON_TOKEN is set."""
    expected = os.environ.get("GROUNDWORK_CRON_TOKEN", "")
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if len(expected) < 16 or not hmac.compare_digest(given, expected):
        raise HTTPException(404, "Not found")      # 404 rather than 401: do not advertise that the endpoint exists
    return await run_in_threadpool(integrations.run_scheduled)


@app.post("/api/webhooks/{provider}/{token}")
async def webhook(provider: str, token: str, request: Request):
    """Called by the other tool, not by a signed-in user. The secret URL token picks the workspace and the signature proves the sender."""
    body = await request.body()
    try:
        return await run_in_threadpool(integrations.handle_webhook, provider, token, body, {k.lower(): v for k, v in request.headers.items()})
    except LookupError:
        raise HTTPException(404, "Unknown webhook")
    except PermissionError as e:
        raise HTTPException(401, str(e))


# ---------------------------------------------------------------- demo data
@api.post("/seed")
def seed():
    if core.list_evidence():
        raise HTTPException(400, "Workspace already has evidence. Reset it first to load the demo.")
    for source, text in SEED_EVIDENCE:
        core.add_evidence(text, source)
    if not core.get_product()["overview"]:
        core.save_product(SEED_PRODUCT["name"], SEED_PRODUCT["overview"], SEED_PRODUCT["canvas"])
    return {"added": len(SEED_EVIDENCE)}


@api.post("/reset")
def reset(ctx: dict = Depends(needs("admin"))):
    teams.audit(ctx["ws"]["id"], ctx["user"], "workspace.reset", "all evidence, problems and initiatives deleted")
    core.reset_workspace()
    return {"reset": True}


app.include_router(public)
app.include_router(api)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
