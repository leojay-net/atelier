#!/usr/bin/env bash
# ==============================================================================
# Automatic Let's Encrypt SSL Setup for Atelier
# ==============================================================================
set -euo pipefail

DOMAIN="${1:-${DOMAIN:-209.250.229.110.sslip.io}}"
EMAIL="${2:-${EMAIL:-admin@${DOMAIN}}}"

echo "============================================================"
echo " Setting up Let's Encrypt SSL for: $DOMAIN"
echo " Notification email: $EMAIL"
echo "============================================================"

# Ensure DOMAIN in .env
if ! grep -q "^DOMAIN=" .env 2>/dev/null; then
    echo "DOMAIN=${DOMAIN}" >> .env
else
    sed -i "s/^DOMAIN=.*/DOMAIN=${DOMAIN}/" .env
fi

mkdir -p deploy/certbot/conf deploy/certbot/www

# 1. Stop Nginx so port 80 is completely free for Certbot standalone validation
echo "[-] Ensuring port 80 is clear..."
docker compose stop nginx 2>/dev/null || true

# 2. Remove any manually created dummy directory so Certbot has full control
if [ -d "deploy/certbot/conf/live/${DOMAIN}" ]; then
    # If fullchain.pem is not a symlink managed by certbot, remove the manual bootstrap dir
    if [ ! -L "deploy/certbot/conf/live/${DOMAIN}/fullchain.pem" ]; then
        echo "[-] Cleaning manual bootstrap directory..."
        rm -rf "deploy/certbot/conf/live/${DOMAIN}"
    fi
fi

# 3. Request genuine Let's Encrypt certificate directly via Certbot standalone
echo "[-] Requesting genuine Let's Encrypt certificate..."
docker compose run --rm -p 80:80 certbot certonly --standalone \
    -d "${DOMAIN}" \
    --email "${EMAIL}" \
    --agree-tos \
    --non-interactive

# 4. Start Nginx and background auto-renewal
echo "[-] Starting Nginx and renewal daemon..."
docker compose up -d nginx certbot

echo "============================================================"
echo " SSL Successfully Configured with Let's Encrypt!"
echo " Web UI   : https://${DOMAIN}"
echo " REST API : https://${DOMAIN}/api/v1"
echo " Docs/Ops : https://${DOMAIN}/api/v1/ops"
echo "============================================================"
