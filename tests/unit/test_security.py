import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.security import (
    decrypt_secret,
    encrypt_secret,
    hash_token,
    redact_secret,
    verify_token_hash,
)


def test_encryption_roundtrip(fernet_key):
    secret = "my_super_secret_odoo_password_123!"
    encrypted = encrypt_secret(secret, fernet_key)
    assert encrypted != secret
    assert isinstance(encrypted, str)

    decrypted = decrypt_secret(encrypted, fernet_key)
    assert decrypted == secret


def test_decryption_with_wrong_key(fernet_key):
    secret = "another_secret"
    encrypted = encrypt_secret(secret, fernet_key)

    different_key = Fernet.generate_key().decode()
    with pytest.raises(InvalidToken):
        decrypt_secret(encrypted, different_key)


def test_hash_token_sha256():
    token = "random_session_token_xyz"
    hashed = hash_token(token)
    assert len(hashed) == 64  # SHA-256 hex digest
    assert verify_token_hash(token, hashed) is True
    assert verify_token_hash("wrong_token", hashed) is False


def test_redact_secret():
    assert redact_secret("short") == "[REDACTED]"
    assert redact_secret("my_very_long_secret_key_123456") == "my_v...3456"
    assert redact_secret(None) == "[NONE]"
