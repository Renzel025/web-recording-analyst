# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI app that receives screen recordings from a separate test-automation project (Casino Plus provider tests), stores them, and serves a page per run (`/r/<slug>`) with the video plus an AI assistant that answers questions about that run from frames cut out of the video. The link is posted to Lark. No build step, no linter configured.

## Commands

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload --port 8000     # dev server
.venv/bin/python -m pytest -q                                       # all tests (need ffmpeg, else skipped)
.venv/bin/python -m pytest tests/test_recordings.py::test_upload_needs_token -q   # single test
.venv/bin/python scripts/upload_recording.py video.mp4 --results last_run_results.json --title "..."  # manual upload, prints link
```

ffmpeg and ffprobe must be on PATH (frame extraction and tests). Production runs on port 8020 behind nginx via systemd; see `DEPLOY.md` (commands) and `SERVER_SETUP.md` (explained walkthrough).

## Architecture

**Upload → background frames → chat.** `POST /api/recordings` (`app/main.py`, `X-Auth-Token` must equal `INGEST_API_TOKEN`; empty token = uploads disabled with 503) writes the mp4 locally, inserts a `Recording` row with `frames_status="pending"`, returns `{id, url}` immediately, then runs `_extract_frames` as a FastAPI `BackgroundTask`. A local copy is always kept even with `STORAGE_BACKEND=oss`, because frames are cut from it. Status is derived from the report text: any ❌ → fail, ⚠️ → warn, else pass.

**Access control.** An HTTP middleware in `app/main.py` requires a login cookie (an HMAC of `SITE_PASSWORD`) on everything, including the `/media` video mount. The only exceptions are `/login`, `/healthz`, `/static/` and `POST /api/recordings`, which checks its own token. It fails closed: if `SITE_PASSWORD` is empty, everything except those routes answers 503. The test `client` fixture logs in by default; parametrize it indirectly with `"logged_out"` or `"no_password"` to test the lock. New routes are protected automatically. Add a route to `_is_open` only if it must work without a browser session.

**Frames (`app/frames.py`).** ≤40 frames, ≥2 s apart, each taken by a separate ffmpeg seek so timestamps are exact. The timestamp is encoded in the filename (`f_<index>_<ms>.jpg`) and `list_frames` parses it back. There is no DB table for frames: the filesystem under `data/frames/<slug>/` is the source of truth. Frames are also tiled into 3×2 `sheet_*.jpg` contact sheets for the CLI path.

**Assistant provider chain (`app/assistant.py`), tried in order:**
1. Anthropic SDK: only if `ANTHROPIC_API_KEY` or an `ant auth login` profile exists. Sends every frame as an image block in a fixed first user turn with `cache_control` on the last block, so follow-ups hit the prompt cache; keep that context byte-identical across questions. Disabled for the process lifetime after the first `AuthenticationError`.
2. `claude -p` CLI subprocess: run with `cwd` = the frames dir, only the `Read` tool allowed, reading the contact sheets. A `sk-ant-oat…` `ANTHROPIC_AUTH_TOKEN` (from `claude setup-token`) is **popped from `os.environ` at import time** so the SDK doesn't pick it up (it 429s from outside Claude Code), and is passed to the CLI as `CLAUDE_CODE_OAUTH_TOKEN`. This is the path actually used on the Mac and on the server. Timeout 240 s; nginx `proxy_read_timeout` is 300 s to match.
3. Groq: text-only, gets the report but no frames.

The reply convention `[m:ss]` is required by `SYSTEM_PROMPT` and parsed in `app/static/assistant.js` into seek buttons on the `<video>`. Changing one side means changing the other. Chat history lives only in the browser and is sent with each request (trimmed server-side to 8 turns, starting with a user turn).

**Config.** `app/config.py` loads `.env` at import and computes module-level constants, and `assistant.py` builds clients at import. That's why the test fixture sets env vars and then `importlib.reload`s `config, db, storage, frames, assistant, main` in order, and clears `get_storage`'s `lru_cache`. Any new module that reads config at import must be added to that reload list. Tests set keys to empty strings rather than unsetting them, because `load_dotenv` would otherwise refill them from the real `.env`. They also point `CLAUDE_BIN` and `ANTHROPIC_CONFIG_DIR` at nonexistent paths to stay off real credentials.

**Other conventions.**
- Datetimes are naive UTC, converted for display by the Jinja `local` filter (`DISPLAY_TZ`).
- Static files go through the `asset()` template global, which appends an mtime `?v=` for cache busting.
- `zoneinfo` has a fallback because a staging server runs Python 3.8. Avoid 3.9+-only syntax (e.g. `list[str]`, `X | Y`); the code uses `typing.List`/`Optional`.
- Schema is created with `create_all` and there are no migrations. Adding a column to `Recording` will not alter an existing `data/recorder.db`.
- The frontend (`assistant.js`, `robot.js`) is ported from a sibling project, `ai-avatar-app`: vanilla JS, three.js from cdnjs, and the browser Web Speech APIs for voice in and out.

## Working rules

The repo ships a `grounded-work` skill (`.claude/skills/grounded-work/SKILL.md`): verify claims against the files before stating them (label them VERIFIED/INFERRED/UNKNOWN), and propose changes before editing anything the user didn't ask to change.
