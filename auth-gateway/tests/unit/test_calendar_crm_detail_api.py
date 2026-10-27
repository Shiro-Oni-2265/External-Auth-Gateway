import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from datetime import datetime, UTC, timedelta

from app.main import create_app
from app.models import User, OdooCredential, GoogleConnection, TelegramConnection
from app.config import Settings
from app.security import encrypt_secret


@pytest.fixture
def detail_api_env(tmp_path):
    db_file = tmp_path / "test_detail_api.db"
    key = Fernet.generate_key().decode()
    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_file}",
        DEBUG=True,
        APP_ENV="development",
        GATEWAY_PUBLIC_URL="http://localhost:8080",
        ENCRYPTION_KEY=key,
        AGENT_TO_GATEWAY_SECRET="agent_secret_detail_api_min_32_chars_12345",
        GATEWAY_TO_AGENT_SECRET="gateway_secret_detail_api_min_32_chars_12345",
        ODOO_URL="http://mock-odoo:8069",
        ODOO_DB="test_db",
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
            name="Test User",
            active=True,
        )
        db.add(user)
        enc_sec = encrypt_secret("my_odoo_password", key)
        cred = OdooCredential(
            user_id=1,
            credential_kind="password",
            encrypted_secret=enc_sec,
        )
        db.add(cred)
        g_conn = GoogleConnection(
            user_id=1,
            email="user@gmail.com",
            google_sub="sub_123",
            status="connected",
            scopes="https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/gmail.send",
            encrypted_access_token="tok",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        t_conn = TelegramConnection(
            user_id=1,
            telegram_user_id=123,
            private_chat_id=123,
            binding_id="bind_123",
            status="connected",
        )
        db.add(g_conn)
        db.add(t_conn)
        db.commit()

    client = TestClient(app)
    return client, app, key


def test_calendar_event_detail_signature_and_success(detail_api_env):
    """Test gọi GET calendar event detail thật mà KHÔNG mock _verify_odoo_account.
    Hàm này phải giải mã credential, gọi đúng odoo_svc.validate_user_account và trả về 200."""
    client, app, key = detail_api_env
    headers = {"Authorization": "Bearer agent_secret_detail_api_min_32_chars_12345"}

    mock_odoo = MagicMock()
    # Mock validate_user_account thành công
    mock_odoo.validate_user_account.return_value = {"id": 10, "login": "user@example.com", "active": True}
    mock_odoo.compute_permissions.return_value = ["calendar.read", "calendar.write", "crm.read"]

    mock_google = MagicMock()
    mock_google.get_calendar_event.return_value = {
        "id": "evt_101",
        "summary": "Họp giao ban",
        "start": {"dateTime": "2026-10-27T09:00:00+07:00"},
        "end": {"dateTime": "2026-10-27T10:00:00+07:00"},
        "etag": '"etag_abc"',
    }

    # CHÚ Ý: Không mock _verify_odoo_account! Chỉ mock client bên ngoài
    with patch("app.internal.routes._get_odoo_service", return_value=mock_odoo), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp = client.get("/internal/v1/users/1/calendar/events/evt_101", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "OK"
        assert data.get("event", {}).get("id") == "evt_101"
        assert data.get("event", {}).get("etag") == '"etag_abc"'
        mock_odoo.validate_user_account.assert_called_once_with(uid=10, secret="my_odoo_password")


def test_crm_lead_detail_signature_and_success(detail_api_env):
    """Test gọi GET CRM lead detail thật mà KHÔNG mock _verify_odoo_account.
    Hàm này phải giải mã credential, kiểm tra quyền crm.read và trả về 200."""
    client, app, key = detail_api_env
    headers = {"Authorization": "Bearer agent_secret_detail_api_min_32_chars_12345"}

    mock_odoo = MagicMock()
    mock_odoo.validate_user_account.return_value = {"id": 10, "login": "user@example.com", "active": True}
    mock_odoo.compute_permissions.return_value = ["crm.read", "crm.create", "crm.write"]
    mock_odoo.get_lead_detail.return_value = {
        "id": 99,
        "name": "Cơ hội VIP",
        "expected_revenue": 500000000.0,
        "stage_id": [2, "Qualified"],
        "write_date": "2026-10-26 08:00:00",
    }

    with patch("app.internal.routes._get_odoo_service", return_value=mock_odoo):
        resp = client.get("/internal/v1/users/1/crm/leads/99", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "OK"
        assert data.get("lead", {}).get("id") == 99
        assert data.get("lead", {}).get("write_date") == "2026-10-26 08:00:00"
        mock_odoo.validate_user_account.assert_called_once_with(uid=10, secret="my_odoo_password")


def test_detail_endpoints_inactive_odoo_account_rejected(detail_api_env):
    """Khi tài khoản Odoo bị khóa/thu hồi credential, cả 2 API đều phải từ chối (403)."""
    client, app, key = detail_api_env
    headers = {"Authorization": "Bearer agent_secret_detail_api_min_32_chars_12345"}

    mock_odoo = MagicMock()
    # Giả lập tài khoản bị vô hiệu hóa
    mock_odoo.validate_user_account.side_effect = ValueError("Tài khoản Odoo của bạn đã bị vô hiệu hóa hoặc là tài khoản bên ngoài.")

    with patch("app.internal.routes._get_odoo_service", return_value=mock_odoo):
        resp1 = client.get("/internal/v1/users/1/calendar/events/evt_101", headers=headers)
        assert resp1.status_code == 403
        data1 = resp1.json()
        assert data1.get("detail", {}).get("code") in ["ODOO_ACCOUNT_INACTIVE", "ODOO_ACCESS_DENIED", "ODOO_CREDENTIAL_INVALID"]

        resp2 = client.get("/internal/v1/users/1/crm/leads/99", headers=headers)
        assert resp2.status_code == 403
        data2 = resp2.json()
        assert data2.get("detail", {}).get("code") in ["ODOO_ACCOUNT_INACTIVE", "ODOO_ACCESS_DENIED", "ODOO_CREDENTIAL_INVALID"]


def test_detail_endpoints_missing_permission_rejected(detail_api_env):
    """Khi user thiếu quyền calendar.read hoặc crm.read, phải trả về 403 PERMISSION_DENIED."""
    client, app, key = detail_api_env
    headers = {"Authorization": "Bearer agent_secret_detail_api_min_32_chars_12345"}

    mock_odoo = MagicMock()
    mock_odoo.validate_user_account.return_value = {"id": 10, "login": "user@example.com", "active": True}
    # User không có quyền calendar.read hay crm.read
    mock_odoo.compute_permissions.return_value = []

    mock_google = MagicMock()
    with patch("app.internal.routes._get_odoo_service", return_value=mock_odoo), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp1 = client.get("/internal/v1/users/1/calendar/events/evt_101", headers=headers)
        assert resp1.status_code == 403
        assert resp1.json().get("detail", {}).get("code") == "PERMISSION_DENIED"

        resp2 = client.get("/internal/v1/users/1/crm/leads/99", headers=headers)
        assert resp2.status_code == 403
        assert resp2.json().get("detail", {}).get("code") == "PERMISSION_DENIED"


def test_detail_endpoints_record_not_found(detail_api_env):
    """Khi bản ghi event hoặc lead không tồn tại, phải trả về 404 NOT_FOUND."""
    client, app, key = detail_api_env
    headers = {"Authorization": "Bearer agent_secret_detail_api_min_32_chars_12345"}

    mock_odoo = MagicMock()
    mock_odoo.validate_user_account.return_value = {"id": 10, "login": "user@example.com", "active": True}
    mock_odoo.compute_permissions.return_value = ["calendar.read", "crm.read"]
    mock_odoo.get_lead_detail.return_value = None  # Không tìm thấy lead

    import httpx
    mock_google = MagicMock()
    # Giả lập Google trả về 404
    mock_google.get_calendar_event.side_effect = httpx.HTTPStatusError(
        "404 Not Found",
        request=MagicMock(),
        response=MagicMock(status_code=404, json=lambda: {"error": {"message": "Not Found"}}),
    )

    with patch("app.internal.routes._get_odoo_service", return_value=mock_odoo), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp1 = client.get("/internal/v1/users/1/calendar/events/non_existent_evt", headers=headers)
        assert resp1.status_code == 404

        resp2 = client.get("/internal/v1/users/1/crm/leads/9999", headers=headers)
        assert resp2.status_code == 404
