# Quick Start: Deploy MediaCMS with Existing CloudFormation Stack

If you already have a CloudFormation stack deployed (`mediacms-dev` or `mediacms-prod`), use this guide to quickly generate Docker configuration and start services.

## Prerequisites

- CloudFormation stack deployed (check with: `aws cloudformation list-stacks`)
- Docker and Docker Compose installed
- AWS CLI configured with `default` profile

## Step 1: Generate Environment File from Stack

```bash
cd /root/project/mediacms

# For dev stack:
./deploy/scripts/generate_docker_env_from_stack.sh mediacms-dev .env.dev https://your-domain.com

# For prod stack:
./deploy/scripts/generate_docker_env_from_stack.sh mediacms-prod .env.production https://your-domain.com
```

This automatically extracts:
- AWS credentials from Secrets Manager
- S3 bucket name
- MediaConvert role ARN and templates
- CloudFront distribution settings

## Step 2: Update Required Values

Edit the generated `.env.dev` or `.env.production`:

```bash
# 1. Set image tag (replace :latest)
MEDIACMS_IMAGE=ghcr.io/caoyjie/mediacms:bed7a63

# 2. Generate secret key
SECRET_KEY=$(openssl rand -base64 48)

# 3. Set strong passwords
ADMIN_PASSWORD=<your-strong-password>
POSTGRES_PASSWORD=<your-strong-db-password>

# 4. Update domain
FRONTEND_HOST=https://your-actual-domain.com
```

## Step 3: Choose Compose File

**For development (with source code mounts):**
```bash
ln -sf .env.dev .env
docker compose up -d
```

**For production (no source mounts, immutable images):**
```bash
ln -sf .env.production .env
docker compose -f docker-compose.production.yaml up -d
```

## Step 4: Run Migrations

```bash
# Development
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --no-input

# Production
docker compose -f docker-compose.production.yaml --project-name mediacms-prod \
    exec web python manage.py migrate
docker compose -f docker-compose.production.yaml --project-name mediacms-prod \
    exec web python manage.py collectstatic --no-input
```

## Step 5: Create Admin User

```bash
# Development
docker compose exec web python manage.py createsuperuser

# Production
docker compose -f docker-compose.production.yaml --project-name mediacms-prod \
    exec web python manage.py shell -c "
from django.contrib.auth import get_user_model;
User = get_user_model();
User.objects.create_superuser(
    username='${ADMIN_USER}',
    email='${ADMIN_EMAIL}',
    password='${ADMIN_PASSWORD}'
) if not User.objects.filter(username='${ADMIN_USER}').exists() else None"
```

## Step 6: Verify Services

```bash
# Check all services are healthy
docker compose ps

# Check logs
docker compose logs -f web

# Test health endpoint
curl http://localhost:8080/health
```

## Step 7: Expose Frontend (Choose One)

### Option A: CloudFlare Tunnel (Recommended)

Easiest way to expose MediaCMS without port conflicts or firewall changes:

```bash
# Interactive setup
make -f Makefile.production tunnel-setup

# Or manually
./deploy/scripts/setup_cloudflare_tunnel.sh mediacms.yourdomain.com mediacms

# Check status
make -f Makefile.production tunnel-status

# View logs
make -f Makefile.production tunnel-logs
```

**Benefits:**
- No port 443 conflict with xray
- Free SSL certificates
- DDoS protection
- No firewall configuration needed

See [CLOUDFLARE-TUNNEL-DESIGN.md](CLOUDFLARE-TUNNEL-DESIGN.md) for details.

### Option B: Traditional Reverse Proxy

Configure nginx/xray to forward traffic to port 8080:

```nginx
# Example nginx config
location / {
    proxy_pass http://localhost:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Troubleshooting

### Services won't start
```bash
docker compose logs <service-name>
```

### Database connection errors
- Check `POSTGRES_HOST=db` (not localhost)
- Verify PostgreSQL is healthy: `docker compose ps db`

### AWS access denied
- Verify credentials were extracted correctly from stack
- Check IAM permissions in CloudFormation stack

### Port conflicts
- Port 8080 should be free (or change `MEDIACMS_WEB_PORT`)
- xray owns port 443 on production - reverse proxy needed

## Next Steps

- Configure reverse proxy (xray/nginx/cloudflare) to route external traffic to port 8080
- Set up CloudFront private key for signed URLs (optional, for private videos)
- Review full deployment guide: `docs/PRODUCTION-DEPLOYMENT-GUIDE.md`

## Using the Makefile (Alternative)

For production deployment with all safety checks:

```bash
make -f Makefile.production help
make -f Makefile.production task5-pull-images
make -f Makefile.production task5-start-services
```

See `Makefile.production` for all available targets.
