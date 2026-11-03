# BotHostX (SmartHost) — Production Linux VPS Deployment Guide

> **Important Production Notice**: Nothing is falsely marked as deployed out of the box. You must deploy the Central Backend on your control-plane Linux server, purchase/provision one or more real Linux VPS worker servers separately, install the Secure VPS Agent + Docker runtime on each worker VPS, and register/verify them inside the **Admin Panel -> VPS Management** screen before user containers can run.

---

## 1. Central Control-Plane Server Prerequisites (Ubuntu 22.04 / 24.04 LTS)

### Python, PostgreSQL & Docker Setup
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx redis-server ufw

# Install Docker & Docker Compose Plugin
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker postgresql redis-server
```

### PostgreSQL Database Setup
```bash
sudo -u postgres psql <<'SQL'
CREATE USER smarthost_user WITH PASSWORD 'STRONG_DB_PASSWORD_HERE';
CREATE DATABASE smarthost OWNER smarthost_user;
GRANT ALL PRIVILEGES ON DATABASE smarthost TO smarthost_user;
SQL
```

---

## 2. Backend Installation & Private `.env` Configuration

```bash
sudo mkdir -p /opt/smarthost /var/lib/smarthost/staging
sudo chown -R $USER:$USER /opt/smarthost /var/lib/smarthost
cp -r backend/* /opt/smarthost/
cd /opt/smarthost

python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create `/opt/smarthost/.env` (keep `chmod 600 .env` — **never** commit `.env` to Git or include secrets in the Android APK):
```bash
cp .env.example .env
chmod 600 .env
```
Populate `.env`:
- `DATABASE_URL=postgresql://smarthost_user:STRONG_DB_PASSWORD_HERE@localhost:5432/smarthost`
- `SECRET_KEY=$(openssl rand -hex 32)`
- `VPS_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")`
- `ADMIN_EMAIL=teamnovabd@gmail.com`
- `ADMIN_PASSWORD=<YOUR_PRIVATE_INITIAL_ADMIN_PASSWORD>`
- `API_BASE_URL=https://api.yourdomain.com`
- `HOSTING_DOMAIN=yourdomain.com`

> On first startup, `bootstrap_initial_admin_and_settings()` checks if `teamnovabd@gmail.com` exists. If not, it hashes `ADMIN_PASSWORD` with bcrypt and creates the Admin account. Subsequent restarts **never** overwrite the Admin password, and the Admin can change their password inside the app anytime.

---

## 3. Database Migration & Systemd Services (API + Worker)

```bash
cd /opt/smarthost
source .venv/bin/activate
alembic upgrade head
```

### FastAPI Service (`/etc/systemd/system/smarthost-api.service`)
```ini
[Unit]
Description=SmartHost / BotHostX FastAPI Backend
After=network.target postgresql.service redis-server.service

[Service]
User=www-data
WorkingDirectory=/opt/smarthost
EnvironmentFile=/opt/smarthost/.env
ExecStart=/opt/smarthost/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Background Worker Service (`/etc/systemd/system/smarthost-worker.service`)
```ini
[Unit]
Description=SmartHost Multi-VPS Health & Expiry Worker
After=network.target postgresql.service smarthost-api.service

[Service]
User=www-data
WorkingDirectory=/opt/smarthost
EnvironmentFile=/opt/smarthost/.env
ExecStart=/opt/smarthost/.venv/bin/python -m app.workers.worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /opt/smarthost /var/lib/smarthost
sudo systemctl daemon-reload
sudo systemctl enable --now smarthost-api smarthost-worker
```

---

## 4. Nginx Reverse Proxy, HTTPS & Wildcard Hosting Subdomains

1. Point DNS `A` record `api.yourdomain.com` -> Central Backend IP.
2. For PHP/HTML user websites (`https://slug.yourdomain.com`), configure wildcard DNS `*.yourdomain.com` -> your Ingress / Worker VPS Reverse Proxy IP and issue a wildcard TLS certificate via Certbot DNS plugin.

`/etc/nginx/sites-available/smarthost-api`:
```nginx
server {
    listen 80;
    server_name api.yourdomain.com;
    client_max_body_size 60M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/smarthost-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.yourdomain.com
```

---

## 5. Worker VPS Provisioning, Secure VPS Agent & Registration

1. Provision a separate Linux VPS (DigitalOcean, Hetzner, Vultr, AWS Lightsail, etc.).
2. Install Docker Engine and pull isolated runtime images:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo docker pull python:3.12-slim
   sudo docker pull php:8.3-apache
   sudo docker pull nginx:1.27-alpine
   ```
3. Deploy `vps-agent/` on the Worker VPS with a strong `VPS_AGENT_TOKEN` (`openssl rand -hex 32`) and TLS certificate, restricting port `9443` via UFW exclusively to your Central Backend IP:
   ```bash
   sudo ufw allow from <CENTRAL_BACKEND_IP> to any port 9443 proto tcp
   ```
4. If using SSH mode instead of/alongside Agent mode, obtain the exact SSH host key fingerprint on the Worker VPS:
   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub -E sha256
   ```
5. Open the **BotHostX Android App**, configure your API Base URL (`https://api.yourdomain.com`), sign in as Admin (`teamnovabd@gmail.com`), open **Admin Panel -> VPS Management -> Add VPS**, enter the server details + Agent Token / SSH Key + SHA256 Host Fingerprint, and tap **Test Connection**.
6. Only after the real health probe confirms OS & Docker availability will the VPS transition from `Setup Required` to `Online` and become eligible for automatic or manual hosting assignment and 10-step verified VPS migration.
