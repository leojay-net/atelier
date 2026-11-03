#!/usr/bin/env bash
# ==============================================================================
# Atelier Deployment Setup for Vultr VM
# Vultr Agent Rush Hackathon 2026
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
EOF
    echo "[!] Generated new .env. Please ensure VULTR_INFERENCE_API_KEY is populated."
fi

# 3. Build Container Images
echo "[-] Building Atelier containers..."
docker build -f docker/Dockerfile -t atelier-ref .
docker build -f docker/app.Dockerfile -t atelier-app .
docker build -f docker/baseline.Dockerfile -t atelier-baseline .
docker build -f web/Dockerfile -t atelier-web ./web

# 4. Migrate database and create default organization
echo "[-] Running migrations and initializing default organization..."
docker compose run --rm api manage migrate
echo "[-] Creating default organization 'vultr-demo'..."
KEY_OUTPUT=$(docker compose run --rm api manage create_org vultr-demo "Vultr Agent Demo" 2>&1 || true)
echo "$KEY_OUTPUT"

# Extract API key if created
API_KEY=$(echo "$KEY_OUTPUT" | grep -o 'atk_[a-zA-Z0-9_]*' | head -n1 || true)
if [ -n "$API_KEY" ]; then
    echo "ATELIER_API_KEY=${API_KEY}" >> .env
fi

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
