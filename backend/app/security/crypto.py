import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings


def _derive_fernet_key() -> bytes:
    raw = settings.VPS_ENCRYPTION_KEY.strip()
    if not raw or raw.startswith("CHANGE_ME"):
        fallback = settings.SECRET_KEY.strip()
        if not fallback or fallback.startswith("CHANGE_ME"):
            raise RuntimeError(
                "VPS_ENCRYPTION_KEY (or SECRET_KEY) must be configured in environment variables"
            )
        raw = f"vps-enc::{fallback}"
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
        if len(decoded) == 32:
            return raw.encode("utf-8")
    except Exception:
        pass
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    fernet = Fernet(_derive_fernet_key())
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    fernet = Fernet(_derive_fernet_key())
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt protected secret material") from exc


def mask_secret(secret_value: str) -> str:
    if not secret_value:
        return ""
    if len(secret_value) <= 8:
        return "*" * len(secret_value)
    return f"{secret_value[:4]}...{secret_value[-4:]}"
