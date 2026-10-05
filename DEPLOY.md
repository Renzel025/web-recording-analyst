# Deploying the Recording Analyst to a server

Same shape as lark-ops-ai: uvicorn behind nginx, kept alive by systemd.

```
test-automation (Mac) --upload--> https://DOMAIN/api/recordings --> nginx --> uvicorn :8020
teammates in Lark   --open link--> https://DOMAIN/r/<id>
```

## Alibaba Cloud (ECS) notes

- **Region:** use an international region (Singapore, Hong Kong, ...). From mainland China
  regions, api.anthropic.com and the Claude Code installer are blocked, and a public domain
  needs ICP filing. `bot-dev` (47.84.198.163) and `bot-prod` (8.219.139.155) are Singapore IPs.
- **Security group:** open inbound TCP 80 and 443 (ECS console → the instance → Security
  Groups → Inbound). Port 8020 stays closed; nginx reaches it locally.
- **OS packages:** on Alibaba Cloud Linux / CentOS use `dnf`/`yum` instead of `apt-get`:
  `sudo dnf install -y python3 nginx` plus ffmpeg (Alibaba Cloud Linux 3:
  `sudo dnf install -y epel-release && sudo dnf install -y ffmpeg`, or a static build from
  johnvansickle.com/ffmpeg if the repo lacks it), and `certbot` via `sudo dnf install -y certbot python3-certbot-nginx`.
  On these images nginx sites go in `/etc/nginx/conf.d/recording-analyst.conf` (no sites-available).
- **Storage (optional):** videos can go to Alibaba OSS instead of the server disk: set
  `STORAGE_BACKEND=oss` and the `OSS_*` variables, and `pip install oss2`.

## 1. Server packages

```bash
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg nginx certbot python3-certbot-nginx
python3 --version            # 3.8 or newer
ffmpeg -version | head -1    # frames are cut with ffmpeg
```

## 2. Copy the code

The repo has no remote yet, so copy it from the Mac (videos and the database stay behind
unless you want them, see step 7):

```bash
# on the Mac
rsync -av --exclude .venv --exclude data --exclude .env --exclude .playwright-mcp \
  ~/Desktop/web-recording-analyst/ root@SERVER:/root/web-recording-analyst/
```

## 3. Python environment and .env

```bash
cd /root/web-recording-analyst
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `PUBLIC_BASE_URL` | `https://DOMAIN` (what Lark links point to) |
| `INGEST_API_TOKEN` | the same token test-automation will send (copy it from the Mac's `.env`, or make a new one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `ANTHROPIC_AUTH_TOKEN` | your `claude setup-token` token (`sk-ant-oat...`), used by the `claude` CLI below |

## 4. The AI on the server

The assistant tries, in order: the Claude API (an `ANTHROPIC_API_KEY`, or an `ant auth login`
profile with credits), then the `claude` CLI with your subscription, then Groq.

For your subscription, install Claude Code on the server:

```bash
curl -fsSL https://claude.ai/install.sh | bash
~/.local/bin/claude --version
```

No browser login is needed: the app passes `ANTHROPIC_AUTH_TOKEN` from `.env` to the CLI as
`CLAUDE_CODE_OAUTH_TOKEN`. To check it by hand:

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat... ~/.local/bin/claude -p "say ok"
```

## 5. systemd

```bash
sudo cp deploy/recording-analyst.service.example /etc/systemd/system/recording-analyst.service
sudo systemctl daemon-reload
sudo systemctl enable --now recording-analyst
curl -s 127.0.0.1:8020/healthz          # {"ok":true}
journalctl -u recording-analyst -n 50 --no-pager
```

## 6. nginx + HTTPS

Point a DNS record (e.g. `recordings.mybot.ink`) at the server first, then:

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/recording-analyst
sudo sed -i 's/DOMAIN/recordings.mybot.ink/g' /etc/nginx/sites-available/recording-analyst
sudo ln -sf /etc/nginx/sites-available/recording-analyst /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d recordings.mybot.ink
```

Open `https://recordings.mybot.ink/` from your phone to confirm.

## 7. Optional: bring the recordings already on the Mac

```bash
# on the Mac, with the service stopped on the server
rsync -av ~/Desktop/web-recording-analyst/data/ root@SERVER:/root/web-recording-analyst/data/
```

Old links keep their ids, so `/r/nclnGkoLXJmY` becomes `https://DOMAIN/r/nclnGkoLXJmY`.

## 8. Test an upload from the Mac

```bash
cd ~/Desktop/web-recording-analyst
RECORDING_ANALYST_URL=https://recordings.mybot.ink INGEST_API_TOKEN=<server token> \
  .venv/bin/python scripts/upload_recording.py \
  ~/Desktop/test-automation/downloaded_files/run_screen.mp4 \
  --results ~/Desktop/test-automation/downloaded_files/last_run_results.json \
  --title "Upload test"
```

It prints the link to post in Lark.

## Updating

```bash
# on the Mac
rsync -av --exclude .venv --exclude data --exclude .env --exclude .playwright-mcp \
  ~/Desktop/web-recording-analyst/ root@SERVER:/root/web-recording-analyst/
# on the server
cd /root/web-recording-analyst && .venv/bin/pip install -r requirements.txt
sudo systemctl restart recording-analyst
```
