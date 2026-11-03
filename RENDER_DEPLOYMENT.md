# Deploying SmartHost / BotHostX FastAPI Backend + PostgreSQL on Render

Why you previously saw `"Health probe failed: Not Found"`:
- Your Render Web Service was running a starter FastAPI file that only exposed `GET /` returning `{"message": "Hello World"}`, without `GET /health`, `POST /api/auth/register`, or `POST /api/auth/login`.
- The BotHostX Android app now checks **`GET /health`** directly, verifies PostgreSQL & authentication readiness (`status == "ok"` and `auth_ready == true`), and rejects starter `"Hello World"` apps with a clear diagnostic message.

---

## Option A — 1-Click Render Blueprint Deployment (Recommended)

1. Push this repository (including `render.yaml` and `backend/`) to your GitHub account.
2. Log in to [Render Dashboard](https://dashboard.render.com) -> **New +** -> **Blueprint**.
3. Connect your GitHub repository. Render will automatically detect `render.yaml` and create:
   - **`smarthost-postgres`** (Managed PostgreSQL database)
   - **`smarthost-api`** (Python 3.12 FastAPI Web Service with `Root Directory = backend` and `Health Check Path = /health`)
4. When prompted for **`ADMIN_PASSWORD`**, enter your private password for `teamnovabd@gmail.com` (never commit this password to Git).
5. Click **Apply**. Once deployed, copy your Render URL (e.g., `https://smarthost-api.onrender.com`), open the **BotHostX** Android app -> **API Endpoint**, paste the URL, and tap **Verify & Save**.

---

## Option B — Manual Render Web Service + PostgreSQL Setup

### Step 1: Create Render PostgreSQL Database
1. In Render Dashboard, click **New +** -> **PostgreSQL**.
2. Set **Name**: `smarthost-db`, **Database**: `smarthost`, **User**: `smarthost_user`.
3. Create the database and copy the **Internal Database URL** (starts with `postgres://...` or `postgresql://...` — `backend/app/config.py` automatically normalizes both for SQLAlchemy 2.0).

### Step 2: Configure Render FastAPI Web Service
1. Click **New +** -> **Web Service** and connect your GitHub repo (or update your existing Render Web Service under **Settings**).
2. Configure these exact settings:
   - **Runtime**: `Python 3`
   - **Root Directory**: `backend`
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
3. Under **Environment**, add these Environment Variables:
   - `PYTHON_VERSION` = `3.12.7`
   - `DATABASE_URL` = *(Paste your Render PostgreSQL Internal Database URL)*
   - `SECRET_KEY` = *(Generate a 64-char random hex string, e.g. `openssl rand -hex 32`)*
   - `VPS_ENCRYPTION_KEY` = *(Generate a random secret or Fernet key)*
   - `ADMIN_EMAIL` = `teamnovabd@gmail.com`
   - `ADMIN_PASSWORD` = *(Your private initial Admin password)*
   - `APP_NAME` = `BotHostX`
4. Click **Save Changes** -> **Manual Deploy** -> **Deploy latest commit**.

### Step 3: Verify Deployment Before Testing in Android App
Open `https://<your-render-service>.onrender.com/health` in a browser. You must see:
```json
{
  "status": "ok",
  "service": "smarthost-api",
  "platform": "BotHostX",
  "auth_ready": true,
  "database": "connected",
  "maintenance_mode": false
}
```
Once `/health` returns the JSON above, enter `https://<your-render-service>.onrender.com` in the BotHostX Android app and tap **Verify & Save** — Registration (`POST /api/auth/register`) and Login (`POST /api/auth/login`) will work immediately against PostgreSQL.
