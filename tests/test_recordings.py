import importlib
import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")

TOKEN = "test-token"


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("v") / "run.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=9",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


PASSWORD = "team-secret"


@pytest.fixture()
def client(request, tmp_path, monkeypatch):
    # Logged in by default; parametrize indirectly with "logged_out" or "no_password".
    mode = getattr(request, "param", "logged_in")
    monkeypatch.setenv("SITE_PASSWORD", "" if mode == "no_password" else PASSWORD)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "videos"))
    monkeypatch.setenv("INGEST_API_TOKEN", TOKEN)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://mac.local:8000")
    # Empty rather than unset: load_dotenv() would otherwise fill them back in from .env.
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GROQ_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CLAUDE_BIN", "no-such-claude-binary")  # keep tests off the real CLI
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "no-ant-profile"))  # and off your ant login
    import app.config, app.db, app.storage, app.frames, app.assistant, app.main  # noqa: E401

    for mod in (app.config, app.db, app.storage, app.frames, app.assistant, app.main):
        importlib.reload(mod)
    app.storage.get_storage.cache_clear()
    from fastapi.testclient import TestClient

    with TestClient(app.main.app) as c:
        if mode == "logged_in":
            assert c.post("/login", data={"password": PASSWORD}).status_code == 200
        yield c


REPORT = "Duration: 1m 40s\nEnter Casino Plus ✅\nEnter provider game ✅\nPlace bet ❌\nBet history ❌ no record\n\nThe bet tap never registered."


def _upload(client, video, token=TOKEN, **data):
    with video.open("rb") as f:
        return client.post(
            "/api/recordings",
            headers={"X-Auth-Token": token},
            data={"title": "Web/PC automatic testing for provider: JILI", "report": REPORT,
                  "facts": json.dumps({"bet_placed": False}), **data},
            files={"video": ("run.mp4", f, "video/mp4")},
        )


def test_upload_returns_link_and_extracts_frames(client, video):
    r = _upload(client, video)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == f"http://mac.local:8000/r/{body['id']}"

    # BackgroundTasks run before TestClient returns, so frames are already cut.
    info = client.get(f"/api/recordings/{body['id']}").json()
    assert info["status"] == "fail"  # ❌ in the report, same rule as the card colour
    assert info["frames_status"] == "ready"
    assert info["frame_count"] == 5  # 9s video, one frame every 2s: 0,2,4,6,8

    from app.frames import list_frames

    times = [t for t, _ in list_frames(body["id"])]
    assert times == [0.0, 2.0, 4.0, 6.0, 8.0]

    page = client.get(f"/r/{body['id']}")
    assert page.status_code == 200 and "<video" in page.text
    assert client.get(info["video_url"]).status_code == 200
    assert body["id"] in client.get("/").text


def test_upload_needs_token(client, video):
    assert _upload(client, video, token="wrong").status_code == 401


def test_context_sent_to_claude_has_report_and_timed_frames(client, video):
    slug = _upload(client, video).json()["id"]
    from app import assistant
    from app.main import _get

    blocks = assistant._context_blocks(_get(slug))
    assert "Place bet ❌" in blocks[0]["text"] and '"bet_placed": false' in blocks[0]["text"]
    labels = [b["text"] for b in blocks if b["type"] == "text"][2:]
    assert labels == ["[0:00]", "[0:02]", "[0:04]", "[0:06]", "[0:08]"]
    assert sum(b["type"] == "image" for b in blocks) == 5
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


def test_chat_without_provider_says_so(client, video):
    slug = _upload(client, video).json()["id"]
    r = client.post(f"/api/recordings/{slug}/chat", json={"message": "what happened?"})
    assert r.status_code == 503 and "no LLM provider" in r.json()["detail"]


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_login_locks_pages_videos_and_chat(client, video):
    body = _upload(client, video).json()  # uploads only need the token
    slug = body["id"]
    for path in ("/", f"/r/{slug}"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert client.get(f"/api/recordings/{slug}").status_code == 401
    assert client.get(f"/media/recordings/{slug}.mp4").status_code == 401
    assert client.post(f"/api/recordings/{slug}/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/healthz").status_code == 200

    assert client.post("/login", data={"password": "wrong"}).status_code == 401
    r = client.post("/login", data={"password": PASSWORD, "next": f"/r/{slug}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/r/{slug}"
    assert client.get(f"/r/{slug}").status_code == 200
    assert client.get(f"/media/recordings/{slug}.mp4").status_code == 200

    client.get("/logout")
    assert client.get(f"/api/recordings/{slug}").status_code == 401


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_login_never_redirects_off_site(client):
    for target in ("//evil.com", "/\\evil.com", "https://evil.com"):
        r = client.post("/login", data={"password": PASSWORD, "next": target}, follow_redirects=False)
        assert r.headers["location"] == "/"


@pytest.mark.parametrize("client", ["no_password"], indirect=True)
def test_no_password_keeps_everything_locked(client, video):
    slug = _upload(client, video).json()["id"]  # automation keeps working
    for path in ("/", f"/r/{slug}", f"/api/recordings/{slug}", f"/media/recordings/{slug}.mp4"):
        assert client.get(path, follow_redirects=False).status_code == 503
    assert client.post("/login", data={"password": ""}).status_code == 401
