"""Root Uvicorn/Gunicorn entrypoint alias for Render deployments (`uvicorn main:app` or `uvicorn app.main:app`)."""
from app.main import app

__all__ = ["app"]
