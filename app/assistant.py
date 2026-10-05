"""The assistant beside each recording, ported from ai-avatar-app's /api/chat.

Same chain as the avatar: Claude answers, Groq catches it if Claude can't. Instead
of retrieving profile chunks, Claude gets this one run: frames from its video, each
with a timestamp, plus the report the automation itself produced. It answers only
from those.
"""

import base64
import json
import logging
import os
import shutil
from pathlib import Path
import subprocess
from typing import Any, Dict, List

from app import config
from app.frames import frames_dir, list_frames, list_sheets, mmss
from app.models import Recording

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000
MAX_HISTORY_TURNS = 8
MAX_REPLY_TOKENS = 4000


def _make_client(label, build):
    # A missing key should only cost us that one provider, not the whole app.
    try:
        return build()
    except Exception as exc:
        log.warning("[LLM] %s unavailable: %s", label, exc)
        return None


# ANTHROPIC_API_KEY (Console key) or ANTHROPIC_AUTH_TOKEN (`claude setup-token`);
# the OAuth path needs its own beta header and the key path must not send it.
# A `claude setup-token` token (sk-ant-oat...) is only accepted from Claude Code, so it is
# kept away from the SDK (which would otherwise pick it up first and get a 429) and handed
# to the `claude` CLI path instead.
SETUP_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
if SETUP_TOKEN.startswith("sk-ant-oat"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN")
else:
    SETUP_TOKEN = ""

# With no API key, a bare Anthropic() uses the `ant auth login` profile
# (~/.config/anthropic), the same credential lookup the SDK docs describe.
USING_PROFILE = not os.environ.get("ANTHROPIC_API_KEY")


def _anthropic():
    import anthropic

    return anthropic.Anthropic()  # API key, else ANTHROPIC_AUTH_TOKEN, else `ant auth login` profile


def _groq():
    from groq import Groq

    return Groq()


# The direct API needs a Console API key. A `claude setup-token` subscription token is
# only accepted from Claude Code itself, so that case goes through the `claude` CLI below.
def _has_api_credential() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config_dir = Path(os.environ.get("ANTHROPIC_CONFIG_DIR") or Path.home() / ".config" / "anthropic")
    return any((config_dir / "credentials").glob("*.json"))


anthropic_client = _make_client("Anthropic", _anthropic) if _has_api_credential() else None
log.info("[LLM] Claude API auth: %s", "none" if anthropic_client is None else
         "API key" if os.environ.get("ANTHROPIC_API_KEY") else "ant auth login profile / auth token")
CLAUDE_BIN = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
groq_client = _make_client("Groq", _groq) if os.environ.get("GROQ_API_KEY") else None

# Flipped on the first 401 so a deploy without a Claude credential stops retrying it.
anthropic_disabled = False

SYSTEM_PROMPT = """You are the assistant on the page for ONE recorded run of a test automation
for the Casino Plus site. The automation logs in, opens a provider's game, places the
minimum bet, and checks the bet history; the person asking is a QA or ops engineer
reviewing the recording.

You are given frames taken from the screen recording, each labelled with its time in the
video, and the report the automation produced for this run. Answer only about this run and
only from what the frames and the report show. Frames are a few seconds apart: if something
could have happened between two frames, or isn't visible, say so instead of guessing. When
the report and the frames disagree, say both. Don't invent causes the recording doesn't show.

When you point at a moment in the video, write its time in square brackets, like [0:42],
so the page can jump the video there. Keep replies short, 1-4 sentences, because they can
be read aloud. No markdown headings or tables.

If the question isn't about this recording, say you can only answer about this run."""


def _report_text(rec: Recording) -> str:
    parts = [f"Run title: {rec.title or '(none)'}", f"Result: {rec.status}"]
    if rec.variant:
        parts.append(f"Variant: {rec.variant}")
    if rec.video_seconds:
        parts.append(f"Video length: {mmss(rec.video_seconds)}")
    if rec.report_text:
        parts.append("Report posted to Lark:\n" + rec.report_text)
    if rec.facts:
        parts.append("Facts recorded by the automation (JSON):\n" + json.dumps(rec.facts, indent=1, default=str))
    return "\n\n".join(parts)


def _context_blocks(rec: Recording) -> List[Dict[str, Any]]:
    """The run itself as the first user turn. Byte-identical on every question about
    this run, so it is cached and follow-ups only pay for the new turn."""
    blocks: List[Dict[str, Any]] = [{"type": "text", "text": _report_text(rec)}]
    frames = list_frames(rec.slug)
    if frames:
        blocks.append({"type": "text", "text": f"Frames from the recording ({len(frames)}), in order:"})
    for t, path in frames:
        blocks.append({"type": "text", "text": f"[{mmss(t)}]"})
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(path.read_bytes()).decode()},
        })
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _clean_history(history) -> List[Dict[str, str]]:
    if not isinstance(history, list):
        return []
    clean = [
        {"role": t["role"], "content": t["content"][:MAX_MESSAGE_LENGTH]}
        for t in history
        if isinstance(t, dict) and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)
    ]
    clean = clean[-MAX_HISTORY_TURNS:]
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    return clean


def _ask_claude(rec: Recording, history: List[Dict[str, str]], message: str) -> str:
    betas = ["server-side-fallback-2026-07-01"]
    if USING_PROFILE:
        betas.append("oauth-2025-04-20")  # OAuth bearer tokens need it on /v1/messages
    context = [{"role": "user", "content": _context_blocks(rec)},
               {"role": "assistant", "content": "I have the recording and the report for this run."}]
    response = anthropic_client.beta.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=MAX_REPLY_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[*context, *history, {"role": "user", "content": message}],
        output_config={"effort": "medium"},
        betas=betas,
        fallbacks="default",  # on a refusal, the API retries on a suitable model
    )
    # A refusal is an HTTP 200, so check before reading content.
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    reply = "".join(b.text for b in response.content if b.type == "text").strip()
    if not reply:
        raise RuntimeError(f"empty response (stop_reason={response.stop_reason})")
    usage = response.usage
    log.info("[assistant] %s in=%s cache_read=%s out=%s", rec.slug, usage.input_tokens,
             getattr(usage, "cache_read_input_tokens", None), usage.output_tokens)
    return reply


CLI_TIMEOUT = 240


def _ask_claude_cli(rec: Recording, history: List[Dict[str, str]], message: str) -> str:
    """Ask through the real `claude` command, the way test-automation's summary does.

    Claude Code opens the contact sheets itself with its Read tool; every other tool is
    off. Uses whatever login `claude` has here, or the setup-token in .env passed on as
    CLAUDE_CODE_OAUTH_TOKEN (the variable Claude Code reads on headless machines).
    """
    sheets = list_sheets(rec.slug) if rec.frames_status == "ready" else []
    lines = [_report_text(rec), ""]
    if sheets:
        lines.append(
            "The recording is in these contact sheets in the current directory. Each sheet is a "
            "3-column x 2-row grid of frames, read left to right, top row first:"
        )
        for path, times in sheets:
            lines.append(f"- {path.name}: " + ", ".join(f"[{mmss(t)}]" for t in times))
        lines.append("Read all the sheets (in parallel) before answering.")
    else:
        lines.append("The video frames aren't available yet; answer from the report only and say so.")
    if history:
        lines += ["", "Conversation so far:"]
        lines += [f"{'User' if t['role'] == 'user' else 'You'}: {t['content']}" for t in history]
    lines += ["", f"Question: {message}"]

    env = dict(os.environ)
    if SETUP_TOKEN and not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = SETUP_TOKEN
    env.pop("ANTHROPIC_API_KEY", None)  # empty here anyway; never let a stale one override the login

    proc = subprocess.run(
        [
            CLAUDE_BIN, "-p", "\n".join(lines),
            "--output-format", "json",
            "--model", config.CLAUDE_CLI_MODEL,
            "--system-prompt", SYSTEM_PROMPT,
            "--allowedTools", "Read",
            "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "NotebookEdit", "Task",
        ],
        cwd=str(frames_dir(rec.slug)) if sheets else None,
        env=env, capture_output=True, text=True, timeout=CLI_TIMEOUT,
    )
    try:
        out = json.loads(proc.stdout or "{}")
    except ValueError:
        out = {}
    reply = (out.get("result") or "").strip()
    if proc.returncode != 0 or out.get("is_error") or not reply:
        detail = reply or (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {detail}")
    log.info("[assistant] %s via claude CLI in %.1fs, cost=%s", rec.slug,
             (out.get("duration_ms") or 0) / 1000, out.get("total_cost_usd"))
    return reply


def _ask_groq(rec: Recording, history: List[Dict[str, str]], message: str) -> str:
    """Groq's model can't see images, so it only gets the report, and says so."""
    system = (
        SYSTEM_PROMPT
        + "\n\nYou can NOT see the video right now, only the report below. If the question needs the"
        " video, say that the video view is unavailable at the moment.\n\n"
        + _report_text(rec)
    )
    completion = groq_client.chat.completions.create(
        model=config.GROQ_MODEL,
        max_tokens=600,
        messages=[{"role": "system", "content": system}, *history, {"role": "user", "content": message}],
    )
    return (completion.choices[0].message.content or "").strip()


def chat(rec: Recording, message: str, history=None) -> Dict[str, Any]:
    global anthropic_disabled
    clean = _clean_history(history)

    claude_error = None
    if anthropic_client is not None and not anthropic_disabled:
        import anthropic

        try:
            return {"reply": _ask_claude(rec, clean, message), "provider": config.ANTHROPIC_MODEL}
        except anthropic.AuthenticationError:
            anthropic_disabled = True
            claude_error = "the Claude credential was rejected"
            log.warning("[LLM] Claude credential rejected; using Groq from now on")
        except anthropic.RateLimitError:
            claude_error = "Claude is rate limiting requests; try again shortly"
            log.warning("[LLM] Claude 429")
        except Exception as exc:
            claude_error = f"Claude failed: {exc}"
            log.warning("[LLM] Claude failed (%s); trying the next provider", exc)

    if CLAUDE_BIN:
        try:
            return {"reply": _ask_claude_cli(rec, clean, message), "provider": f"claude CLI ({config.CLAUDE_CLI_MODEL})"}
        except subprocess.TimeoutExpired:
            claude_error = f"Claude Code took longer than {CLI_TIMEOUT}s"
            log.warning("[LLM] %s", claude_error)
        except Exception as exc:
            claude_error = str(exc)
            log.warning("[LLM] %s", exc)

    if groq_client is not None:
        return {"reply": _ask_groq(rec, clean, message), "provider": config.GROQ_MODEL}

    if claude_error:
        raise RuntimeError(claude_error)
    raise RuntimeError("no LLM provider: install/log in to the `claude` CLI, or set ANTHROPIC_API_KEY or GROQ_API_KEY")
