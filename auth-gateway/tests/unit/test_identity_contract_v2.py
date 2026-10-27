import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import OdooCredential, TelegramConnection, User
from app.security import encrypt_secret


def test_resolve_identity_v2_success(tmp_path):
    db_path = tmp_path / "test_gw_v2.db"
    fernet_key = Fernet.generate_key().decode()
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite:///{db_path}",
        ENCRYPTION_KEY=fernet_key,
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_long_1111",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_long_2222",
    )
    app = create_app(settings)
    session_factory = app.state.session_factory

    with session_factory() as db:
        user = User(
            tenant="https://erp.test.com",
            database="test_db",
            odoo_uid=105,
            login="user@test.com",
            name="Nguyen Van A",
            active=True,
        )
        db.add(user)
        db.flush()
        conn = TelegramConnection(
            user_id=user.id,
            telegram_user_id=123456,
            private_chat_id=123456,
            username="user_a",
            binding_id="tg_bind_fixed_1234",
            status="connected",
        )
        cred = OdooCredential(
            user_id=user.id,
            credential_kind="api_key",
            encrypted_secret=encrypt_secret("dummy_key", settings.ENCRYPTION_KEY),
        )
        db.add_all([conn, cred])
        db.commit()

    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    with patch("app.odoo.service.OdooService.validate_user_account", return_value={"id": 105, "active": True, "share": False}):
        resp = client.post(
            "/internal/v1/identity/resolve",
            json={"telegram_user_id": 123456, "chat_id": 123456, "contract_version": 2},
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract_version"] == 2
    assert data["telegram_binding_id"] == "tg_bind_fixed_1234"
    assert "permissions" in data
    assert "authorization_checked_at" in data
    assert "job_title" in data
    assert "department" in data
    assert "manager_name" in data


def test_resolve_identity_v2_not_linked(tmp_path):
    db_path = tmp_path / "test_gw_v2_unlinked.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite:///{db_path}",
        ENCRYPTION_KEY="test_encryption_key_for_unit_tests_32ch=",
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_long_1111",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_long_2222",
    )
    app = create_app(settings)
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    resp = client.post(
        "/internal/v1/identity/resolve",
        json={"telegram_user_id": 999999, "chat_id": 999999, "contract_version": 2},
        headers=headers,
    )
    assert resp.status_code == 404
