from unittest.mock import MagicMock

import pytest

from app.db import init_db
from app.models import OdooCredential, TelegramConnection, User
from app.odoo.client import OdooAccessError, OdooClient
from app.security import encrypt_secret
from app.telegram.client import TelegramClient
from app.telegram.service import TelegramService


@pytest.fixture
def crm_components(temp_db_path, fernet_key):
    engine, session_factory = init_db(f"sqlite:///{temp_db_path}")

    # Create User A
    with session_factory() as db:
        user_a = User(
            tenant="https://test.odoo.com",
            database="test_db",
            odoo_uid=11,
            login="user_a@company.com",
            name="Sales Rep A",
        )
        db.add(user_a)
        db.flush()
        db.add(
            OdooCredential(
                user_id=user_a.id,
                credential_kind="api_key",
                encrypted_secret=encrypt_secret("key_a", fernet_key),
            )
        )
        db.add(
            TelegramConnection(
                user_id=user_a.id,
                telegram_user_id=11111,
                private_chat_id=11111,
                status="connected",
            )
        )

        # Create User B
        user_b = User(
            tenant="https://test.odoo.com",
            database="test_db",
            odoo_uid=22,
            login="user_b@company.com",
            name="Sales Rep B",
        )
        db.add(user_b)
        db.flush()
        db.add(
            OdooCredential(
                user_id=user_b.id,
                credential_kind="api_key",
                encrypted_secret=encrypt_secret("key_b", fernet_key),
            )
        )
        db.add(
            TelegramConnection(
                user_id=user_b.id,
                telegram_user_id=22222,
                private_chat_id=22222,
                status="connected",
            )
        )
        db.commit()

    mock_odoo = MagicMock(spec=OdooClient)
    mock_tele = MagicMock(spec=TelegramClient)

    service = TelegramService(
        session_factory=session_factory,
        telegram_client=mock_tele,
        bot_username="MyOdooBot",
        webhook_secret="secret",
        odoo_client=mock_odoo,
        encryption_key=fernet_key,
        odoo_base_url="https://test.odoo.com",
    )

    yield service, session_factory, mock_odoo, mock_tele
    engine.dispose()


def test_crm_without_keyword(crm_components):
    service, _, mock_odoo, mock_tele = crm_components

    mock_odoo.execute.return_value = [
        {"id": 101, "name": "Khách hàng TechCorp <VIP>", "stage_id": [2, "Đang đàm phán"]},
        {"id": 102, "name": "Dự án ERP Toàn Cầu", "stage_id": [1, "Mới tiếp nhận"]},
    ]

    update = {
        "update_id": 201,
        "message": {
            "message_id": 1,
            "chat": {"id": 11111, "type": "private"},
            "from": {"id": 11111, "is_bot": False},
            "text": "/crm",
        },
    }

    service.process_update_dict(update)

    # Verify query used User A's uid and key
    mock_odoo.execute.assert_called_once()
    args, kwargs = mock_odoo.execute.call_args
    assert args[0] == 11  # uid=11
    assert args[1] == "key_a"  # secret='key_a'
    assert args[2] == "crm.lead"
    assert args[3] == "search_read"

    # Verify domain
    domain = args[4][0]
    assert ("user_id", "=", 11) in domain
    assert ("active", "=", True) in domain

    # Verify output message was sent and HTML escaped
    mock_tele.send_message.assert_called_once()
    msg_sent = mock_tele.send_message.call_args[0][1]
    assert "&lt;VIP&gt;" in msg_sent  # HTML escaped!
    assert "Khách hàng TechCorp" in msg_sent
    assert "Đang đàm phán" in msg_sent
    assert "https://test.odoo.com" in msg_sent


def test_crm_with_keyword_and_bot_suffix(crm_components):
    service, _, mock_odoo, mock_tele = crm_components
    mock_odoo.execute.return_value = []

    update = {
        "update_id": 202,
        "message": {
            "message_id": 2,
            "chat": {"id": 11111, "type": "private"},
            "from": {"id": 11111, "is_bot": False},
            "text": "/crm@MyOdooBot Vinamilk",
        },
    }

    service.process_update_dict(update)

    domain = mock_odoo.execute.call_args[0][4][0]
    assert ("name", "ilike", "Vinamilk") in domain

    msg_sent = mock_tele.send_message.call_args[0][1]
    assert "Không tìm thấy" in msg_sent


def test_crm_access_error_handled_gracefully(crm_components):
    service, _, mock_odoo, mock_tele = crm_components
    mock_odoo.execute.side_effect = OdooAccessError("Access Denied to model crm.lead")

    update = {
        "update_id": 203,
        "message": {
            "message_id": 3,
            "chat": {"id": 11111, "type": "private"},
            "from": {"id": 11111, "is_bot": False},
            "text": "/crm",
        },
    }

    service.process_update_dict(update)

    msg_sent = mock_tele.send_message.call_args[0][1]
    assert "quyền" in msg_sent.lower()


def test_crm_rate_limiting(crm_components):
    service, _, mock_odoo, mock_tele = crm_components
    mock_odoo.execute.return_value = []

    # 10 calls succeed
    for i in range(10):
        update = {
            "update_id": 300 + i,
            "message": {
                "message_id": 10 + i,
                "chat": {"id": 11111, "type": "private"},
                "from": {"id": 11111, "is_bot": False},
                "text": "/crm",
            },
        }
        service.process_update_dict(update)

    # 11th call in same minute -> rate limited
    update_11 = {
        "update_id": 311,
        "message": {
            "message_id": 21,
            "chat": {"id": 11111, "type": "private"},
            "from": {"id": 11111, "is_bot": False},
            "text": "/crm",
        },
    }
    service.process_update_dict(update_11)

    last_msg = mock_tele.send_message.call_args[0][1]
    assert "thao tác quá nhanh" in last_msg or "giới hạn" in last_msg
