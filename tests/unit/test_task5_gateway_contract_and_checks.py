from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import GoogleConnection, OdooCredential, User


@pytest.fixture
def gateway_test_env(tmp_path):
    db_file = tmp_path / "test_task5.db"
    key = Fernet.generate_key().decode()
    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_file}",
        DEBUG=True,
        APP_ENV="development",
        GATEWAY_PUBLIC_URL="http://localhost:8080",
        ENCRYPTION_KEY=key,
        AGENT_TO_GATEWAY_SECRET="agent_secret_task5_min_32_characters_12345",
        GATEWAY_TO_AGENT_SECRET="gateway_secret_task5_min_32_characters_12345",
    )
    app = create_app(settings=settings)

    from app.db import init_db
    init_db(settings.DATABASE_URL)

    with app.state.session_factory() as db:
        user = User(
            id=1,
            tenant="default",
            database="odoo_db",
            odoo_uid=10,
            login="user@example.com",
            name="User A",
            active=True,
        )
        db.add(user)
        from app.security import encrypt_secret
        enc_sec = encrypt_secret("my_odoo_password", key)
        cred = OdooCredential(
            user_id=1,
            credential_kind="password",
            encrypted_secret=enc_sec,
        )
        db.add(cred)
        from datetime import UTC, datetime, timedelta
        g_conn = GoogleConnection(
            user_id=1,
            email="user@gmail.com",
            google_sub="google_sub_1",
            status="connected",
            scopes="https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.send",
            encrypted_access_token="dummy_token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        from app.models import TelegramConnection
        t_conn = TelegramConnection(
            user_id=1,
            telegram_user_id=123,
            private_chat_id=123,
            binding_id="bind_1",
            status="connected",
        )
        db.add(g_conn)
        db.add(t_conn)
        db.commit()

    client = TestClient(app)
    return client, app


def test_gmail_send_permission_denied_not_wrapped_as_completed(gateway_test_env):
    """Khi Google trả về PERMISSION_DENIED (403), Gateway phải giữ nguyên trạng thái, không bọc completed."""
    client, app = gateway_test_env
    headers = {"Authorization": "Bearer agent_secret_task5_min_32_characters_12345"}

    mock_google = MagicMock()
    mock_google.send_email.return_value = {
        "status": "PERMISSION_DENIED",
        "message": "Chưa được cấp quyền gửi Gmail (cần scope gmail.send).",
    }

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp = client.post(
            "/internal/v1/users/1/actions",
            json={
                "operation_id": "op_gmail_perm_denied_1",
                "telegram_user_id": 123,
                "chat_id": 123,
                "telegram_binding_id": "bind_1",
                "action": "gmail.send",
                "arguments": {
                    "to": "test@example.com",
                    "subject": "Test Subj",
                    "body_text": "Test Body",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Quan trọng: status phải là PERMISSION_DENIED, tuyệt đối không được là completed
        assert data.get("status") == "PERMISSION_DENIED"
        assert "completed" != data.get("status")


def test_gmail_send_missing_message_id_fails(gateway_test_env):
    """Khi Google client trả về dict thiếu ID, action phải bị đánh dấu failed."""
    client, app = gateway_test_env
    headers = {"Authorization": "Bearer agent_secret_task5_min_32_characters_12345"}

    mock_google = MagicMock()
    # Trả về kết quả thiếu ID
    mock_google.send_email.return_value = {"status": "OK"}

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp = client.post(
            "/internal/v1/users/1/actions",
            json={
                "operation_id": "op_gmail_missing_id_1",
                "telegram_user_id": 123,
                "chat_id": 123,
                "telegram_binding_id": "bind_1",
                "action": "gmail.send",
                "arguments": {
                    "to": "test@example.com",
                    "subject": "Test Subj",
                    "body_text": "Test Body",
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ["SERVICE_ERROR", "failed"]


def test_get_calendar_event_detail(gateway_test_env):
    """API GET chi tiết sự kiện lịch phải trả về event data với etag."""
    client, app = gateway_test_env
    headers = {"Authorization": "Bearer agent_secret_task5_min_32_characters_12345"}

    mock_google = MagicMock()
    mock_google.get_calendar_event.return_value = {
        "id": "evt_abc123",
        "summary": "Cuộc họp quan trọng",
        "start": {"dateTime": "2026-10-27T09:00:00+07:00"},
        "end": {"dateTime": "2026-10-27T10:00:00+07:00"},
        "etag": '"etag_val_123"',
    }

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp = client.get("/internal/v1/users/1/calendar/events/evt_abc123", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "OK"
        assert data.get("event", {}).get("id") == "evt_abc123"
        assert data.get("event", {}).get("etag") == '"etag_val_123"'


def test_get_crm_lead_detail(gateway_test_env):
    """API GET chi tiết lead CRM phải trả về thông tin lead kèm write_date."""
    client, app = gateway_test_env
    headers = {"Authorization": "Bearer agent_secret_task5_min_32_characters_12345"}

    mock_odoo = MagicMock()
    mock_odoo.compute_permissions.return_value = ["crm.read", "crm.create", "crm.write"]
    mock_odoo.get_lead_detail.return_value = {
        "id": 99,
        "name": "Cơ hội VIP",
        "expected_revenue": 500000000.0,
        "stage_id": [2, "Qualified"],
        "write_date": "2026-10-26 08:00:00",
    }

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_odoo_service", return_value=mock_odoo):
        resp = client.get("/internal/v1/users/1/crm/leads/99", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "OK"
        assert data.get("lead", {}).get("id") == 99
        assert data.get("lead", {}).get("write_date") == "2026-10-26 08:00:00"


def test_crm_update_write_date_conflict(gateway_test_env):
    """Khi update CRM lead với expected_write_date cũ hơn write_date hiện tại, phải báo CONFLICT."""
    client, app = gateway_test_env
    headers = {"Authorization": "Bearer agent_secret_task5_min_32_characters_12345"}

    mock_odoo = MagicMock()
    mock_odoo.compute_permissions.return_value = ["crm.read", "crm.create", "crm.write"]
    mock_odoo.update_lead.side_effect = ValueError(
        "Dữ liệu cơ hội CRM đã bị thay đổi trên hệ thống sau khi tạo bản xem trước (write_date mismatch). Vui lòng tạo yêu cầu mới."
    )

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_odoo_service", return_value=mock_odoo):
        resp = client.post(
            "/internal/v1/users/1/actions",
            json={
                "operation_id": "op_crm_conflict_1",
                "telegram_user_id": 123,
                "chat_id": 123,
                "telegram_binding_id": "bind_1",
                "action": "crm.update",
                "arguments": {
                    "lead_id": 99,
                    "expected_revenue": 600000000.0,
                    "expected_write_date": "2026-10-26 08:00:00",  # Khác với write_date hiện tại
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ["CONFLICT", "failed"]

