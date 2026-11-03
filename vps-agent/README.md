# SmartHost / BotHostX — Secure VPS Agent

This service runs on each remote Linux VPS added by the SmartHost Administrator. It never exposes an unauthenticated management port: every request from the central SmartHost Backend must carry:
1. TLS encryption (`https://<vps-ip-or-host>:9443`)
2. `Authorization: Bearer <VPS_AGENT_TOKEN>`
3. `X-SmartHost-Timestamp` (120-second replay window)
4. `X-SmartHost-Signature` (HMAC-SHA256 over `<timestamp>.<raw_body>`)

## Setup on Each Target Linux VPS

```bash
# 1. Install Docker Engine
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 2. Pre-pull isolated hosting runtime images
docker pull python:3.12-slim
docker pull php:8.3-apache
docker pull nginx:1.27-alpine

# 3. Generate a 48-byte random Agent Token (paste this into Admin Panel -> Add VPS -> Agent Token)
openssl rand -hex 32

# 4. Run the VPS Agent behind TLS + firewall restricted to your Central Backend IP
ufw allow from <CENTRAL_BACKEND_PUBLIC_IP> to any port 9443 proto tcp
```
