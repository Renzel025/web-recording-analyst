"""Upload a finished run's video to the Recording Analyst and print the link.

This is the same call test-automation makes when a run ends, so it doubles as a
manual test and as the reference for wiring it in:

    python scripts/upload_recording.py path/to/run_screen.mp4 \\
        --results path/to/last_run_results.json \\
        --title "Web/PC automatic testing for provider: JILI (Fortune Gems)" \\
        --report-file report.txt

Reads RECORDING_ANALYST_URL (default http://127.0.0.1:8000) and INGEST_API_TOKEN
from the environment or this repo's .env.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def upload(video: Path, title: str = "", report: str = "", results: dict = None, variant: str = "") -> dict:
    base = os.environ.get("RECORDING_ANALYST_URL", "http://127.0.0.1:8000").rstrip("/")
    results = results or {}
    with video.open("rb") as f:
        resp = requests.post(
            f"{base}/api/recordings",
            headers={"X-Auth-Token": os.environ.get("INGEST_API_TOKEN", "")},
            data={
                "title": title,
                "report": report,
                "summary": results.get("summary", ""),
                "facts": json.dumps(results.get("facts", {})),
                "duration_seconds": results.get("duration_seconds") or "",
                "variant": variant,
            },
            files={"video": (video.name, f, "video/mp4")},
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--results", type=Path, help="last_run_results.json from test-automation")
    ap.add_argument("--title", default="")
    ap.add_argument("--report", default="", help="the card text")
    ap.add_argument("--report-file", type=Path, help="read the card text from a file")
    ap.add_argument("--variant", default="desktop")
    args = ap.parse_args()

    results = json.loads(args.results.read_text()) if args.results else {}
    report = args.report_file.read_text() if args.report_file else args.report
    try:
        out = upload(args.video, args.title, report, results, args.variant)
    except requests.RequestException as exc:
        sys.exit(f"upload failed: {exc}")
    print(out["url"])


if __name__ == "__main__":
    main()
