import base64
import hashlib
import hmac
import json
import logging
import secrets
import shutil
import time
from datetime import timezone
from typing import Optional
from urllib.parse import quote, urlencode

import requests

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app import assistant, config, frames
from app.db import Session, init_db
from app.models import Recording
from app.storage import get_storage, video_url

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Recording Analyst")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
if config.STORAGE_BACKEND == "local":
    config.LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=config.LOCAL_STORAGE_DIR), name="media")

try:  # zoneinfo is 3.9+; some servers (like the lark-ops staging box) still run 3.8
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(config.DISPLAY_TZ)
except ImportError:
    from datetime import timedelta

    TZ = timezone(timedelta(hours=8))  # Asia/Manila has no DST
templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
templates.env.filters["local"] = (
    lambda dt, fmt="%b %d, %H:%M": dt.replace(tzinfo=timezone.utc).astimezone(TZ).strftime(fmt) if dt else "—"
)
templates.env.filters["mmss"] = frames.mmss


def asset(path: str) -> str:
    """/static URL with the file's mtime as ?v=, so a changed file is never served from
    the browser's cache (StaticFiles sends no Cache-Control, so Chrome guesses)."""
    try:
        v = int((config.BASE_DIR / "app" / "static" / path).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{path}?v={v}"


templates.env.globals.update(video_url=video_url, DISPLAY_TZ=config.DISPLAY_TZ, asset=asset)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if not _lark_ready():
        log.warning("LARK_APP_ID / LARK_APP_SECRET not set: every page, video and chat answers 503 until they are")
    elif not config.LARK_TENANT_KEY:
        log.warning("LARK_TENANT_KEY not set: nobody can log in yet; the first Lark login shows the key to copy")


# --- Login with Lark -------------------------------------------------------------
# OAuth 2.0 against Lark. Only people whose Lark organisation (tenant_key) matches
# LARK_TENANT_KEY get in. The session cookie is a payload signed with the app secret,
# so rotating LARK_APP_SECRET logs everyone out.

SESSION_COOKIE = "ra_session"
STATE_COOKIE = "ra_lark_state"
SESSION_DAYS = 30
LARK_AUTHORIZE_URL = "https://accounts.larksuite.com/open-apis/authen/v1/authorize"
LARK_TOKEN_URL = "https://open.larksuite.com/open-apis/authen/v2/oauth/token"
LARK_USER_INFO_URL = "https://open.larksuite.com/open-apis/authen/v1/user_info"


def _lark_ready() -> bool:
    return bool(config.LARK_APP_ID and config.LARK_APP_SECRET)


def _redirect_uri() -> str:
    # Must be listed under Security Settings > Redirect URLs in the Lark developer console.
    return f"{config.PUBLIC_BASE_URL}/auth/lark/callback"


def _sign(payload: str) -> str:
    return hmac.new(config.LARK_APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _session_value(user: dict) -> str:
    data = {"id": user.get("open_id"), "name": user.get("name") or "", "exp": int(time.time()) + SESSION_DAYS * 86400}
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def _current_user(request: Request) -> Optional[dict]:
    payload, _, sig = request.cookies.get(SESSION_COOKIE, "").partition(".")
    if not (_lark_ready() and payload and secrets.compare_digest(sig.encode(), _sign(payload).encode())):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None
    return data if data.get("exp", 0) > time.time() else None


def _is_open(request: Request) -> bool:
    path = request.url.path
    if path in ("/login", "/auth/lark", "/auth/lark/callback", "/healthz") or path.startswith("/static/"):
        return True
    # test-automation has no browser session; the upload checks X-Auth-Token itself.
    return path == "/api/recordings" and request.method == "POST"


@app.middleware("http")
async def require_login(request: Request, call_next):
    # Covers the /media mount too, so videos can't be fetched without logging in.
    if _is_open(request):
        return await call_next(request)
    if not _lark_ready():
        # Fail closed: a deploy without the Lark app must not be public.
        return PlainTextResponse("Locked: set LARK_APP_ID and LARK_APP_SECRET in .env and restart.", status_code=503)
    user = _current_user(request)
    if user:
        request.state.user = user
        return await call_next(request)
    if request.url.path.startswith(("/api/", "/media/")):
        return JSONResponse({"detail": "Login required"}, status_code=401)
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


def _safe_next(target: str) -> str:
    """Only same-site paths, so ?next=//evil.com can't bounce people elsewhere."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def _lark_user(code: str) -> dict:
    """Trade the one-time code for the user's token, then ask Lark who they are."""
    tok = requests.post(LARK_TOKEN_URL, timeout=15, json={
        "grant_type": "authorization_code", "client_id": config.LARK_APP_ID,
        "client_secret": config.LARK_APP_SECRET, "code": code, "redirect_uri": _redirect_uri(),
    }).json()
    if not tok.get("access_token"):
        raise RuntimeError(f"token exchange failed: {tok.get('code')} {tok.get('error_description') or tok.get('msg')}")
    info = requests.get(LARK_USER_INFO_URL, timeout=15,
                        headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    if info.get("code") != 0:
        raise RuntimeError(f"user_info failed: {info.get('code')} {info.get('msg')}")
    return info["data"]


def _status_from_report(report: str) -> str:
    """Same rule as the Lark card's header colour: red if any ❌, orange if any ⚠️."""
    if not report:
        return "unknown"
    if "❌" in report:
        return "fail"
    if "⚠️" in report or "⚠" in report:
        return "warn"
    return "pass"


def _get(slug: str) -> Recording:
    with Session() as s:
        rec = s.scalar(select(Recording).where(Recording.slug == slug))
    if rec is None:
        raise HTTPException(404, "Recording not found")
    return rec


# --- Pages -------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def recordings_page(request: Request):
    with Session() as s:
        recs = s.scalars(select(Recording).order_by(Recording.created_at.desc()).limit(200)).all()
    return templates.TemplateResponse(request, "recordings.html", {"recs": recs})


@app.get("/r/{slug}", response_class=HTMLResponse)
def recording_page(request: Request, slug: str):
    rec = _get(slug)
    log.info("view: %s opened %s", request.state.user.get("name"), slug)
    return templates.TemplateResponse(request, "recording.html", {"rec": rec})


def _login_page(request: Request, next: str = "/", error: str = "", status: int = 200):
    return templates.TemplateResponse(
        request, "login.html", {"next": _safe_next(next), "error": error, "ready": _lark_ready()}, status_code=status,
    )


def _cookie_args(request: Request) -> dict:
    # Behind nginx uvicorn sees https via X-Forwarded-Proto; on the Mac's plain http the
    # cookie must not be Secure or the browser would drop it.
    return {"httponly": True, "samesite": "lax", "secure": request.url.scheme == "https"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if _current_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return _login_page(request, next)


@app.get("/auth/lark")
def lark_start(request: Request, next: str = "/"):
    if not _lark_ready():
        return _login_page(request, next, status=503)
    state = secrets.token_urlsafe(16)
    params = urlencode({"client_id": config.LARK_APP_ID, "redirect_uri": _redirect_uri(), "state": state})
    resp = RedirectResponse(f"{LARK_AUTHORIZE_URL}?{params}", status_code=303)
    # The state comes back on the callback; matching it proves the login started here.
    resp.set_cookie(STATE_COOKIE, f"{state}|{_safe_next(next)}", max_age=600, **_cookie_args(request))
    return resp


@app.get("/auth/lark/callback", response_class=HTMLResponse)
def lark_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    expected, _, target = request.cookies.get(STATE_COOKIE, "").partition("|")
    if not _lark_ready():
        return _login_page(request, status=503)
    if error or not code:
        return _login_page(request, target, "Lark login was cancelled.", 403)
    if not expected or not secrets.compare_digest(state.encode(), expected.encode()):
        return _login_page(request, target, "That login link expired. Please try again.", 403)
    try:
        user = _lark_user(code)
    except Exception:
        log.exception("Lark login failed")
        return _login_page(request, target, "Couldn't finish the Lark login. Please try again.", 502)

    name, tenant = user.get("name") or user.get("open_id"), user.get("tenant_key") or ""
    if not config.LARK_TENANT_KEY:
        log.warning("Lark login by %s: set LARK_TENANT_KEY=%s in .env to let this organisation in", name, tenant)
        return _login_page(request, target, f"Almost set up: add LARK_TENANT_KEY={tenant} to .env and restart.", 403)
    if not secrets.compare_digest(tenant.encode(), config.LARK_TENANT_KEY.encode()):
        log.warning("refused Lark login from another organisation: %s (tenant %s)", name, tenant)
        return _login_page(request, target, "Only members of our Lark organisation can open recordings.", 403)

    log.info("login: %s (%s)", name, user.get("open_id"))
    resp = RedirectResponse(_safe_next(target), status_code=303)
    resp.set_cookie(SESSION_COOKIE, _session_value(user), max_age=SESSION_DAYS * 86400, **_cookie_args(request))
    resp.delete_cookie(STATE_COOKIE)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# --- API ---------------------------------------------------------------------


def _extract_frames(slug: str, video_path: str) -> None:
    """Runs after the upload has been answered, so the automation isn't kept waiting."""
    from pathlib import Path

    with Session() as s:
        rec = s.scalar(select(Recording).where(Recording.slug == slug))
        try:
            count, interval, length = frames.extract(Path(video_path), slug)
            rec.frame_count, rec.frame_interval, rec.video_seconds = count, interval, length
            rec.frames_status, rec.frames_error = "ready", None
            log.info("frames for %s: %d every %.0fs (video %.0fs)", slug, count, interval, length)
        except Exception as exc:
            log.exception("frame extraction failed for %s", slug)
            rec.frames_status, rec.frames_error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        s.commit()


@app.post("/api/recordings")
def api_upload(
    background: BackgroundTasks,
    video: UploadFile = File(...),
    title: str = Form(""),
    report: str = Form(""),
    summary: str = Form(""),
    facts: str = Form(""),
    variant: str = Form(""),
    status: str = Form(""),
    duration_seconds: Optional[float] = Form(None),
    x_auth_token: str = Header(""),
):
    """Called by test-automation when a run ends. Returns the link to post in Lark.

    multipart/form-data: video (mp4), title, report (the card text), summary,
    facts (JSON from last_run_results.json), variant, optional status.
    Header: X-Auth-Token: <INGEST_API_TOKEN>.
    """
    if not config.INGEST_API_TOKEN:
        raise HTTPException(503, "Uploads are disabled: set INGEST_API_TOKEN in .env")
    if not secrets.compare_digest(x_auth_token, config.INGEST_API_TOKEN):
        raise HTTPException(401, "Bad token")
    if facts:
        try:
            json.loads(facts)
        except ValueError:
            raise HTTPException(422, "facts must be JSON")

    slug = secrets.token_urlsafe(9)
    # Always keep a local copy: frames are cut from it even when the video also goes to OSS.
    key = f"recordings/{slug}.mp4"
    local = config.LOCAL_STORAGE_DIR / key
    local.parent.mkdir(parents=True, exist_ok=True)
    with local.open("wb") as f:
        shutil.copyfileobj(video.file, f)
    if config.STORAGE_BACKEND != "local":
        key = get_storage().save(local.read_bytes(), key)

    rec = Recording(
        slug=slug,
        title=title[:255] or None,
        variant=variant[:16] or None,
        status=status if status in ("pass", "fail", "warn") else _status_from_report(report),
        report_text=report or None,
        summary=summary or None,
        facts_json=facts or None,
        duration_seconds=duration_seconds,
        video_key=key,
        frames_status="pending",
    )
    with Session() as s:
        s.add(rec)
        s.commit()
    background.add_task(_extract_frames, slug, str(local))
    return {"id": slug, "url": f"{config.PUBLIC_BASE_URL}/r/{slug}"}


@app.get("/api/recordings/{slug}")
def api_recording(slug: str):
    rec = _get(slug)
    return {
        "id": rec.slug,
        "title": rec.title,
        "status": rec.status,
        "frames_status": rec.frames_status,
        "frame_count": rec.frame_count,
        "video_url": video_url(rec.video_key),
    }


@app.post("/api/recordings/{slug}/chat")
def api_chat(slug: str, body: dict = Body(...)):
    rec = _get(slug)
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(400, "message is required")
    if len(message) > assistant.MAX_MESSAGE_LENGTH:
        raise HTTPException(400, "message is too long")
    try:
        return assistant.chat(rec, message.strip(), body.get("history"))
    except RuntimeError as exc:
        log.error("chat failed: %s", exc)
        raise HTTPException(503, str(exc))


@app.get("/healthz")
def healthz():
    return {"ok": True}
