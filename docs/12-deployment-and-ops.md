# 12. Deployment and Operations

This document describes the production deployment, infrastructure architecture, environment configuration, and operational procedures for Atelier on Vultr Cloud Compute for the **Vultr: Agent Rush Hackathon** (November 3–8, 2026).

---

## 1. Cloud Infrastructure Overview

- **Cloud Provider:** Vultr Cloud Compute
- **Location:** London (LHR)
- **Plan:** `vx1-g-2c-8g-120s` (2 vCPUs, 8 GB RAM, 120 GB Local NVMe SSD)
- **OS:** Ubuntu 24.04 LTS (x86_64)
- **Public IP:** `209.250.229.110`
- **Domain:** `https://209.250.229.110.sslip.io`
- **TLS/SSL:** Genuine Let's Encrypt certificate (valid through February 2027) with automated background renewal daemon.

---

## 2. Architecture & Service Topology

Atelier runs as a multi-container Docker Compose stack fronted by an Nginx reverse proxy:

```
[ Client Browser / CLI ]
           |
       (HTTPS 443)
           v
    [ Nginx Reverse Proxy ]
      |                 |
      | /api/*          | /*
      v                 v
[ Django Ninja API ]  [ Next.js 16 Web UI ]
  (port 8000)           (port 3000)
      |
 (Job Queue / DB)
      v
[ Sandboxed Worker ] ---> [ Vultr Serverless Inference ]
      |                   (https://api.vultrinference.com/v1)
 (Local Storage /data)
```

### Services Breakdown

1. **`atelier-nginx-1` (Nginx Alpine):**
   - Terminates TLS/SSL on port 443 using Let's Encrypt certificates.
   - Redirects all port 80 HTTP traffic to HTTPS.
   - Handles SSE streaming for real-time job progress (`proxy_buffering off; proxy_cache off;`).
   - Routes `/api/` to `api:8000` and `/` to `web:3000`.

2. **`atelier-certbot-1` (Certbot):**
   - Automatically checks and renews certificates every 12 hours via shared ACME challenge directory.

3. **`atelier-api-1` (Django Ninja ASGI):**
   - Stateless REST API running on Uvicorn.
   - Manages organizations, API keys, assets, specs, and jobs.
   - Immediate validation endpoint (`POST /api/v1/validate`) verifies DAG correctness without compute or AI tokens.

4. **`atelier-worker-1` (Atelier Media Worker):**
   - Pulls queued jobs from SQLite/PostgreSQL queue.
   - For `spec` jobs: Executes fused graph in isolated, resource-capped subprocesses (Pillow, ffmpeg 7.x, LibreOffice).
   - For `prompt` jobs: Invokes AI Director via Vultr Serverless Inference adapter to formulate canonical JSON DAG, then executes it deterministically.

5. **`atelier-web-1` (Next.js 16 App Router):**
   - Human-facing dashboard with asset browser, natural-language prompt input, real-time SSE execution timeline, and artifact inspector.

---

## 3. Operations & Capability Catalogue

As of November 2026, the engine exposes **69 operations** across all media domains:

| Category | Ops Count | Key Capabilities |
| :--- | :--- | :--- |
| **Video** | 21 ops | `video.trim`, `video.concat`, `video.reframe`, `video.auto_reframe`, `video.color`, `video.fade`, `video.speed`, `video.mute`, `video.soundtrack`, `video.text`, `video.captions`, `video.blur_faces`, `video.overlay`, `video.frame`, `video.slideshow`, `video.crop`, `video.pip`, `video.transition`, `video.lower_third` |
| **Audio** | 10 ops | `audio.loudnorm` (EBU R128), `audio.denoise` (DeepFilterNet3), `audio.trim`, `audio.fade`, `audio.volume`, `audio.speed`, `audio.concat`, `audio.mix`, `audio.silence_remove` |
| **Image** | 18 ops | `image.resize`, `image.crop`, `image.filter`, `image.color`, `image.watermark`, `image.remove_background` (BiRefNet), `image.convert` |
| **Document** | 14 ops | Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), PDF conversions via headless LibreOffice and pikepdf |
| **Checks** | 6 ops | Automated assertions: dimensions, loudness (-14 to -16 LUFS), contrast, codec compliance |

---

## 4. Benchmarks & Latency

### Deterministic Engine Execution (Zero AI Tokens)
- **Op:** `image.resize` (1200x800 -> 400x267 RGB PNG)
- **Host Execution:** Vultr `vx1-g-2c-8g-120s`
- **Total Compute Time:** **0.133 seconds**
- **Wall-clock Turnaround (Client -> HTTPS -> API -> Worker -> Output):** **0.139 seconds**
- **Tokens Used:** 0

### AI Director Inference (Vultr Serverless Inference)
- **Endpoint:** `https://api.vultrinference.com/v1`
- **Models:** `deepseek-v4.1-flash-1017`, `llama-3.3-70b-instruct`
- **Prompt Formulation:** Structured JSON DAG via function calling / json_schema
- **Inference Latency:** ~0.8s – 1.4s per turn
- **Token Efficiency:** Single-turn plan formulation (~180–320 tokens total) vs 10,000+ tokens for multi-turn agents.

---

## 5. Security & Isolation Model

- **Filesystem Sandbox:** Subprocesses execute against a temporary directory with read-only root and no execution rights on output paths.
- **Network Isolation:** Media render processes run strictly air-gapped without socket permissions. Model calls happen exclusively at the orchestrator level.
- **Resource Caps:** Process limits enforced via OS cgroups/RLIMIT (CPU timeout 300s, max memory 4 GB per step).
- **Containment Demonstration:** Automated harness (`scripts/demo_containment.py`) verifies:
  1. Filesystem escape block (`rm -rf /` fails with `PermissionDenied`)
  2. Infinite loop kill (timeout triggered, host unimpacted)
  3. Network exfiltration block (zero outbound sockets permitted in render sandbox)

---

## 6. Operational Runbook

### Restarting Services with New Configuration
```bash
# Docker Compose requires recreate to reload environment variables:
docker compose up -d --force-recreate worker api
```

### Checking Worker Logs
```bash
docker compose logs -f --tail=50 worker
```

### Inspecting Container DNS / Connectivity
```bash
docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('https://api.vultrinference.com/v1/models').status)"
```
