# CloudFlare Tunnel Design for MediaCMS Frontend

## Overview

Use CloudFlare Tunnel (cloudflared) to securely expose MediaCMS frontend without opening firewall ports or dealing with the existing xray process on port 443.

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   Browser   │────────▶│  CloudFlare CDN  │────────▶│ CloudFlare Edge │
└─────────────┘         └──────────────────┘         └─────────────────┘
                              HTTPS                           │
                                                              │ Tunnel
                                                              │
                                                              ▼
                        ┌───────────────────────────────────────────┐
                        │     Production Server (localhost)         │
                        │                                           │
                        │  ┌──────────────┐                        │
                        │  │  cloudflared │ (tunnel daemon)         │
                        │  └───────┬──────┘                        │
                        │          │ http://localhost:8080         │
                        │          ▼                                │
                        │  ┌──────────────┐                        │
                        │  │ MediaCMS Web │ :8080                  │
                        │  │  (Docker)    │                        │
                        │  └──────────────┘                        │
                        │                                           │
                        │  Port 443: xray (untouched)              │
                        └───────────────────────────────────────────┘
```

## Benefits

1. **No Port Conflicts:** Bypasses xray on port 443
2. **No Firewall Changes:** Outbound tunnel only (port 7844)
3. **Free SSL:** CloudFlare handles certificates automatically
4. **DDoS Protection:** CloudFlare's network protects your server
5. **Easy Management:** Web UI for tunnel configuration
6. **Zero Trust:** Optional access policies and authentication

## Setup Steps

### Prerequisites

- CloudFlare account with a domain
- Domain DNS managed by CloudFlare
- Docker running MediaCMS on port 8080

### Step 1: Install cloudflared

```bash
# Download and install cloudflared
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Verify installation
cloudflared --version
```

### Step 2: Authenticate with CloudFlare

```bash
# Login (opens browser for authentication)
cloudflared tunnel login

# This creates: ~/.cloudflared/cert.pem
```

### Step 3: Create Tunnel

```bash
# Create a named tunnel
cloudflared tunnel create mediacms

# This creates:
# - Tunnel UUID and credentials at ~/.cloudflared/<UUID>.json
# - Tunnel registered in your CloudFlare account

# Save the tunnel ID for later
TUNNEL_ID=$(cloudflared tunnel list | grep mediacms | awk '{print $1}')
echo "Tunnel ID: $TUNNEL_ID"
```

### Step 4: Configure DNS

```bash
# Create DNS record pointing to the tunnel
# Replace 'mediacms.yourdomain.com' with your actual domain
cloudflared tunnel route dns mediacms mediacms.yourdomain.com

# This creates a CNAME record in CloudFlare DNS:
# mediacms.yourdomain.com -> <UUID>.cfargotunnel.com
```

### Step 5: Create Tunnel Configuration

```bash
# Create config file
sudo mkdir -p /etc/cloudflared
sudo tee /etc/cloudflared/config.yml <<EOF
tunnel: mediacms
credentials-file: /root/.cloudflared/${TUNNEL_ID}.json

ingress:
  # Route for your MediaCMS domain
  - hostname: mediacms.yourdomain.com
    service: http://localhost:8080
    originRequest:
      # Pass the original host header
      noTLSVerify: false
      connectTimeout: 30s
      
  # Catch-all rule (required)
  - service: http_status:404
EOF
```

### Step 6: Test Tunnel

```bash
# Start tunnel in foreground (test mode)
cloudflared tunnel --config /etc/cloudflared/config.yml run mediacms

# In another terminal, check if MediaCMS is accessible
curl -I https://mediacms.yourdomain.com
```

### Step 7: Install as System Service

```bash
# Install service
sudo cloudflared service install

# Start service
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# Check status
sudo systemctl status cloudflared

# View logs
sudo journalctl -u cloudflared -f
```

## Configuration Options

### Multiple Domains

To expose multiple subdomains (e.g., admin panel, API):

```yaml
tunnel: mediacms
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  # Main frontend
  - hostname: mediacms.yourdomain.com
    service: http://localhost:8080
    
  # Admin panel (if separate)
  - hostname: admin.mediacms.yourdomain.com
    service: http://localhost:8080/admin
    
  # API endpoint
  - hostname: api.mediacms.yourdomain.com
    service: http://localhost:8080/api
    
  # Catch-all
  - service: http_status:404
```

### Access Control

Add CloudFlare Access for authentication:

```yaml
tunnel: mediacms
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  - hostname: mediacms.yourdomain.com
    service: http://localhost:8080
    originRequest:
      # CloudFlare Access will handle authentication
      access:
        required: true
        teamName: your-team-name
        
  - service: http_status:404
```

### WebSocket Support

If MediaCMS uses WebSockets (e.g., for real-time features):

```yaml
tunnel: mediacms
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  - hostname: mediacms.yourdomain.com
    service: http://localhost:8080
    originRequest:
      noTLSVerify: false
      http2Origin: true
      # WebSocket support is enabled by default
      
  - service: http_status:404
```

## Environment Configuration

Update your `.env` file to use the CloudFlare domain:

```bash
# In .env.dev or .env.production
FRONTEND_HOST=https://mediacms.yourdomain.com

# Optional: Trust CloudFlare IPs for X-Forwarded-For
# ALLOWED_HOSTS=mediacms.yourdomain.com
```

## Docker Compose Integration (Optional)

Run cloudflared as a Docker container alongside MediaCMS:

```yaml
# Add to docker-compose.production.yaml

services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: mediacms-tunnel
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    networks:
      - mediacms-network
    depends_on:
      - web

  web:
    # ... existing web service config
```

To get the tunnel token:
```bash
cloudflared tunnel token mediacms
```

## Monitoring

### Check Tunnel Status

```bash
# Via CLI
cloudflared tunnel info mediacms

# Via systemd
sudo systemctl status cloudflared

# View logs
sudo journalctl -u cloudflared -f
```

### CloudFlare Dashboard

1. Go to https://one.dash.cloudflare.com/
2. Navigate to **Access** → **Tunnels**
3. View tunnel status, traffic, and metrics

## Troubleshooting

### Tunnel Not Connecting

```bash
# Check service status
sudo systemctl status cloudflared

# Check logs for errors
sudo journalctl -u cloudflared --since "10 minutes ago"

# Test connectivity
cloudflared tunnel --config /etc/cloudflared/config.yml run mediacms
```

### 502 Bad Gateway

- MediaCMS not running on port 8080
- Check: `docker compose ps` and `curl http://localhost:8080`

### DNS Not Resolving

```bash
# Verify DNS record
dig mediacms.yourdomain.com

# Should return CNAME to *.cfargotunnel.com
# May take a few minutes to propagate
```

### Connection Timeout

```bash
# Increase timeout in config.yml
ingress:
  - hostname: mediacms.yourdomain.com
    service: http://localhost:8080
    originRequest:
      connectTimeout: 60s
      keepAliveTimeout: 90s
```

## Security Considerations

1. **Credentials:** Protect `~/.cloudflared/*.json` files (contain tunnel credentials)
2. **Rate Limiting:** Enable in CloudFlare dashboard to prevent abuse
3. **WAF Rules:** Configure CloudFlare WAF for additional protection
4. **Access Policies:** Use CloudFlare Access for authentication if needed
5. **Local Binding:** Keep MediaCMS bound to localhost:8080 (not 0.0.0.0:8080)

## Comparison with Traditional Reverse Proxy

| Feature | CloudFlare Tunnel | Traditional (nginx/xray) |
|---------|------------------|-------------------------|
| SSL Certificates | Automatic | Manual/Let's Encrypt |
| Port Conflicts | None (outbound only) | Must manage 80/443 |
| Firewall | No inbound rules | Open 80/443 |
| DDoS Protection | CloudFlare network | Self-managed |
| Configuration | Simple YAML | Complex configs |
| Management | Web UI + CLI | SSH + config files |

## Cost

- **CloudFlare Tunnel:** FREE (included with free CloudFlare plan)
- **Bandwidth:** Unlimited (CloudFlare absorbs DDoS)
- **Zero Trust Features:** Free tier available, Pro for advanced features

## Rollback Plan

If CloudFlare Tunnel causes issues:

```bash
# Stop tunnel
sudo systemctl stop cloudflared
sudo systemctl disable cloudflared

# Remove DNS record in CloudFlare dashboard
# Or via CLI:
cloudflared tunnel route dns delete mediacms mediacms.yourdomain.com

# MediaCMS still accessible on http://localhost:8080
# Configure traditional reverse proxy if needed
```

## Next Steps

1. **Install cloudflared** on production server
2. **Create tunnel** with your CloudFlare account
3. **Configure DNS** to point to tunnel
4. **Update .env** with CloudFlare domain
5. **Test access** via https://mediacms.yourdomain.com
6. **Enable monitoring** in CloudFlare dashboard
7. **Optional:** Configure CloudFlare Access for authentication

## References

- CloudFlare Tunnel Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- cloudflared GitHub: https://github.com/cloudflare/cloudflared
- CloudFlare Zero Trust: https://developers.cloudflare.com/cloudflare-one/
