from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import GoogleConnection, TelegramConnection, User


def _setup_app_and_client(temp_db_path, fernet_key):
    settings = Settings(
        APP_ENV="development",
        PUBLIC_BASE_URL="http://localhost:8080",
        ODOO_URL="https://mycompany.odoo.com",
        ODOO_DB="test_db",
        DATABASE_URL=f"sqlite:///{temp_db_path}",
        ENCRYPTION_KEY=fernet_key,
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        TELEGRAM_BOT_USERNAME="TestBot",
        TELEGRAM_WEBHOOK_SECRET="expected_webhook_secret_xyz",
        AGENT_TO_GATEWAY_SECRET="super-secret-agent-key-1234567890",
    )
    app = create_app(settings)
    client = TestClient(app)
    return app, client, settings


def test_internal_api_authentication(temp_db_path, fernet_key):
    app, client, settings = _setup_app_and_client(temp_db_path, fernet_key)

    # 1. Missing header -> 401
    resp = client.post(
        "/internal/v1/identity/resolve", json={"telegram_user_id": 111, "chat_id": 111}
    )
    assert resp.status_code == 401

    # 2. Wrong secret -> 401
    resp = client.post(
        "/internal/v1/identity/resolve",
        json={"telegram_user_id": 111, "chat_id": 111},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


def test_internal_identity_resolve(temp_db_path, fernet_key):
    app, client, settings = _setup_app_and_client(temp_db_path, fernet_key)
    auth_header = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    with app.state.session_factory() as db:
        user = User(
            tenant="https://mycompany.odoo.com",
            database="test_db",
            odoo_uid=2,
            login="test@example.com",
            name="Test User",
            tz="Asia/Bangkok",
            active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        conn = TelegramConnection(
            user_id=user.id,
            telegram_user_id=999888,
            private_chat_id=999888,
            username="testuser",
            status="connected",
        )
        db.add(conn)
        db.commit()

    # 1. Resolve success
    resp = client.post(
        "/internal/v1/identity/resolve",
        json={"telegram_user_id": 999888, "chat_id": 999888},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["tenant"] == "https://mycompany.odoo.com"
    assert data["database"] == "test_db"
    assert data["odoo_uid"] == 2
    assert data["name"] == "Test User"
    assert data["tz"] == "Asia/Bangkok"

    # 2. Resolve not found
    resp_nf = client.post(
        "/internal/v1/identity/resolve",
        json={"telegram_user_id": 111222, "chat_id": 111222},
        headers=auth_header,
    )
    assert resp_nf.status_code == 404


def test_internal_connections_and_google(temp_db_path, fernet_key):
    app, client, settings = _setup_app_and_client(temp_db_path, fernet_key)
    auth_header = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    with app.state.session_factory() as db:
        user = User(
            tenant="https://mycompany.odoo.com",
            database="test_db",
            odoo_uid=3,
            login="sales@example.com",
            name="Sales Rep",
            tz="Asia/Bangkok",
            active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id

        g_conn = GoogleConnection(
            user_id=user_id,
            google_sub="sub-12345",
            email="sales@company.com",
            scopes="openid email https://www.googleapis.com/auth/calendar.events",
            encrypted_access_token="dummy-enc-acc",
            encrypted_refresh_token="dummy-enc-ref",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            status="connected",
        )
        db.add(g_conn)
        db.commit()

    # Test /connections
    resp_conn = client.get(f"/internal/v1/users/{user_id}/connections", headers=auth_header)
    assert resp_conn.status_code == 200
    conn_data = resp_conn.json()
    assert conn_data["google"]["status"] == "OK"
    assert conn_data["google"]["email"] == "sales@company.com"

    # Test /gmail/unread without gmail.readonly scope -> PERMISSION_DENIED
    resp_gmail = client.get(f"/internal/v1/users/{user_id}/gmail/unread", headers=auth_header)
    assert resp_gmail.status_code == 200
    gmail_data = resp_gmail.json()
    assert gmail_data["status"] == "PERMISSION_DENIED"
    assert "Cổng Portal" in gmail_data["instruction"]

    # Test /calendar/events with mock
    with patch("app.google.client.GoogleClient.list_calendar_events") as mock_cal:
        mock_cal.return_value = {
            "items": [
                {
                    "id": "event_1",
                    "summary": "Demo Product Meeting",
                    "start": {"dateTime": "2026-10-26T10:00:00+07:00"},
                    "end": {"dateTime": "2026-10-26T11:00:00+07:00"},
                    "location": "Phòng họp 1",
                    "htmlLink": "https://calendar.google.com/event1",
                }
            ]
        }
        resp_cal = client.get(
            f"/internal/v1/users/{user_id}/calendar/events?time_min=2026-10-26T00:00:00%2B07:00&time_max=2026-10-26T23:59:59%2B07:00",
            headers=auth_header,
        )
        assert resp_cal.status_code == 200
        cal_data = resp_cal.json()
        assert cal_data["status"] == "OK"
        assert len(cal_data["events"]) == 1
        assert cal_data["events"][0]["summary"] == "Demo Product Meeting"
