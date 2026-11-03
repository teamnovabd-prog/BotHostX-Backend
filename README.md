# BotHostX (SmartHost — Mobile Hosting Platform)

Complete mobile cloud hosting platform allowing users to deploy and manage isolated **Python Bots**, **Telegram Bots**, **PHP Scripts**, and **HTML Websites** from Android, backed by **FastAPI + PostgreSQL + Docker** and a full **Multi-VPS Admin Management System**.

## Repository Structure

- `app/` — Android mobile client (`com.bothost.mobile`) built with Kotlin, Jetpack Compose, Material 3, MVVM, Navigation Compose, Retrofit/OkHttp/Moshi, Coroutines, DataStore, Coil, and Android File Picker.
- `New/BotHostX.apk` — Compiled Android APK ready for direct download/installation.
- `backend/` — Production Python FastAPI REST API, SQLAlchemy 2.0 ORM (23 models), Alembic migrations, Fernet secret encryption (`VPS_ENCRYPTION_KEY`), pinned-host-key SSH + HMAC-signed VPS Agent client, resource-aware VPS selector, 10-step safe VPS migrator, and background health worker.
- `vps-agent/` — Authenticated Docker isolation agent deployed on worker Linux VPS servers.
- `DEPLOYMENT.md` — Full step-by-step Linux VPS, PostgreSQL, HTTPS, wildcard domain, and Multi-VPS setup guide.

## Configuring the Android App API Endpoint

1. Deploy `backend/` to your Linux server following `DEPLOYMENT.md` and configure your private `backend/.env` (including `ADMIN_EMAIL=teamnovabd@gmail.com` and your private `ADMIN_PASSWORD`).
2. Launch **BotHostX** on Android, tap the **Server Endpoint** button on Onboarding, Login, or Settings, enter your HTTPS API URL (e.g., `https://api.yourdomain.com`), and tap **Verify & Save**.
3. Sign in as a regular user or sign in with `teamnovabd@gmail.com` to access the **SmartHost Admin Panel** and **Multi-VPS Management**.
