import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: Path) -> Path:
    p = Path(_env(name, str(default)))
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


DATA_DIR = _path("DATA_DIR", BASE_DIR / "data")
DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'recorder.db'}")

STORAGE_BACKEND = _env("STORAGE_BACKEND", "local")
LOCAL_STORAGE_DIR = _path("LOCAL_STORAGE_DIR", DATA_DIR / "videos")
OSS_ENDPOINT = _env("OSS_ENDPOINT")
OSS_BUCKET = _env("OSS_BUCKET")
OSS_ACCESS_KEY_ID = _env("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = _env("OSS_ACCESS_KEY_SECRET")
OSS_URL_EXPIRES = int(_env("OSS_URL_EXPIRES", "3600"))

# The address teammates open from Lark. On a MacBook this is its LAN address,
# e.g. http://192.168.1.20:8000 (localhost only works on the Mac itself).
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# Required by POST /api/recordings (header X-Auth-Token). Empty = uploads refused.
INGEST_API_TOKEN = _env("INGEST_API_TOKEN")

DISPLAY_TZ = _env("DISPLAY_TZ", "Asia/Manila")

ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-opus-5-5")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")
# Model for the `claude -p` path (your Claude subscription).
CLAUDE_CLI_MODEL = _env("CLAUDE_CLI_MODEL", ANTHROPIC_MODEL)
