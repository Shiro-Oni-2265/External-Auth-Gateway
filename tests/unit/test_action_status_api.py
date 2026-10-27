import json
from datetime import UTC, datetime, timedelta
from cryptography.fernet import Fernet
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ActionOperation, TelegramConnection, User


@pytest.fixture
def action_status_env(tmp_path):
    db_path = tmp_path / "action_status_test.db"
    fernet_key = Fernet.generate_key().decode()
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite:///{db_path}",
        ENCRYPTION_KEY=fernet_key,
        AGENT_TO_GATEWAY_SECRET="test_agent_secret_32_chars_min_length",
        GATEWAY_TO_AGENT_SECRET="test_gw_secret_32_chars_min_length",
    )
    app = create_app(settings)
    client = TestClient(app)

    with app.state.session_factory() as db:
        user = User(
            id=10,
            tenant="https://test.odoo.local",
            odoo_uid=20,
            login="user@test.local",
            name="Test Action User",
            active=True,
            database="test_db",
        )
        tg = TelegramConnection(
            user_id=10,
            telegram_user_id=888999,
            private_chat_id=888999,
            binding_id="bind_action_status_1",
            status="connected",
        )
        db.add_all([user, tg])
        db.commit()

    return client, settings, app.state.session_factory, 10


def test_action_status_requires_auth(action_status_env):
    client, settings, _, user_id = action_status_env
    resp = client.get(
        f"/internal/v1/users/{user_id}/actions/op_test_1",
        params={
            "telegram_user_id": 888999,
            "chat_id": 888999,
            "telegram_binding_id": "bind_action_status_1",
        },
    )
    assert resp.status_code in [401, 403]


def test_action_status_not_found(action_status_env):
    client, settings, _, user_id = action_status_env
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}
    resp = client.get(
        f"/internal/v1/users/{user_id}/actions/op_non_existent",
        params={
            "telegram_user_id": 888999,
            "chat_id": 888999,
            "telegram_binding_id": "bind_action_status_1",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation_id"] == "op_non_existent"
    assert data["status"] == "not_found"
    assert data["result"] is None


def test_action_status_completed_returns_result(action_status_env):
    client, settings, session_factory, user_id = action_status_env
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    # Tạo bản ghi completed
    with session_factory() as db:
        op = ActionOperation(
            operation_id="op_completed_123",
            user_id=user_id,
            action="calendar.create",
            payload_hash="hash_123",
            telegram_binding_id="bind_action_status_1",
            status="completed",
            result_json=json.dumps({"status": "completed", "result": {"id": "cal_event_999", "summary": "Họp chiến lược"}}),
        )
        db.add(op)
        db.commit()

    resp = client.get(
        f"/internal/v1/users/{user_id}/actions/op_completed_123",
        params={
            "telegram_user_id": 888999,
            "chat_id": 888999,
            "telegram_binding_id": "bind_action_status_1",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["operation_id"] == "op_completed_123"
    assert data["action"] == "calendar.create"
    assert data["status"] == "completed"
    assert data["result"]["id"] == "cal_event_999"
    assert data["result"]["summary"] == "Họp chiến lược"


def test_action_status_rejects_binding_mismatch(action_status_env):
    client, settings, session_factory, user_id = action_status_env
    headers = {"Authorization": f"Bearer {settings.AGENT_TO_GATEWAY_SECRET}"}

    # Tạo bản ghi với binding bind_action_status_1
    with session_factory() as db:
        op = ActionOperation(
            operation_id="op_mismatch_456",
            user_id=user_id,
            action="gmail.send",
            payload_hash="hash_456",
            telegram_binding_id="bind_different_user",
            status="completed",
            result_json=json.dumps({"status": "completed", "result": {"id": "msg_secret"}}),
        )
        db.add(op)
        db.commit()

    # Truy vấn với bind_action_status_1 -> phải bị từ chối 403
    resp = client.get(
        f"/internal/v1/users/{user_id}/actions/op_mismatch_456",
        params={
            "telegram_user_id": 888999,
            "chat_id": 888999,
            "telegram_binding_id": "bind_action_status_1",
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BINDING_MISMATCH"
