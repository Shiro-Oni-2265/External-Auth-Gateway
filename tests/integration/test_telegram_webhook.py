from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import TelegramUpdate


def test_telegram_webhook_secret_and_idempotency(temp_db_path, fernet_key):
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
    )
    app = create_app(settings)
    client = TestClient(app)

    valid_payload = {
        "update_id": 55555,
        "message": {
            "message_id": 1,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "is_bot": False},
            "text": "/help",
        },
    }

    # 1. Missing secret token -> 403
    resp_no_secret = client.post("/webhooks/telegram", json=valid_payload)
    assert resp_no_secret.status_code == 403

    # 2. Wrong secret token -> 403
    resp_wrong_secret = client.post(
        "/webhooks/telegram",
        json=valid_payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
    )
    assert resp_wrong_secret.status_code == 403

    # 3. Valid secret token -> 200 OK and update saved in DB
    with patch("app.telegram.client.TelegramClient.send_message"):
        resp_valid = client.post(
            "/webhooks/telegram",
            json=valid_payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "expected_webhook_secret_xyz"},
        )
        assert resp_valid.status_code == 200

    with app.state.session_factory() as db:
        up = db.query(TelegramUpdate).filter_by(update_id=55555).first()
        assert up is not None

    # 4. Duplicate update_id -> returns 200 OK idempotently
    resp_duplicate = client.post(
        "/webhooks/telegram",
        json=valid_payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "expected_webhook_secret_xyz"},
    )
    assert resp_duplicate.status_code == 200
