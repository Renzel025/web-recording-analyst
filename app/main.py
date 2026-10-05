import json
import logging
import secrets
import shutil
from datetime import timezone
from typing import Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
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
    return templates.TemplateResponse(request, "recording.html", {"rec": _get(slug)})


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
