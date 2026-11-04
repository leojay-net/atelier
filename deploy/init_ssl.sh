#!/usr/bin/env bash
# ==============================================================================
# Automatic Let's Encrypt SSL Setup for Atelier
# ==============================================================================
set -euo pipefail

DOMAIN="${1:-${DOMAIN:-209.250.229.110.sslip.io}}"
EMAIL="${2:-${EMAIL:-admin@${DOMAIN}}}"

echo "============================================================"
echo " Setting up Let's Encrypt SSL"
echo " Domain: $DOMAIN"
echo " Email : $EMAIL"
echo "============================================================"

# Ensure DOMAIN and CORS origins in .env
if ! grep -q "^DOMAIN=" .env 2>/dev/null; then
    echo "DOMAIN=${DOMAIN}" >> .env
else
    sed -i "s/^DOMAIN=.*/DOMAIN=${DOMAIN}/" .env
fi

# Ensure data directories exist
mkdir -p deploy/certbot/conf deploy/certbot/www

CERT_DIR="deploy/certbot/conf/live/${DOMAIN}"

# 1. Generate temporary self-signed certificate if none exists
if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
    echo "[-] Generating bootstrap certificate so Nginx can start..."
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -keyout "${CERT_DIR}/privkey.pem" \
        -out "${CERT_DIR}/fullchain.pem" \
        -subj "/CN=localhost"
fi

# 2. Start Nginx
echo "[-] Starting Nginx..."
docker compose up -d nginx

# 3. Request official Let's Encrypt certificate using certbot container
echo "[-] Requesting Let's Encrypt SSL certificate via certbot..."
docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    -d ${DOMAIN} \
    --email ${EMAIL} \
    --rsa-key-size 4096 \
    --agree-tos \
    --force-renewal \
    --non-interactive" certbot

# 4. Reload Nginx with genuine certificate
echo "[-] Reloading Nginx with new certificate..."
docker compose exec nginx nginx -s reload

# 5. Start certbot background renewal service
echo "[-] Starting automated certificate renewal daemon..."
docker compose up -d certbot

echo "============================================================"
echo " SSL Successfully Configured with Let's Encrypt!"
echo " Web UI   : https://${DOMAIN}"
echo " REST API : https://${DOMAIN}/api/v1"
echo " Docs/Ops : https://${DOMAIN}/api/v1/ops"
echo "============================================================"
