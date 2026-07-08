# kag — Deployment Guide

> Production deployment to `kag.aiconn.ai`. Local dev is covered in the main [README](../README.md#quick-start-local-development).

---

## Architecture Recap

```
                    Internet
                       │
                       │ HTTPS
                       ▼
              ┌──────────────────┐
              │   Cloudflare     │  ← TLS, DDoS, rate-limit
              │      Edge        │
              └────────┬─────────┘
                       │ outbound tunnel
                       ▼
              ┌──────────────────┐
              │  cloudflared     │  ← runs on kag host, initiates outbound connection
              │  (Tunnel agent)  │     No inbound port exposed
              └────────┬─────────┘
                       │ http://localhost:8800
                       ▼
               ┌──────────────────────┐
               │   kag (uvicorn)      │  ←── systemd, bound to 127.0.0.1 only
               │   FastAPI            │
               └────────┬─────────────┘
                        │
         ┌──────────────┼──────────────┐
         │              │              │
         ▼              ▼              ▼
    ┌─────────┐   ┌──────────┐   ┌──────────┐
    │  Redis  │   │ ArangoDB │   │  Qdrant  │
    └─────────┘   └──────────┘   └──────────┘
                                       ▲
                                       │
                               ┌───────┴────────┐
                               │  SeaweedFS     │
                               └────────────────┘
                                       ▲
                                       │
                               ┌───────┴────────┐
                               │   dllm (LLM)   │  ← OpenAI-compatible at :11400/v1
                               └────────────────┘
```

All four datastores (ArangoDB, Qdrant, SeaweedFS, Redis) are **shared with aibox-th** and live in a different namespace (prefix `kag_` / bucket `kag` / keys under `kag/`).

---

## Server Provisioning

### Minimum

| Resource | Recommendation |
|---|---|
| CPU | 2 vCPU (4 vCPU for production) |
| RAM | 4 GB (8 GB recommended) |
| Disk | 30 GB OS + 50 GB+ for logs/cache |
| OS | Ubuntu 22.04 LTS or 24.04 LTS |
| Public IP | **No** — Cloudflare Tunnel is outbound-only, no inbound port needed |

### LLM host — [dllm](https://github.com/dllm) (preferred)

[dllm](https://github.com/dllm) is the team's unified LLM serving layer. It exposes an OpenAI-compatible API at `:11400/v1` and runs on:

- **Apple Silicon** (Mac Mini M4 Pro 64GB+, Mac Studio M2 Ultra) via MLX
- **NVIDIA** (RTX 4090/5090, DGX Spark GB-10, H100) via vLLM
- **ARM64 Linux edge boxes** (ASUS/Dell/HP/銘凡 GB-10 devices)

| Resource | Minimum (Mac Mini 64GB) | Recommended (DGX Spark 128GB) |
|---|---|---|
| Unified memory | 64 GB | 128 GB |
| Disk (model weights) | 50 GB | 100 GB |
| Concurrent users | 2-4 | 4-8 |

**Default model set on dllm** (covers all kag needs):

| Model | Purpose | VRAM/RAM | kag env var |
|---|---|---|---|
| `qwen3-30b-a3b-4bit` | Main chat/reasoning (graph extraction, search reasoning) | ~18 GB | `GRAPH_MODEL` |
| `qwen2.5-vl-8b` | Multimodal (image captioning via OpenAI `image_url`) | ~5 GB | `VLM_MODEL` |
| `bge-m3` | Embeddings (1024-dim, multilingual incl. 繁中) | ~2 GB | `EMBEDDING_MODEL` |

Total: ~25 GB RAM headroom; comfortable on 64GB+ unified memory.

**Pull the models on the dllm host before first use**:

```bash
dllm pull qwen3-30b-a3b-4bit
dllm pull qwen2.5-vl-8b
dllm pull bge-m3
```

**Pin the chat model** (auto-loaded on first request, kept hot):

```bash
dllm pin qwen3-30b-a3b-4bit
dllm pin bge-m3
```

The LLM host can be the same machine as kag (in dev), but for production should be separate (LLM inference is GPU/CPU bound; kag is I/O bound).

### ⚠ Ollama is NOT supported (deprecated)

Ollama is being **shut down** across the team's LLM stack. While it superficially exposes OpenAI-compatible endpoints (`/v1/chat/completions`, `/v1/embeddings`), several details diverge from the OpenAI spec — embedding array structure, `json_mode` behavior, and vision message format — which have caused subtle integration bugs in the past.

**All new kag deployments MUST use [dllm](https://github.com/dllm).** If you have an existing Ollama setup, plan migration to dllm.

> Historical note: prior versions of kag specs documented Ollama as a dev fallback. As of the v0.1 spec, this is removed; only fully OpenAI-conformant servers are supported.

### Domain & Public Access (Cloudflare Tunnel)

`kag.aiconn.ai` is exposed via **Cloudflare Tunnel** — there is **no inbound port** on the kag host. The `cloudflared` daemon on the host initiates an outbound connection to Cloudflare's edge, and the edge routes public traffic to that tunnel.

| Item | Value |
|---|---|
| Public hostname | `kag.aiconn.ai` |
| Edge | Cloudflare (TLS, DDoS, WAF, rate limiting all handled by CF) |
| Tunnel agent | `cloudflared` (systemd unit `cloudflared`) |
| Backend origin | `http://127.0.0.1:8800` (kag bound to localhost only) |
| DNS | CNAME `kag.aiconn.ai` → `<TUNNEL_ID>.cfargotunnel.com` (managed in Cloudflare dashboard) |

**No public IP needed on the kag host.** No nginx, no certbot, no firewall rules for ports 80/443. Only `cloudflared` (outbound :7844 to `region.v2.argotunnel.com`) and the local kag service on 127.0.0.1:8800.

> Tunnel provisioning (creating the tunnel, downloading the credential JSON, adding the DNS record) is done **once** in the Cloudflare dashboard. After that, only `cloudflared` config + systemd service are needed on the kag host.

### Tunnel config on the kag host

`/etc/cloudflared/config.yml` (managed by the `cloudflared` package):

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: kag.aiconn.ai
    service: http://127.0.0.1:8800
    originRequest:
      noTLSVerify: false
      keepAliveConnections: 16
  # Catch-all 404 for any other hostname hitting this tunnel
  - service: http_status:404
```

The `<TUNNEL_ID>` and credential JSON come from `cloudflared tunnel login` + `cloudflared tunnel create kag` (run once on any machine; copy the resulting files to the kag host).

---

## Installation

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git
# cloudflared — install from Cloudflare's official deb repo
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared focal main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared
# (No nginx, no certbot — Cloudflare handles all of that.)
```

### 2. Install uv (one-time)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 3. Clone & install

```bash
sudo mkdir -p /opt/kag
sudo chown $USER:$USER /opt/kag
cd /opt/kag
git clone <repo-url> .
uv sync
```

### 4. Configure environment

```bash
cp .env.example .env
chmod 600 .env   # secrets should not be world-readable
$EDITOR .env
```

Required values (do NOT use the dev defaults from `.env.example`):

```bash
KAG_ENV=production
KAG_LOG_LEVEL=INFO

# Generate strong secrets
KAG_API_KEY_PEPPER=$(openssl rand -hex 32)
KAG_ADMIN_TOKEN=$(openssl rand -hex 32)

# Point to shared infra
ARANGO_URL=http://10.0.0.10:8529        # private IP of shared arango
QDRANT_URL=http://10.0.0.11:6333
SEAWEED_URL=http://10.0.0.12:8888
SEAWEED_BUCKET=kag
REDIS_URL=redis://10.0.0.13:6379/0

# LLM (dllm preferred; OpenAI-compatible)
LLM_BASE_URL=http://10.0.0.20:11400/v1
LLM_API_KEY=__CHANGE_ME__              # bearer token from dllm admin
EMBEDDING_MODEL=bge-m3
GRAPH_MODEL=qwen3-30b-a3b-4bit
VLM_MODEL=qwen2.5-vl-8b

# Production hardening
KAG_WORKERS=2
KAG_FILE_PATH_ALLOWLIST=/opt/kag/imports   # for path-mode uploads
KAG_VECTOR_CHUNK_SIZE=512
KAG_VECTOR_CHUNK_OVERLAP=64
```

### 5. Bootstrap schema

```bash
cd /opt/kag
uv run kag migrate
uv run kag db-check   # verify all dependencies
```

### 6. Test boot

```bash
cd /opt/kag
uv run kag dev
# In another terminal:
curl -s http://localhost:8800/health | jq .
# Should show all deps "ok": true
```

Kill the dev server (Ctrl+C) once verified.

---

## systemd Service

### `/etc/systemd/system/kag.service`

```ini
[Unit]
Description=kag — Knowledge-Augmented Generation Service
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=kag
Group=kag
WorkingDirectory=/opt/kag
EnvironmentFile=/opt/kag/.env
ExecStart=/home/kag/.local/bin/uv run kag serve
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/kag/kag.log
StandardError=append:/var/log/kag/kag.log

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/kag /var/log/kag
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/kag-worker.service`

```ini
[Unit]
Description=kag — Celery Worker
After=network.target redis.service
Wants=network-online.target

[Service]
Type=simple
User=kag
Group=kag
WorkingDirectory=/opt/kag
EnvironmentFile=/opt/kag/.env
ExecStart=/home/kag/.local/bin/uv run kag worker --concurrency=2
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/kag/worker.log
StandardError=append:/var/log/kag/worker.log

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/kag /var/log/kag

[Install]
WantedBy=multi-user.target
```

### Enable & start

```bash
sudo useradd -r -s /bin/bash kag
sudo mkdir -p /var/log/kag
sudo chown kag:kag /var/log/kag /opt/kag

sudo systemctl daemon-reload
sudo systemctl enable --now kag kag-worker
sudo systemctl status kag kag-worker
```

**Note on `kag serve`**: this is a planned subcommand (Wave 1, task 2/6) that runs uvicorn in production mode (no reload, proper signal handling). For now, you can use `uv run uvicorn kag.main:app --host 0.0.0.0 --port 8800 --workers $KAG_WORKERS` as the ExecStart until the CLI command lands.

### CLI management (works once the CLI is built)

```bash
sudo -u kag kag status
sudo -u kag kag logs -f
sudo -u kag kag restart
```

---

## cloudflared Tunnel Setup

`cloudflared` is the outbound tunnel agent that exposes `kag.aiconn.ai` to the public internet without opening any inbound port on the kag host.

### Install & authenticate

```bash
# Already installed via the apt step in §1
cloudflared --version
# → cloudflared version 2026.5.0 ...

# One-time per operator: log in to Cloudflare and authorize this machine
cloudflared tunnel login
# → opens browser, pick the aiconn.ai zone, downloads cert.pem to ~/.cloudflared/

# One-time per tunnel: create the tunnel (run on any machine that has cloudflared + the cert)
cloudflared tunnel create kag
# → prints "Tunnel credentials written to /home/<you>/.cloudflared/<TUNNEL_ID>.json"
# → record <TUNNEL_ID> (UUID); you'll need it for the config below
```

### Copy credentials to the kag host

```bash
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/
sudo chmod 600 /etc/cloudflared/<TUNNEL_ID>.json
sudo cp ~/.cloudflared/cert.pem /etc/cloudflared/
```

### DNS record (one-time in Cloudflare dashboard or CLI)

```bash
cloudflared tunnel route dns kag kag.aiconn.ai
# → Creates a CNAME from kag.aiconn.ai → <TUNNEL_ID>.cfargotunnel.com
```

### Run cloudflared as a systemd service

The `cloudflared` package ships with a systemd unit. Edit the config to point to your tunnel:

```bash
sudo nano /etc/cloudflared/config.yml
```

Paste:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: kag.aiconn.ai
    service: http://127.0.0.1:8800
    originRequest:
      noTLSVerify: false
      keepAliveConnections: 16
  - service: http_status:404
```

Then enable and start:

```bash
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared   # should show "active (running)"
```

### Verify the tunnel end-to-end

```bash
# From any external machine
curl -s https://kag.aiconn.ai/health | jq .

# From the kag host, check cloudflared logs for the connection
sudo journalctl -u cloudflared -f
```

If the curl returns `{"status": "ok", ...}` and `journalctl` shows "Registered tunnel connection ...", the deployment is live.

---

## Shared Infrastructure Configuration

kag **must** use the SAME ArangoDB / Qdrant / SeaweedFS / Redis instances as aibox-th, but with namespacing. Verify before deploying:

### ArangoDB

- Connect as `root` with same credentials as aibox-th
- Use **the same database name** (e.g., `aistock`)
- All kag collections auto-prefixed `kag_` — verify via `kag db-check`
- **Never** drop a non-`kag_` collection

### Qdrant

- Same instance, no special config needed
- Per-KB collections auto-prefixed `kag_kb_`
- If aibox-th uses a different embedding dim, ensure `QDRANT_VECTOR_DIM` matches kag's embedding model

### SeaweedFS

- Use bucket `kag` (NEW bucket; do NOT share with aibox-th's bucket)
- All keys under `kag/` prefix

### Redis

- Same Redis instance, different DB number to isolate (e.g., `/1` for kag, `/0` for aibox-th)
- Or: same DB but rely on Celery's key namespacing (`kag:celery:...` vs aibox-th's keys)

---

## Monitoring

### Health check

An external uptime monitor (Cloudflare Analytics, UptimeRobot, or your team's existing stack) should hit `https://kag.aiconn.ai/health` every 30s and alert on 5xx. kag's `/health` checks all dependencies (ArangoDB, Qdrant, SeaweedFS, Redis, LLM).

### Logs

- `/var/log/kag/kag.log` — JSON structured logs (use `jq` to query)
- `/var/log/kag/worker.log` — Celery worker logs
- `journalctl -u kag -u kag-worker` for systemd-managed logs

### Metrics (planned, v0.2)

A `/metrics` endpoint will expose Prometheus-format metrics:
- `kag_http_requests_total{path, status}`
- `kag_celery_tasks_total{type, status}`
- `kag_celery_task_duration_seconds{type}`
- `kag_qdrant_collection_size{kb_key}`
- `kag_arango_documents_total{collection}`

### Log aggregation (optional)

Pipe `/var/log/kag/*.log` to a Loki / Elastic / Datadog via Promtail or Filebeat. Each line is JSON, so structured queries are trivial.

---

## Backup & Recovery

### What to back up

| Data | Method |
|---|---|
| ArangoDB (kag collections only) | `arangodump --collection kag_*` nightly |
| SeaweedFS bucket `kag` | `weed shell` to copy / `rclone` to S3-compatible target |
| Redis | Not needed (Celery state is rebuildable) |
| Qdrant | Optional (can be re-vectorized from chunks); nightly snapshot recommended |
| `.env` | Back up to secret manager |

### Recovery

- ArangoDB: `arangorestore --input <dump>` to restore
- SeaweedFS: `rclone copy` from backup target
- Qdrant: if lost, trigger re-vectorize on all files via `kag-admin` reindex script (planned v0.2)
- Redis: no recovery needed

---

## Scaling

### Vertical (single-host)

- Add CPU/RAM to the kag host
- Increase `KAG_WORKERS` (uvicorn processes)
- Increase celery `--concurrency` (worker processes)

### Horizontal

- Run multiple kag servers behind a load balancer (Cloudflare Tunnel can round-robin to multiple backends via additional ingress rules, or use Cloudflare Load Balancer product)
- Celery workers scale independently — add more `kag-worker` instances
- All datastores are shared and support multiple connections
- No session state means stateless scaling works out of the box

### Per-component

| Component | Scaling strategy |
|---|---|
| uvicorn | Stateless; scale horizontally |
| Celery worker | Scale horizontally (Celery handles distribution) |
| Redis | Use Redis Cluster or Sentinel for HA |
| ArangoDB | Use ArangoDB Cluster (active-failover) for HA; read replicas for read scaling |
| Qdrant | Distributed mode; add nodes to scale vector capacity |
| SeaweedFS | Add volume servers; data is distributed by default |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `kag status` reports "not running" | systemd service not started | `sudo systemctl start kag` |
| `/health` returns 503 with `arango: connection refused` | ARANGO_URL wrong or arango down | Verify `KAG_ADMIN_TOKEN` is set; check `systemctl status kag`; verify arango reachable from kag host |
| File upload returns 500 | SeaweedFS bucket missing or wrong creds | `kag db-check`; verify `SEAWEED_*` env |
| Vectorize jobs stuck in `pending` | Celery worker not running | `sudo systemctl status kag-worker` |
| Vectorize jobs fail with `connection refused` to LLM server | Wrong `LLM_BASE_URL` or dllm not running | `curl $LLM_BASE_URL/models` from kag host (auth header required) |
| Embedding model returns wrong-dim vectors | `EMBEDDING_MODEL` output dim doesn't match `QDRANT_VECTOR_DIM` | Verify with `dllm show bge-m3` (or `ollama show bge-m3`); set `QDRANT_VECTOR_DIM` to match |
| 401 on KB API key | Pepper changed (regenerated secrets) | Re-issue API keys; old keys will be invalid |

---

## Future Work (v0.2+)

- Helm chart for k8s deployment
- Multi-region deployment with cross-region replication
- Webhooks for job completion events
- OpenTelemetry distributed tracing
- Admin UI (still NO end-user UI, but an internal one for KB/Ontology management)
- GraphQL endpoint as alternative to REST
