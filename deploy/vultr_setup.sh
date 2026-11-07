#!/usr/bin/env bash
# ==============================================================================
# Atelier Deployment Setup for Vultr VM
# ==============================================================================
set -euo pipefail

echo "============================================================"
echo " Setting up Atelier on Vultr Cloud Compute VM"
echo "============================================================"

# 1. Install Docker & prerequisites if not present
if ! command -v docker &>/dev/null; then
    echo "[-] Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable --now docker
fi

# 2. Configure Environment
if [ ! -f .env ]; then
    echo "[-] Creating .env configuration..."
    SECRET_KEY=$(openssl rand -hex 32)
    cat <<EOF > .env
ATELIER_SECRET_KEY=${SECRET_KEY}
ATELIER_ALLOWED_HOSTS=*
ATELIER_CORS_ORIGINS=http://localhost:3000
ATELIER_MODEL_CLIENT=atelier.api.core.clients.vultr_client
ATELIER_MODEL_NAME=llama-3.3-70b-instruct
VULTR_INFERENCE_BASE_URL=https://api.vultrinference.com/v1
# Put your Vultr Serverless Inference API Key below:
VULTR_INFERENCE_API_KEY=${VULTR_INFERENCE_API_KEY:-}

# Optional: Vultr Object Storage (S3-compatible)
ATELIER_S3_BUCKET=${ATELIER_S3_BUCKET:-}
ATELIER_S3_ENDPOINT=${ATELIER_S3_ENDPOINT:-https://ewr1.vultrobjects.com}
VULTR_STORAGE_ACCESS_KEY=${VULTR_STORAGE_ACCESS_KEY:-}
VULTR_STORAGE_SECRET_KEY=${VULTR_STORAGE_SECRET_KEY:-}
EOF
    echo "[!] Generated new .env. Please ensure VULTR_INFERENCE_API_KEY is populated."
fi

# 3. Build Container Images
echo "[-] Building Atelier containers..."
docker build -f docker/Dockerfile -t atelier-ref .
docker build -f docker/app.Dockerfile -t atelier-app .
docker build -f docker/baseline.Dockerfile -t atelier-baseline .
docker build -f web/Dockerfile -t atelier-web ./web

# 4. Migrate database (people create their own accounts on the web UI's sign-up page)
echo "[-] Running migrations..."
docker compose run --rm api manage migrate

# 5. Start stack
echo "[-] Launching Atelier services with Docker Compose..."
docker compose up -d

PUBLIC_IP=$(curl -s https://api.ipify.org || echo "YOUR_SERVER_IP")
echo "============================================================"
echo " Atelier is now running on your Vultr VM!"
echo " Web UI   : http://${PUBLIC_IP}:3000"
echo " REST API : http://${PUBLIC_IP}:8000/api/v1"
echo " Docs/Ops : http://${PUBLIC_IP}:8000/api/v1/ops"
echo "============================================================"
