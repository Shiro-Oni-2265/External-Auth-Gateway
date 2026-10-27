import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, UTC, timedelta
from cryptography.fernet import Fernet
import httpx

from app.config import Settings
from app.main import create_app
from app.models import ActionOperation, GoogleConnection, OdooCredential, TelegramConnection, User
from app.security import encrypt_secret


@pytest.fixture
def test_setup(tmp_path):
    db_path = tmp_path / "idempotency.db"
    fernet_key = Fernet.generate_key().decode()
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite:///{db_path}",
        ENCRYPTION_KEY=fernet_key,
        AGENT_TO_GATEWAY_SECRET="secret_agent_gateway_12345678901234567890",
        GATEWAY_TO_AGENT_SECRET="secret_gateway_agent_12345678901234567890",
    )
    app = create_app(settings)
    client = TestClient(app)

    with app.state.session_factory() as db:
        user = User(
            tenant="https://odoo.demo.local",
            odoo_uid=10,
            login="user@example.com",
            name="Test Idempotency User",
            active=True,
            database="demo_db",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        cred = OdooCredential(
            user_id=user.id,
            credential_kind="api_key",
            encrypted_secret=encrypt_secret("api_key_123", fernet_key),
        )
        db.add(cred)

        tg = TelegramConnection(
            user_id=user.id,
            telegram_user_id=777,
            private_chat_id=777,
            binding_id="tg_bind_idem_123",
            status="connected",
        )
        db.add(tg)

        google_conn = GoogleConnection(
            user_id=user.id,
            google_sub="sub_idem",
            email="test@gmail.com",
            scopes="https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.send",
            status="connected",
            encrypted_access_token=encrypt_secret("enc_access", fernet_key),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(google_conn)
        db.commit()

        user_id = user.id

    with patch("app.odoo.service.OdooService.validate_user_account", return_value={"id": 10, "active": True, "share": False}):
        yield client, settings, app.state.session_factory, user_id


def test_action_idempotency_returns_cached_result(test_setup):
    client, settings, session_factory, user_id = test_setup
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    payload = {
        "operation_id": "op_test_idem_001",
        "telegram_user_id": 777,
        "chat_id": 777,
        "telegram_binding_id": "tg_bind_idem_123",
        "action": "calendar.create",
        "arguments": {
            "summary": "Họp kế hoạch",
            "start_time": "2026-10-27T10:00:00Z",
            "end_time": "2026-10-27T11:00:00Z",
        },
    }

    mock_google = MagicMock()
    mock_google.create_calendar_event.return_value = {"id": "cal_ev_123", "summary": "Họp kế hoạch"}

    with patch("app.internal.routes._get_google_client", return_value=mock_google):
        # Lần 1: Thực thi thành công
        res1 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload, headers=headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "completed"
        assert data1["result"]["id"] == "cal_ev_123"
        assert mock_google.create_calendar_event.call_count == 1

        # Lần 2: Gửi lại y hệt operation_id & payload (Gateway retry)
        res2 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload, headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2 == data1
        # mock_google KHÔNG được gọi lần thứ hai!
        assert mock_google.create_calendar_event.call_count == 1


def test_action_idempotency_payload_mismatch_raises_409(test_setup):
    client, settings, session_factory, user_id = test_setup
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    payload1 = {
        "operation_id": "op_test_idem_002",
        "telegram_user_id": 777,
        "chat_id": 777,
        "telegram_binding_id": "tg_bind_idem_123",
        "action": "calendar.create",
        "arguments": {
            "summary": "Họp kế hoạch",
            "start_time": "2026-10-27T10:00:00Z",
            "end_time": "2026-10-27T11:00:00Z",
        },
    }
    payload2 = {
        "operation_id": "op_test_idem_002",
        "telegram_user_id": 777,
        "chat_id": 777,
        "telegram_binding_id": "tg_bind_idem_123",
        "action": "calendar.create",
        "arguments": {
            "summary": "Họp đột xuất (khác summary)",
            "start_time": "2026-10-27T10:00:00Z",
            "end_time": "2026-10-27T11:00:00Z",
        },
    }

    mock_google = MagicMock()
    mock_google.create_calendar_event.return_value = {"id": "cal_ev_456"}

    with patch("app.internal.routes._get_google_client", return_value=mock_google):
        res1 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload1, headers=headers)
        assert res1.status_code == 200

        # Trùng operation_id nhưng payload khác -> HTTP 409
        res2 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload2, headers=headers)
        assert res2.status_code == 409
        assert res2.json()["detail"]["code"] == "PAYLOAD_MISMATCH"


def test_action_timeout_records_unknown_result(test_setup):
    client, settings, session_factory, user_id = test_setup
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    payload = {
        "operation_id": "op_test_idem_timeout",
        "telegram_user_id": 777,
        "chat_id": 777,
        "telegram_binding_id": "tg_bind_idem_123",
        "action": "calendar.create",
        "arguments": {
            "summary": "Họp timeout",
            "start_time": "2026-10-27T10:00:00Z",
            "end_time": "2026-10-27T11:00:00Z",
        },
    }

    mock_google = MagicMock()
    mock_google.create_calendar_event.side_effect = httpx.TimeoutException("Timeout")

    with patch("app.internal.routes._get_google_client", return_value=mock_google):
        res1 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload, headers=headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "UNKNOWN_RESULT"

        # Lần 2 gọi lại cùng operation_id: trả lại UNKNOWN_RESULT, không gọi Google lần 2
        res2 = client.post(f"/internal/v1/users/{user_id}/actions", json=payload, headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "UNKNOWN_RESULT"
        assert mock_google.create_calendar_event.call_count == 1
