from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import GoogleConnection, OdooCredential, TelegramConnection, User
from app.security import encrypt_secret


@pytest.fixture
def test_app_and_user(tmp_path):
    db_path = tmp_path / "gw_actions.db"
    fernet_key = Fernet.generate_key().decode()
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite:///{db_path}",
        ENCRYPTION_KEY=fernet_key,
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_long_1111",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_long_2222",
    )
    app = create_app(settings)
    with app.state.session_factory() as db:
        user = User(
            tenant="https://erp.test.com",
            database="test_db",
            odoo_uid=101,
            login="user@test.com",
            name="Test User",
            active=True,
        )
        db.add(user)
        db.flush()
        conn = TelegramConnection(
            user_id=user.id,
            telegram_user_id=777,
            private_chat_id=777,
            username="test_tele",
            binding_id="tg_bind_valid_123",
            status="connected",
        )
        cred = OdooCredential(
            user_id=user.id,
            credential_kind="api_key",
            encrypted_secret=encrypt_secret("odoo_api_key_secret", fernet_key),
        )
        g_conn = GoogleConnection(
            user_id=user.id,
            google_sub="sub_123",
            email="user@gmail.com",
            scopes="https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.send",
            encrypted_access_token=encrypt_secret("access_token", fernet_key),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            status="connected",
        )
        db.add_all([conn, cred, g_conn])
        db.commit()
        user_id = user.id

    with patch("app.odoo.service.OdooService.validate_user_account", return_value={"id": 101, "active": True, "share": False}):
        yield app, user_id


def test_action_rejects_mismatched_binding(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    resp = client.post(
        f"/internal/v1/users/{user_id}/actions",
        json={
            "operation_id": "op_test_1",
            "telegram_user_id": 777,
            "chat_id": 777,
            "telegram_binding_id": "tg_bind_OUTDATED_999",
            "action": "gmail.send",
            "arguments": {"to": ["test@example.com"], "subject": "Test", "body_text": "Hello"},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RECONNECT_REQUIRED"


def test_action_rejects_unpermitted_action(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    # Mock odoo_svc compute_permissions to return NO crm permissions
    with patch("app.odoo.service.OdooService.compute_permissions", return_value=["tasks.read"]):
        resp = client.post(
            f"/internal/v1/users/{user_id}/actions",
            json={
                "operation_id": "op_crm_create_1",
                "telegram_user_id": 777,
                "chat_id": 777,
                "telegram_binding_id": "tg_bind_valid_123",
                "action": "crm.create",
                "arguments": {"name": "New Opportunity"},
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "PERMISSION_DENIED"


def test_crm_leads_endpoint_success(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    with patch("app.odoo.service.OdooService.search_my_leads") as mock_leads:
        mock_leads.return_value = [
            {"id": 10, "name": "Cơ hội A", "stage_id": [1, "Mới"]}
        ]
        resp = client.get(
            f"/internal/v1/users/{user_id}/crm/leads",
            params={"search_term": "Cơ hội", "limit": 5},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "OK"
        assert len(resp.json()["leads"]) == 1


def test_action_calendar_create_success(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    with patch("app.google.client.GoogleClient.create_calendar_event") as mock_cal:
        mock_cal.return_value = {"id": "cal_evt_100", "summary": "Họp tuần"}
        resp = client.post(
            f"/internal/v1/users/{user_id}/actions",
            json={
                "operation_id": "op_cal_1",
                "telegram_user_id": 777,
                "chat_id": 777,
                "telegram_binding_id": "tg_bind_valid_123",
                "action": "calendar.create",
                "arguments": {
                    "summary": "Họp tuần",
                    "start_time": "2026-10-27T09:00:00Z",
                    "end_time": "2026-10-27T10:00:00Z",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] in ["OK", "completed"]
        assert resp.json()["result"]["id"] == "cal_evt_100"


def test_action_gmail_send_success(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    with patch("app.google.client.GoogleClient.send_email") as mock_send:
        mock_send.return_value = {"status": "OK", "id": "msg_123"}
        resp = client.post(
            f"/internal/v1/users/{user_id}/actions",
            json={
                "operation_id": "op_gmail_1",
                "telegram_user_id": 777,
                "chat_id": 777,
                "telegram_binding_id": "tg_bind_valid_123",
                "action": "gmail.send",
                "arguments": {
                    "to": ["customer@example.com"],
                    "subject": "Chào bạn",
                    "body_text": "Cảm ơn bạn đã liên hệ.",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] in ["OK", "completed"]
        data = resp.json()
        assert data.get("id") == "msg_123" or data.get("result", {}).get("id") == "msg_123"


def test_action_crm_create_success(test_app_and_user):
    app, user_id = test_app_and_user
    client = TestClient(app)
    headers = {"Authorization": "Bearer agent_to_gw_secret_32_chars_long_1111"}
    with patch("app.odoo.service.OdooService.compute_permissions", return_value=["crm.create", "crm.write", "crm.read"]), \
         patch("app.odoo.service.OdooService.create_lead") as mock_crm:
        mock_crm.return_value = {"id": 88, "name": "Cơ hội Dự án X"}
        resp = client.post(
            f"/internal/v1/users/{user_id}/actions",
            json={
                "operation_id": "op_crm_create_88",
                "telegram_user_id": 777,
                "chat_id": 777,
                "telegram_binding_id": "tg_bind_valid_123",
                "action": "crm.create",
                "arguments": {
                    "name": "Cơ hội Dự án X",
                    "contact_name": "Anh Ba",
                    "expected_revenue": 50000000.0,
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] in ["OK", "completed"]
        assert resp.json()["result"]["id"] == 88
