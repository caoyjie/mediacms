#!/usr/bin/env bash
set -euo pipefail

# CloudFlare Tunnel Setup Script for MediaCMS
# Usage: ./setup_cloudflare_tunnel.sh DOMAIN TUNNEL_NAME

if (($# < 2)); then
    printf 'Usage: %s DOMAIN TUNNEL_NAME\n' "${0##*/}" >&2
    printf 'Example: %s mediacms.yourdomain.com mediacms\n' "${0##*/}" >&2
    exit 64
fi

DOMAIN=$1
TUNNEL_NAME=$2
CONFIG_DIR="/etc/cloudflared"
CREDS_DIR="/root/.cloudflared"

printf '=== CloudFlare Tunnel Setup for MediaCMS ===\n\n'

# Step 1: Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    printf 'Step 1: Installing cloudflared...\n'
    cd /tmp
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i cloudflared-linux-amd64.deb
    rm cloudflared-linux-amd64.deb
    printf '✅ cloudflared installed\n\n'
else
    printf 'Step 1: cloudflared already installed (%s)\n\n' "$(cloudflared --version | head -1)"
fi

# Step 2: Check authentication
if [[ ! -f "$CREDS_DIR/cert.pem" ]]; then
    printf 'Step 2: Authenticate with CloudFlare\n'
    printf 'This will open a browser window for authentication.\n'
    printf 'Press Enter to continue...\n'
    read -r
    cloudflared tunnel login
    printf '✅ Authenticated with CloudFlare\n\n'
else
    printf 'Step 2: Already authenticated with CloudFlare\n\n'
fi

# Step 3: Create tunnel
printf 'Step 3: Creating tunnel "%s"...\n' "$TUNNEL_NAME"
if cloudflared tunnel list | grep -q "$TUNNEL_NAME"; then
    printf 'ℹ️  Tunnel "%s" already exists\n' "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
else
    cloudflared tunnel create "$TUNNEL_NAME"
    TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')
    printf '✅ Tunnel created with ID: %s\n' "$TUNNEL_ID"
fi
printf '\n'

# Step 4: Configure DNS
printf 'Step 4: Configuring DNS for %s...\n' "$DOMAIN"
if cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN" 2>&1 | grep -q "already exists"; then
    printf 'ℹ️  DNS record already exists for %s\n' "$DOMAIN"
else
    cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN"
    printf '✅ DNS record created: %s -> %s.cfargotunnel.com\n' "$DOMAIN" "$TUNNEL_ID"
fi
printf '\n'

# Step 5: Create configuration
printf 'Step 5: Creating tunnel configuration...\n'
sudo mkdir -p "$CONFIG_DIR"

sudo tee "$CONFIG_DIR/config.yml" > /dev/null <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS_DIR/$TUNNEL_ID.json

# Logging
loglevel: info

# Tunnel settings
metrics: 0.0.0.0:2000
no-autoupdate: true

ingress:
  # MediaCMS frontend
  - hostname: $DOMAIN
    service: http://localhost:8080
    originRequest:
      noTLSVerify: false
      connectTimeout: 30s
      keepAliveTimeout: 90s
      http2Origin: false

  # Catch-all rule (required)
  - service: http_status:404
EOF

printf '✅ Configuration created at %s/config.yml\n\n' "$CONFIG_DIR"

# Step 6: Test tunnel
printf 'Step 6: Testing tunnel configuration...\n'
if cloudflared tunnel --config "$CONFIG_DIR/config.yml" info "$TUNNEL_NAME" &> /dev/null; then
    printf '✅ Tunnel configuration valid\n\n'
else
    printf '❌ Tunnel configuration invalid\n' >&2
    exit 1
fi

# Step 7: Install as service
printf 'Step 7: Installing cloudflared as system service...\n'
if systemctl is-active --quiet cloudflared; then
    printf 'Stopping existing cloudflared service...\n'
    sudo systemctl stop cloudflared
fi

sudo cloudflared service install
sudo systemctl daemon-reload
printf '✅ Service installed\n\n'

# Step 8: Start service
printf 'Step 8: Starting cloudflared service...\n'
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# Wait a bit for service to start
sleep 3

if systemctl is-active --quiet cloudflared; then
    printf '✅ Service started and enabled\n\n'
else
    printf '❌ Service failed to start\n' >&2
    printf 'Check logs with: sudo journalctl -u cloudflared -f\n' >&2
    exit 1
fi

# Step 9: Summary
printf '=== Setup Complete! ===\n\n'
printf 'Tunnel Information:\n'
printf '  Name: %s\n' "$TUNNEL_NAME"
printf '  ID: %s\n' "$TUNNEL_ID"
printf '  Domain: %s\n' "$DOMAIN"
printf '  Backend: http://localhost:8080\n\n'

printf 'Configuration:\n'
printf '  Config: %s/config.yml\n' "$CONFIG_DIR"
printf '  Credentials: %s/%s.json\n\n' "$CREDS_DIR" "$TUNNEL_ID"

printf 'Service Management:\n'
printf '  Status:  sudo systemctl status cloudflared\n'
printf '  Logs:    sudo journalctl -u cloudflared -f\n'
printf '  Restart: sudo systemctl restart cloudflared\n'
printf '  Stop:    sudo systemctl stop cloudflared\n\n'

printf 'Next Steps:\n'
printf '  1. Update MediaCMS .env file:\n'
printf '     FRONTEND_HOST=https://%s\n\n' "$DOMAIN"
printf '  2. Restart MediaCMS:\n'
printf '     docker compose restart web\n\n'
printf '  3. Test access:\n'
printf '     curl -I https://%s\n\n' "$DOMAIN"
printf '  4. Monitor tunnel:\n'
printf '     https://one.dash.cloudflare.com/ -> Access -> Tunnels\n\n'

printf '⚠️  Note: DNS propagation may take a few minutes\n'
