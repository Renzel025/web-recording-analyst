# Setting up the Recording Analyst on a server

A step-by-step tutorial: what to type, and why each step matters. For just the commands,
see [DEPLOY.md](DEPLOY.md). Both files describe the same setup.

## The big picture

On the Mac, `uvicorn app.main:app` runs the app and you open `http://127.0.0.1:8000`.
A server needs three more things:

1. **It must keep running** after you log out, and come back after a crash or reboot.
   **systemd** handles that.
2. **It must be reachable from outside over HTTPS** with a real domain name. **nginx**
   receives the traffic on ports 80/443 and passes it on to the app. **certbot** provides
   the free HTTPS certificate.
3. **The AI must work without a browser login**: the app hands your Claude subscription
   token from `.env` to the `claude` CLI.

```
Internet ──https:443──▶ nginx ──http──▶ uvicorn 127.0.0.1:8020 ──▶ app (FastAPI)
                         │                                         ├─ data/        videos, frames, SQLite DB
                         │                                         └─ claude CLI   answers questions
             certbot (HTTPS cert)          systemd keeps uvicorn alive
```

Port 8020 is only reachable from inside the server. Everything from outside goes through nginx.

| Thing | Where it lives on the server |
|---|---|
| Code | `/root/web-recording-analyst/` |
| Python environment | `/root/web-recording-analyst/.venv/` |
| Settings and secrets | `/root/web-recording-analyst/.env` |
| Videos, frames, database | `/root/web-recording-analyst/data/` |
| systemd unit | `/etc/systemd/system/recording-analyst.service` |
| nginx site (Ubuntu/Debian) | `/etc/nginx/sites-available/recording-analyst` |
| nginx site (Alibaba Cloud Linux / CentOS) | `/etc/nginx/conf.d/recording-analyst.conf` |
| Claude Code CLI | `/root/.local/bin/claude` |

---

## Step 0: Before you start

You need:

- **A Linux server.** On Alibaba Cloud ECS, pick an **international region** such as
  Singapore or Hong Kong. Mainland China regions block `api.anthropic.com` and the Claude
  installer, and a public domain there needs ICP filing. `bot-dev` and
  `bot-prod` are both in Singapore.
- **SSH access as root:** `ssh root@SERVER`.
- **A domain name** you can add DNS records to, for example `recordings.mybot.ink`.
- **Your Claude subscription token**, which looks like `sk-ant-oat...`. To make one, run
  `claude setup-token` on the Mac. Or copy `ANTHROPIC_AUTH_TOKEN` from the Mac's `.env`.

**Open the firewall.** In the ECS console, go to the instance → **Security Groups** →
**Inbound** and allow TCP **80** and **443**. Do **not** open 8020.

**Point DNS at the server.** Add an `A` record for `recordings.mybot.ink` with the server's
public IP. Do this early, because certbot (step 6) only works once DNS resolves. To check:

```bash
# on the Mac
dig +short recordings.mybot.ink     # should print the server IP
```

---

## Step 1: Install system packages

The app needs Python 3.8 or newer, **ffmpeg** to cut frames out of videos, **nginx**, and
**certbot**.

**Ubuntu / Debian:**

```bash
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg nginx certbot python3-certbot-nginx
```

**Alibaba Cloud Linux 3 / CentOS:**

```bash
sudo dnf install -y python3 nginx
sudo dnf install -y epel-release && sudo dnf install -y ffmpeg
sudo dnf install -y certbot python3-certbot-nginx
```

If `dnf` can't find ffmpeg, download a static build from johnvansickle.com/ffmpeg and
put `ffmpeg` and `ffprobe` in `/usr/local/bin`.

**Check it worked:**

```bash
python3 --version            # 3.8 or newer
ffmpeg -version | head -1
ffprobe -version | head -1   # frames.py needs both
nginx -v
```

> Why ffmpeg matters: without it, uploads still work, but every recording ends up with
> `frames_status = failed`. The AI can then only read the text report, not the video.

---

## Step 2: Copy the code

The repo has no git remote yet, so copy it from the Mac with `rsync`. Leave out the local
venv, the local data, and the Mac's `.env`:

```bash
# on the Mac
rsync -av --exclude .venv --exclude data --exclude .env --exclude .playwright-mcp \
  ~/Desktop/web-recording-analyst/ root@SERVER:/root/web-recording-analyst/
```

**Check it worked:**

```bash
# on the server
ls /root/web-recording-analyst      # app/ deploy/ scripts/ requirements.txt ...
```

---

## Step 3: Python environment and `.env`

```bash
cd /root/web-recording-analyst
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
```

Fill in these values:

| Variable | What to put | Why |
|---|---|---|
| `PUBLIC_BASE_URL` | `https://recordings.mybot.ink` | The app builds the links it returns from this (`https://.../r/<id>`). If it's wrong, the links posted in Lark are wrong. |
| `INGEST_API_TOKEN` | A long random string | The upload password. test-automation must send the same value. If it's empty, all uploads are refused (HTTP 503). Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, or reuse the Mac's. |
| `SITE_PASSWORD` | A team password | **Required.** It protects every page, video and chat. Teammates type it once per browser and stay logged in for 30 days. Uploads don't use it, they use `INGEST_API_TOKEN`. If it's empty, the site stays locked and every page answers 503. Changing it logs everyone out. |
| `ANTHROPIC_AUTH_TOKEN` | `sk-ant-oat...` | Your Claude subscription token. The app passes it to the `claude` CLI. |
| `DISPLAY_TZ` | `Asia/Manila` (default) | The timezone used for dates on the pages. |
| `STORAGE_BACKEND` | `local` (default) | Videos stay on the server disk. See "Optional: Alibaba OSS" below for cloud storage. |

Leave `ANTHROPIC_API_KEY` and `GROQ_API_KEY` empty unless you actually have them.

Protect the file, since it contains secrets:

```bash
chmod 600 .env
```

**Check it worked.** Start the app by hand once, in the foreground:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8020
# in a second SSH session:
curl -s 127.0.0.1:8020/healthz        # {"ok":true}
```

Press Ctrl+C to stop it. systemd runs it from step 5 on.

---

## Step 4: Install Claude Code (the AI)

When someone asks a question, the app tries these in order:

1. **The Claude API**, if `ANTHROPIC_API_KEY` is set.
2. **The `claude` CLI**, using your subscription. This is the normal path for us.
3. **Groq**, if `GROQ_API_KEY` is set. Groq only sees the text report, not the video.

For path 2, install Claude Code on the server:

```bash
curl -fsSL https://claude.ai/install.sh | bash
~/.local/bin/claude --version
```

You don't need a browser login. The app copies `ANTHROPIC_AUTH_TOKEN` into
`CLAUDE_CODE_OAUTH_TOKEN` when it runs the CLI (`app/assistant.py`). Test the token by
hand:

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat... ~/.local/bin/claude -p "say ok"
```

It should print `ok`. If it fails with a network error, the server's region probably
can't reach Anthropic (see step 0).

> The CLI is installed under `/root/.local/bin`, which isn't on systemd's default PATH.
> The service file adds it back. Keep that line if you edit the file.

---

## Step 5: Keep the app running with systemd

The unit file is already in the repo: `deploy/recording-analyst.service.example`. It
does the following:

- Runs `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8020 --workers 1`.
  - `--host 127.0.0.1` means only nginx can reach it.
  - Port 8020 avoids clashing with lark-ops-ai on 8000.
- Loads `.env` (`EnvironmentFile=`).
- Adds `/root/.local/bin` to `PATH` so the app can find `claude`.
- `Restart=always` restarts the app 5 s after a crash. `WantedBy=multi-user.target`
  together with `enable` starts it at boot.

Install and start it:

```bash
sudo cp deploy/recording-analyst.service.example /etc/systemd/system/recording-analyst.service
sudo systemctl daemon-reload
sudo systemctl enable --now recording-analyst
```

**Check it worked:**

```bash
systemctl status recording-analyst --no-pager     # "active (running)"
curl -s 127.0.0.1:8020/healthz                    # {"ok":true}
journalctl -u recording-analyst -n 50 --no-pager  # the app's logs
```

The log should include a line like `[LLM] Claude API auth: none`. That's expected when
there's no API key: the app falls through to the `claude` CLI.

Commands you'll use often:

```bash
sudo systemctl restart recording-analyst    # after changing code or .env
sudo systemctl stop recording-analyst
journalctl -u recording-analyst -f          # follow the logs live
```

---

## Step 6: nginx and HTTPS

The repo has the site config: `deploy/nginx.conf.example`. It does the following:

- Listens on port 80 for your domain and proxies everything to `127.0.0.1:8020`.
- `client_max_body_size 200m`. nginx's default limit is 1 MB, which would reject video
  uploads with **413**.
- `proxy_read_timeout 300s`. An answer through the `claude` CLI can take up to about
  4 minutes, and nginx's default 60 s would cut it off with **504**.

**Ubuntu / Debian:**

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/recording-analyst
sudo sed -i 's/DOMAIN/recordings.mybot.ink/g' /etc/nginx/sites-available/recording-analyst
sudo ln -sf /etc/nginx/sites-available/recording-analyst /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Alibaba Cloud Linux / CentOS.** These have no `sites-available`, so use `conf.d`:

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/conf.d/recording-analyst.conf
sudo sed -i 's/DOMAIN/recordings.mybot.ink/g' /etc/nginx/conf.d/recording-analyst.conf
sudo systemctl enable --now nginx
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` checks the config before reloading. Always run it first.

**Check HTTP works:**

```bash
curl -s http://recordings.mybot.ink/healthz       # {"ok":true}
```

**Add HTTPS.** certbot gets a free Let's Encrypt certificate and edits the nginx config for you:

```bash
sudo certbot --nginx -d recordings.mybot.ink
```

If certbot asks whether to redirect HTTP to HTTPS, choose **redirect**. Without HTTPS, the
team password and the videos travel unencrypted. To confirm the redirect is on:

```bash
curl -sI http://recordings.mybot.ink/ | head -3     # 301, Location: https://...
```

The certificate renews automatically. To test renewal: `sudo certbot renew --dry-run`.

**Check it worked:** open `https://recordings.mybot.ink/` on your phone. You should see
the login page, and after the team password, the recordings list. Check that the data
is locked without a login:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://recordings.mybot.ink/api/recordings/anything   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://recordings.mybot.ink/                          # 303 (to /login)
```

---

## Step 7 (optional): Move the Mac's existing recordings over

Stop the service on the server first, so the database isn't in use:

```bash
# on the server
sudo systemctl stop recording-analyst
# on the Mac
rsync -av ~/Desktop/web-recording-analyst/data/ root@SERVER:/root/web-recording-analyst/data/
# on the server
sudo systemctl start recording-analyst
```

Recordings keep their ids, so `/r/nclnGkoLXJmY` becomes `https://recordings.mybot.ink/r/nclnGkoLXJmY`.

---

## Step 8: Test a real upload

From the Mac, upload to the server using **the server's** token:

```bash
cd ~/Desktop/web-recording-analyst
RECORDING_ANALYST_URL=https://recordings.mybot.ink INGEST_API_TOKEN=<server token> \
  .venv/bin/python scripts/upload_recording.py \
  ~/Desktop/test-automation/downloaded_files/run_screen.mp4 \
  --results ~/Desktop/test-automation/downloaded_files/last_run_results.json \
  --title "Upload test"
```

It prints the link. Open it, wait for the "Preparing the video" note to disappear, then
ask the assistant "What happened in this run?".

To check from the server side:

```bash
journalctl -u recording-analyst -n 30 --no-pager
# look for:  frames for <id>: N every Xs ...
#            [assistant] <id> via claude CLI in Ys ...
ls /root/web-recording-analyst/data/frames/<id>/
```

Finally, point test-automation at the server. Set its `RECORDING_ANALYST_URL` and
`INGEST_API_TOKEN` to the same values.

---

## Updating after code changes

```bash
# on the Mac
rsync -av --exclude .venv --exclude data --exclude .env --exclude .playwright-mcp \
  ~/Desktop/web-recording-analyst/ root@SERVER:/root/web-recording-analyst/
# on the server
cd /root/web-recording-analyst && .venv/bin/pip install -r requirements.txt
sudo systemctl restart recording-analyst
```

The `--exclude` flags matter: they keep the server's own `.env` and `data/` from being
overwritten.

---

## Optional: Alibaba OSS for videos

To store videos in an OSS bucket instead of on the server disk:

```bash
.venv/bin/pip install oss2
```

Then set these in `.env`: `STORAGE_BACKEND=oss`, `OSS_ENDPOINT`, `OSS_BUCKET`,
`OSS_ACCESS_KEY_ID` and `OSS_ACCESS_KEY_SECRET`. Restart the service.

The app still keeps a local copy of each video, because frames are cut from it. Pages
link to the video with a signed URL that expires after an hour (`OSS_URL_EXPIRES`).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Upload returns **503** "Uploads are disabled" | `INGEST_API_TOKEN` is empty in the server's `.env` | Set it, then `systemctl restart recording-analyst` |
| Upload returns **401** "Bad token" | test-automation's token doesn't match the server's | Copy the exact value from the server's `.env` |
| Upload returns **413** | nginx body limit | Make sure `client_max_body_size 200m;` is in the site config, then reload nginx |
| Assistant says "Preparing the video" forever, or frames failed | ffmpeg/ffprobe missing | `which ffmpeg ffprobe`, install them (step 1), then upload again |
| Assistant error "claude CLI failed" | Bad or expired token, or `claude` not found | Run the step 4 test by hand. Check the `PATH` line in the service file |
| Assistant error after about 60 s, or **504** | nginx timeout too short | Make sure `proxy_read_timeout 300s;` is set |
| Lark links point to `127.0.0.1` | `PUBLIC_BASE_URL` not set | Set it to `https://your-domain`, then restart |
| Site doesn't load at all | Firewall or DNS | Security group has 80/443 open? `dig` shows the right IP? `systemctl status nginx` |
| **502 Bad Gateway** | The app isn't running | `systemctl status recording-analyst`, then `journalctl -u recording-analyst -n 50` |
| certbot fails | DNS doesn't point at the server yet, or port 80 is closed | Fix DNS or the firewall and run certbot again |

For anything else, start with `journalctl -u recording-analyst -n 100 --no-pager`.
