import importlib
import json
import shutil
import subprocess
from urllib.parse import parse_qs, urlparse

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


TENANT = "t_our_company"
ALICE = {"open_id": "ou_alice", "name": "Alice", "tenant_key": TENANT}


@pytest.fixture()
def client(request, tmp_path, monkeypatch):
    # Logged in by default; parametrize indirectly with "logged_out", "no_lark" or "no_tenant".
    mode = getattr(request, "param", "logged_in")
    monkeypatch.setenv("LARK_APP_ID", "" if mode == "no_lark" else "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "" if mode == "no_lark" else "lark-secret")
    monkeypatch.setenv("LARK_TENANT_KEY", "" if mode == "no_tenant" else TENANT)
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

    # Stand in for Lark: whatever code comes back belongs to the user in c.lark_user.
    monkeypatch.setattr(app.main, "_lark_user", lambda code: c.lark_user)
    with TestClient(app.main.app) as c:
        c.lark_user = ALICE
        if mode == "logged_in":
            assert _lark_login(c).status_code == 303
        yield c


def _lark_login(client, next="/"):
    """Click "Log in with Lark", then come back from Lark with a code."""
    r = client.get("/auth/lark", params={"next": next}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get("/auth/lark/callback", params={"code": "c0de", "state": state}, follow_redirects=False)


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
    slug = _upload(client, video).json()["id"]  # uploads only need the token
    for path in ("/", f"/r/{slug}"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/login?next={path}"
    assert client.get(f"/api/recordings/{slug}").status_code == 401
    assert client.get(f"/media/recordings/{slug}.mp4").status_code == 401
    assert client.post(f"/api/recordings/{slug}/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/healthz").status_code == 200
    assert "Log in with Lark" in client.get("/login").text

    r = _lark_login(client, next=f"/r/{slug}")
    assert r.status_code == 303 and r.headers["location"] == f"/r/{slug}"
    page = client.get(f"/r/{slug}")
    assert page.status_code == 200 and "Alice" in page.text
    assert client.get(f"/media/recordings/{slug}.mp4").status_code == 200

    client.get("/logout")
    assert client.get(f"/api/recordings/{slug}").status_code == 401


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_lark_authorize_link_goes_to_lark_with_our_app(client):
    r = client.get("/auth/lark", follow_redirects=False)
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.larksuite.com"
    assert q["client_id"] == ["cli_test"]
    assert q["redirect_uri"] == ["http://mac.local:8000/auth/lark/callback"]


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_other_organisation_is_refused(client):
    client.lark_user = {"open_id": "ou_mallory", "name": "Mallory", "tenant_key": "t_someone_else"}
    r = _lark_login(client)
    assert r.status_code == 403 and "Only members of our Lark organisation" in r.text
    assert client.get("/api/recordings/x").status_code == 401


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_callback_without_our_state_is_refused(client):
    client.get("/auth/lark", follow_redirects=False)
    r = client.get("/auth/lark/callback", params={"code": "c0de", "state": "forged"}, follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/api/recordings/x").status_code == 401


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_forged_session_cookie_is_refused(client):
    client.cookies.set("ra_session", "eyJpZCI6ICJvdV94In0=.deadbeef")
    assert client.get("/api/recordings/x").status_code == 401


@pytest.mark.parametrize("client", ["logged_out"], indirect=True)
def test_login_never_redirects_off_site(client):
    for target in ("//evil.com", "/\\evil.com", "https://evil.com"):
        assert _lark_login(client, next=target).headers["location"] == "/"


@pytest.mark.parametrize("client", ["no_tenant"], indirect=True)
def test_first_login_shows_the_tenant_key_to_configure(client):
    client.cookies.clear()
    r = _lark_login(client)
    assert r.status_code == 403 and f"LARK_TENANT_KEY={TENANT}" in r.text
    assert client.get("/api/recordings/x").status_code == 401


@pytest.mark.parametrize("client", ["no_lark"], indirect=True)
def test_without_lark_app_everything_stays_locked(client, video):
    slug = _upload(client, video).json()["id"]  # automation keeps working
    for path in ("/", f"/r/{slug}", f"/api/recordings/{slug}", f"/media/recordings/{slug}.mp4"):
        assert client.get(path, follow_redirects=False).status_code == 503
