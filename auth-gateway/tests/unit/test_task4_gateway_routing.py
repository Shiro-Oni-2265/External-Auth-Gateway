import pytest
from unittest.mock import MagicMock
from app.telegram.service import TelegramService


@pytest.fixture
def mock_telegram_service():
    mock_db_session = MagicMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_db_session

    mock_client = MagicMock()

    service = TelegramService(
        session_factory=mock_session_factory,
        telegram_client=mock_client,
        bot_username="test_bot",
        webhook_secret="test_secret",
        enable_ai_forwarding=True,
    )
    return service, mock_db_session


def test_crm_routing_legacy_search_handled_by_gateway(mock_telegram_service):
    """Lệnh /crm và /crm <từ khóa> thông thường phải do Gateway xử lý."""
    service, db = mock_telegram_service

    # 1. /crm
    update_1 = {
        "update_id": 101,
        "message": {
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "is_bot": False},
            "text": "/crm",
        },
    }
    res1 = service.ingest_update(update_1)
    assert res1["status"] == "ok"
    # Kiểm tra TelegramUpdate được tạo với status='gateway_command'
    db.add.assert_called()
    saved_update1 = db.add.call_args_list[-1][0][0]
    assert saved_update1.status == "gateway_command"

    # 2. /crm từ khóa tìm kiếm
    update_2 = {
        "update_id": 102,
        "message": {
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 12345, "is_bot": False},
            "text": "/crm Công ty Vinamilk",
        },
    }
    res2 = service.ingest_update(update_2)
    assert res2["status"] == "ok"
    saved_update2 = db.add.call_args_list[-1][0][0]
    assert saved_update2.status == "gateway_command"


def test_crm_routing_advanced_subcommands_forwarded_to_agent(mock_telegram_service):
    """Lệnh /crm search, /crm create, /crm update phải được chuyển sang Agent (status='pending')."""
    service, db = mock_telegram_service

    subcommands = [
        "/crm search Vinamilk",
        "/crm create Khách hàng tiềm năng A | 0901234567",
        "/crm update 42 | 3 | 100000000",
    ]

    for idx, cmd in enumerate(subcommands, start=201):
        update = {
            "update_id": idx,
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False},
                "text": cmd,
            },
        }
        res = service.ingest_update(update)
        assert res["status"] == "ok"
        saved_update = db.add.call_args_list[-1][0][0]
        assert saved_update.status == "pending", f"Lệnh '{cmd}' phải có status='pending' để forward sang Agent, nhưng lại là {saved_update.status}"
