# kag production deployment

This directory contains the files needed to run kag in production
on `kag.aiconn.ai`. The deployment model is **no inbound port**:
Cloudflare Tunnel (`cloudflared`) makes an outbound connection
from the host to Cloudflare's edge, and the public hostname
proxies traffic back to `127.0.0.1:8800` on the kag host.

## Files

- `systemd/kag.service` — runs the HTTP API on `127.0.0.1:8800`
- `systemd/kag-worker.service` — runs the Celery worker
  (vectorize + graph extraction)
- `../docker-compose.prod.yml` — equivalent of the above two,
  containerized; useful for local prod-like runs

For the full deployment narrative (provisioning, TLS, secrets,
backups, monitoring) see `docs/DEPLOYMENT.md`.

## Quick start on a fresh host

```bash
# 1. System dependencies
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared focal main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# 2. kag application
sudo useradd -r -s /bin/bash -d /opt/kag kag
sudo mkdir -p /opt/kag /var/log/kag
sudo chown -R kag:kag /opt/kag /var/log/kag
cd /opt/kag
sudo -u kag git clone <repo-url> .
sudo -u kag uv sync --frozen
sudo cp /home/kag/dotenv /opt/kag/.env
sudo chown kag:kag /opt/kag/.env && sudo chmod 600 /opt/kag/.env

# 3. systemd
sudo cp deploy/systemd/kag.service deploy/systemd/kag-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kag kag-worker
sudo systemctl status kag kag-worker

# 4. Cloudflare Tunnel
# (One-time) on any machine with cloudflared installed:
cloudflared tunnel login
cloudflared tunnel create kag
cloudflared tunnel route dns kag kag.aiconn.ai
# Copy the resulting credentials file to /etc/cloudflared/<TUNNEL_ID>.json
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared

# 5. Verify end-to-end
curl -sSf https://kag.aiconn.ai/health | jq .
# All deps should be "ok": true (or the bare /health returns 200)
```

## Container alternative

`docker-compose.prod.yml` (at the repo root) gives the same
topology without systemd. Useful for hosts without systemd, or
when you want to validate the image locally before rolling it
to a cloud VM. The compose file binds `127.0.0.1:8800` only;
the cloudflared daemon still makes an outbound tunnel from the
host.

## Hardening notes

- `kag.service` runs as the unprivileged `kag` user with
  `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`.
  The only writable paths are `/opt/kag` and `/var/log/kag`.
- `KAG_HOST=127.0.0.1` (production default in `.env.example`).
  The kag binary **must not** bind to a public address. Cloudflare
  Tunnel is the only way in.
- `.env` should be `chmod 600` and owned by `kag`. Rotate
  `KAG_API_KEY_PEPPER` invalidates every KB API key; rotate
  `KAG_ADMIN_TOKEN` invalidates admin auth.
