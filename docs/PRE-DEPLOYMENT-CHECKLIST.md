# MediaCMS Pre-Deployment Checklist

**Domain:** mediacms.ygcyj.xin  
**Date:** 2026-08-03  
**Status:** Ready for deployment

## ✅ Infrastructure Ready

### 1. AWS Resources (CloudFormation Stack: mediacms-dev)
- [x] S3 Bucket for media storage
- [x] RDS PostgreSQL database
- [x] IAM credentials configured
- [ ] Verify credentials in `.env` file

**Action Required:**
```bash
cd /root/project/mediacms
./deploy/scripts/generate_docker_env_from_stack.sh mediacms-dev > .env
```

### 2. CloudFlare Tunnel
- [x] Tunnel running: `mattermost` (faa2b453-bd9f-4eef-a3c9-a92330ff23a2)
- [x] Configuration updated: `/etc/cloudflared/config.yml`
- [x] DNS record created: `mediacms.ygcyj.xin`
- [x] Service restarted: `cloudflared.service`
- [ ] DNS propagation (wait 1-5 minutes)

**Verify:**
```bash
systemctl status cloudflared
curl -I https://mediacms.ygcyj.xin
# Expected: 502 Bad Gateway (MediaCMS not running yet)
```

### 3. Docker Images
- [ ] Check if images are available locally
- [ ] Or pull from registry

**Check:**
```bash
docker images | grep mediacms
```

## 📋 Deployment Steps

### Step 1: Generate Configuration
```bash
cd /root/project/mediacms

# Generate from CloudFormation stack
./deploy/scripts/generate_docker_env_from_stack.sh mediacms-dev > .env

# Review and update
vi .env
```

**Required Updates in .env:**
- `POSTGRES_PASSWORD=` (set secure password)
- `SECRET_KEY=` (set secure random key)
- `ADMIN_USER=admin`
- `ADMIN_EMAIL=admin@ygcyj.xin`
- `ADMIN_PASSWORD=` (set secure password)
- `FRONTEND_HOST=https://mediacms.ygcyj.xin`

### Step 2: Start Services
```bash
# Start all services
docker compose up -d

# Wait for services to be healthy (30-60 seconds)
watch -n 2 'docker compose ps'
```

**Expected:**
- `db` - healthy (PostgreSQL)
- `redis` - healthy
- `web` - healthy (Django)
- `celery_beat` - running
- `celery_worker` - running
- `migrations` - completed/exited

### Step 3: Run Database Migrations
```bash
# Run migrations
docker compose exec web python manage.py migrate

# Collect static files
docker compose exec web python manage.py collectstatic --no-input

# Verify
docker compose exec web python manage.py showmigrations
```

### Step 4: Create Admin User
```bash
docker compose exec web python manage.py createsuperuser
# Follow prompts or use environment variables
```

### Step 5: Verify Application
```bash
# Health check
curl http://localhost:8080/health

# Check logs
docker compose logs web --tail=50

# Test frontend
curl -I http://localhost:8080
```

### Step 6: Test Through CloudFlare Tunnel
```bash
# Wait 2-3 minutes for DNS propagation
sleep 180

# Test HTTPS
curl -I https://mediacms.ygcyj.xin

# Expected: 200 OK or 302 redirect
```

### Step 7: Browser Test
Open in browser:
- **Frontend:** https://mediacms.ygcyj.xin
- **Admin:** https://mediacms.ygcyj.xin/admin
- **Login:** Use admin credentials from Step 4

### Step 8: Upload Test Media
1. Login to admin panel
2. Upload a test image
3. Verify it appears on S3 bucket
4. Verify thumbnail generation (celery worker)

## 🔍 Verification Checklist

### Application Layer
- [ ] Web server responding on :8080
- [ ] Admin panel accessible
- [ ] User login works
- [ ] Static files loading
- [ ] API endpoints responding

### Database Layer
- [ ] PostgreSQL healthy
- [ ] Migrations applied
- [ ] Admin user created
- [ ] Database connection from web

### Storage Layer
- [ ] S3 bucket accessible
- [ ] AWS credentials valid
- [ ] File upload works
- [ ] Thumbnail generation works

### Worker Layer
- [ ] Redis healthy
- [ ] Celery worker running
- [ ] Celery beat running
- [ ] Background tasks executing

### Network Layer
- [ ] CloudFlare tunnel active
- [ ] DNS resolving correctly
- [ ] HTTPS working
- [ ] No SSL errors

## 🚨 Troubleshooting

### Services Won't Start
```bash
# Check logs
docker compose logs

# Check specific service
docker compose logs web

# Recreate services
docker compose down
docker compose up -d
```

### Database Connection Errors
```bash
# Verify PostgreSQL
docker compose ps db
docker compose logs db

# Test connection
docker compose exec web python manage.py dbshell
```

### AWS/S3 Access Errors
```bash
# Check credentials
docker compose exec web python manage.py shell
>>> from django.core.files.storage import default_storage
>>> default_storage.bucket.name
>>> default_storage.connection.meta.client.list_buckets()
```

### CloudFlare Tunnel 502
```bash
# Tunnel status
systemctl status cloudflared
journalctl -u cloudflared -f

# Test origin
curl http://localhost:8080/health

# Restart tunnel
systemctl restart cloudflared
```

### DNS Not Resolving
```bash
# Check CloudFlare dashboard
# https://dash.cloudflare.com/

# Force flush
cloudflared tunnel route dns mattermost mediacms.ygcyj.xin

# Wait 5 minutes for propagation
```

## 📊 Monitoring

### Real-time Logs
```bash
# All services
docker compose logs -f

# Web only
docker compose logs -f web

# Workers
docker compose logs -f celery_worker celery_beat

# Tunnel
journalctl -u cloudflared -f
```

### Resource Usage
```bash
# Container stats
docker stats

# Service health
docker compose ps

# System resources
top
```

### CloudFlare Dashboard
- **URL:** https://one.dash.cloudflare.com/
- **Navigate to:** Access → Tunnels → mattermost
- **Metrics:** Requests, bandwidth, errors

## 🎯 Success Criteria

All checks must pass:
- [x] CloudFormation stack: mediacms-dev (READY)
- [x] CloudFlare Tunnel configured (READY)
- [x] DNS record created (READY)
- [ ] Docker services running (PENDING)
- [ ] Database migrations applied (PENDING)
- [ ] Admin user created (PENDING)
- [ ] Web accessible via HTTPS (PENDING)
- [ ] Media upload working (PENDING)

## 📝 Next Steps

1. **Generate .env:**
   ```bash
   ./deploy/scripts/generate_docker_env_from_stack.sh mediacms-dev > .env
   ```

2. **Review and update passwords:**
   ```bash
   vi .env
   ```

3. **Start deployment:**
   ```bash
   docker compose up -d
   ```

4. **Follow checklist above** ☝️

## 🔗 Quick Links

- **Documentation:**
  - [Quick Start](QUICKSTART.md)
  - [Production Guide](PRODUCTION-DEPLOYMENT-GUIDE.md)
  - [CloudFlare Tunnel](CLOUDFLARE-TUNNEL-SUMMARY.md)

- **Configuration Files:**
  - `.env` - Main configuration
  - `docker-compose.yaml` - Service definitions
  - `/etc/cloudflared/config.yml` - Tunnel config

- **CloudFlare:**
  - Dashboard: https://one.dash.cloudflare.com/
  - DNS: https://dash.cloudflare.com/

---

**Ready to deploy!** Follow Step 1 to begin. 🚀
