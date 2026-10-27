import hashlib
import secrets
from datetime import UTC

from cryptography.fernet import Fernet


def encrypt_secret(plain_text: str, key: str) -> str:
    f = Fernet(key.encode() if isinstance(key, str) else key)
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_text: str, key: str) -> str:
    f = Fernet(key.encode() if isinstance(key, str) else key)
    return f.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(token: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_token(token), expected_hash)


def generate_secure_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def redact_secret(secret: str | None) -> str:
    if not secret:
        return "[NONE]"
    if len(secret) <= 8:
        return "[REDACTED]"
    return f"{secret[:4]}...{secret[-4:]}"


def ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
