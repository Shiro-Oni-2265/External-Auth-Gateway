from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.google.client import GoogleClient
from app.main import create_app
from app.models import GoogleConnection, OdooCredential, TelegramConnection, User
from app.security import encrypt_secret


def test_google_client_uses_patch_for_calendar_update():
    """GoogleClient.update_calendar_event phải dùng phương thức HTTP PATCH
    để hỗ trợ cập nhật một phần không làm mất các trường khác."""
    mock_oauth = MagicMock()
    mock_oauth.get_valid_access_token.return_value = "fake_access_token"

    client = GoogleClient(oauth_service=mock_oauth)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "evt_123", "summary": "Họp cập nhật"}

    with patch.object(client, "_request", return_value=mock_resp) as mock_req:
        res = client.update_calendar_event(
            user_id=1,
            calendar_id="primary",
            event_id="evt_123",
            event_data={"summary": "Họp cập nhật"},
            etag='"etag_version_1"',
        )
        assert res["id"] == "evt_123"
        # Đảm bảo gọi _request với phương thức PATCH
        mock_req.assert_called_once_with(
            1,
            "PATCH",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events/evt_123",
            json_data={"summary": "Họp cập nhật"},
            headers={"If-Match": '"etag_version_1"'},
        )


def test_calendar_update_conflict_on_412(tmp_path):
    """Khi Google trả về 412 Precondition Failed (ETag mismatch), API action trả về CONFLICT."""
    db_file = tmp_path / "test_cal_conflict.db"
    key = Fernet.generate_key().decode()
    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_file}",
        DEBUG=True,
        APP_ENV="development",
        GATEWAY_PUBLIC_URL="http://localhost:8080",
        ENCRYPTION_KEY=key,
        AGENT_TO_GATEWAY_SECRET="agent_secret_patch_min_32_characters_12345",
        GATEWAY_TO_AGENT_SECRET="gateway_secret_patch_min_32_characters_12345",
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
            google_sub="google_sub_1",
            status="connected",
            scopes="https://www.googleapis.com/auth/calendar.events",
            encrypted_access_token="tok",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
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

    test_client = TestClient(app)
    headers = {"Authorization": "Bearer agent_secret_patch_min_32_characters_12345"}

    mock_google = MagicMock()
    # Giả lập Google trả về 412
    mock_resp = MagicMock()
    mock_resp.status_code = 412
    mock_google.update_calendar_event.side_effect = httpx.HTTPStatusError(
        "412 Precondition Failed",
        request=MagicMock(),
        response=mock_resp,
    )

    with patch("app.internal.routes._verify_odoo_account", return_value={"id": 10, "login": "user@example.com"}), \
         patch("app.internal.routes._get_google_client", return_value=mock_google):
        resp = test_client.post(
            "/internal/v1/users/1/actions",
            json={
                "operation_id": "op_patch_conflict_1",
                "telegram_user_id": 123,
                "chat_id": 123,
                "telegram_binding_id": "bind_1",
                "action": "calendar.update",
                "arguments": {
                    "event_id": "evt_123",
                    "title": "Tên mới",
                    "etag": '"etag_old"',
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "CONFLICT"
        assert "If-Match conflict" in data.get("message", "")
