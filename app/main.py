"""Groundwork web API and UI host. Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import time

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, auth, core
from .seed_data import SEED_EVIDENCE, SEED_PRODUCT

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Groundwork")
auth.init_auth()


async def require_user(request: Request) -> dict:
    """Every workspace route runs as a logged-in user, against that user's own database."""
    user = auth.user_by_session(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(401, "Please log in to continue.")
    core.use_db(auth.workspace_path(user["id"]))
    return user


api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
public = APIRouter(prefix="/api/auth")


# ---------------------------------------------------------------- models
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


def _logged_in(request: Request) -> bool:
    return auth.user_by_session(request.cookies.get(auth.COOKIE)) is not None


@app.get("/")
def landing():
    return _page("landing.html")


@app.get("/login")
def login_page(request: Request):
    return RedirectResponse("/app") if _logged_in(request) else _page("login.html")


@app.get("/signup")
def signup_page(request: Request):
    return RedirectResponse("/app") if _logged_in(request) else _page("signup.html")


@app.get("/app")
def app_page(request: Request):
    return _page("app.html") if _logged_in(request) else RedirectResponse("/login")


# ---------------------------------------------------------------- accounts
_failures: dict[str, list[float]] = {}
MAX_FAILURES, WINDOW = 8, 600


def _throttled(key: str) -> bool:
    now = time.time()
    hits = [t for t in _failures.get(key, []) if now - t < WINDOW]
    _failures[key] = hits
    return len(hits) >= MAX_FAILURES


def _start_session(user: dict) -> JSONResponse:
    resp = JSONResponse({"user": {k: user[k] for k in ("id", "email", "name")}})
    resp.set_cookie(
        auth.COOKIE, auth.create_session(user["id"]), max_age=auth.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=auth.SECURE_COOKIES, path="/",
    )
    return resp


@public.post("/signup")
def signup(body: SignupIn, request: Request):
    key = f"signup:{request.client.host if request.client else '?'}"
    if _throttled(key):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    _failures.setdefault(key, []).append(time.time())
    try:
        user = auth.create_user(body.email, body.password, body.name)
    except ValueError as e:
        _bad(e)
    core.use_db(auth.workspace_path(user["id"]))
    return _start_session(user)


@public.post("/login")
def login(body: LoginIn, request: Request):
    key = f"login:{request.client.host if request.client else '?'}:{body.email.strip().lower()}"
    if _throttled(key):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    user = auth.authenticate(body.email, body.password)
    if not user:
        _failures.setdefault(key, []).append(time.time())
        raise HTTPException(401, "That email and password do not match.")
    _failures.pop(key, None)
    return _start_session(user)


@public.post("/logout")
def logout(request: Request):
    auth.delete_session(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@public.get("/me")
def me(request: Request):
    """Always 200 so public pages can check for a session without a console error."""
    return {"user": auth.user_by_session(request.cookies.get(auth.COOKIE))}


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
def post_evidence(body: EvidenceIn):
    try:
        return core.add_evidence(body.text, body.source)
    except ValueError as e:
        _bad(e)


@api.delete("/evidence/{eid}")
def remove_evidence(eid: int):
    if not core.delete_evidence(eid):
        raise HTTPException(404, "Evidence not found")
    return {"deleted": eid}


@api.post("/evidence/upload")
async def upload_evidence(file: UploadFile = File(...)):
    """CSV with a 'text' column (optional 'source'), or a plain text file split on blank lines."""
    raw = (await file.read()).decode("utf-8", errors="replace")
    added = 0
    if (file.filename or "").lower().endswith(".csv"):
        reader = csv.DictReader(io.StringIO(raw))
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        text_col = cols.get("text") or cols.get("feedback") or cols.get("message") or cols.get("body")
        if not text_col:
            raise HTTPException(400, "CSV needs a column named text, feedback, message, or body")
        src_col = cols.get("source")
        for r in reader:
            if (r.get(text_col) or "").strip():
                core.add_evidence(r[text_col], (r.get(src_col) if src_col else "") or file.filename or "csv")
                added += 1
    else:
        for chunk in [c for c in raw.split("\n\n") if c.strip()]:
            core.add_evidence(chunk, file.filename or "upload")
            added += 1
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
    return {"mode": mode, "problems_created": len(ids), "evidence_analyzed": len(items)}


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
        t = core.update_theme(tid, body.model_dump(exclude_none=True))
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
def reset():
    core.reset_workspace()
    return {"reset": True}


app.include_router(public)
app.include_router(api)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
