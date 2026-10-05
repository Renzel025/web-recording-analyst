"""Pull still frames out of a recording so the assistant can see what happened.

Claude can't take a video file, but it reads images. Each frame is saved with its
timestamp, so a reply can say "at 0:42 ..." and the page can jump the video there.
"""

import json
import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from app import config

log = logging.getLogger(__name__)

MAX_FRAMES = 40  # enough to follow a 2-5 minute run without flooding the context
MIN_INTERVAL = 2.0  # seconds; short videos still get at most one frame per 2s
FRAME_WIDTH = 960


def frames_dir(slug: str) -> Path:
    return config.DATA_DIR / "frames" / slug


def video_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def extract(video_path: Path, slug: str) -> Tuple[int, float, float]:
    """-> (frame count, seconds between frames, video length). Raises on failure."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    length = video_seconds(video_path)
    interval = max(MIN_INTERVAL, math.ceil(length / MAX_FRAMES))
    out = frames_dir(slug)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Seek-per-frame instead of the fps filter: each frame is taken at an exact,
    # known time, so the timestamp shown to the model is the real one.
    times = [t * interval for t in range(int(length // interval) + 1) if t * interval < length - 0.2]
    for i, t in enumerate(times, 1):
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "5",
                str(out / f"f_{i:04d}_{int(t * 1000):08d}.jpg"),
            ],
            check=True, timeout=120,
        )
    paths = list(out.glob("f_*.jpg"))
    if not paths:
        raise RuntimeError("ffmpeg produced no frames")
    try:
        make_sheets(slug)
    except Exception:
        log.exception("contact sheets failed for %s (the API path doesn't need them)", slug)
    return len(paths), float(interval), length


SHEET_COLS, SHEET_ROWS = 3, 2


def make_sheets(slug: str) -> None:
    """Tile the frames into 3x2 contact sheets (sheet_01.jpg, ...) for the `claude -p` path,
    where reading 6 sheets is much faster than reading 37 single images."""
    out = frames_dir(slug)
    for old in out.glob("sheet_*.jpg"):
        old.unlink()
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y", "-pattern_type", "glob", "-i", str(out / "f_*.jpg"),
            "-vf", f"scale=640:-2,tile={SHEET_COLS}x{SHEET_ROWS}:padding=6:margin=6", "-q:v", "4",
            str(out / "sheet_%02d.jpg"),
        ],
        check=True, timeout=300,
    )


def list_sheets(slug: str) -> List[Tuple[Path, List[float]]]:
    """-> [(sheet path, [timestamps of its tiles, left-to-right then top-to-bottom])]."""
    if not list(frames_dir(slug).glob("sheet_*.jpg")):
        make_sheets(slug)
    times = [t for t, _ in list_frames(slug)]
    per = SHEET_COLS * SHEET_ROWS
    sheets = sorted(frames_dir(slug).glob("sheet_*.jpg"))
    return [(p, times[i * per:(i + 1) * per]) for i, p in enumerate(sheets)]


def list_frames(slug: str) -> List[Tuple[float, Path]]:
    """-> [(timestamp seconds, path)] in order. The timestamp is in the file name."""
    out = []
    for p in sorted(frames_dir(slug).glob("f_*.jpg")):
        out.append((int(p.stem.split("_")[2]) / 1000.0, p))
    return out


def mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"
