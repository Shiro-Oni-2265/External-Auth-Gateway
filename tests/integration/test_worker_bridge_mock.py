import json
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.models import TelegramConnection, TelegramUpdate, User
from app.telegram.client import TelegramClient
from app.telegram.service import create_telegram_service
from app.telegram.worker import TelegramAIWorker


@pytest.fixture
def cross_app_env(tmp_path):
    secret_gw_to_agent = "gateway_to_agent_cross_secret_min32chars"
    secret_agent_to_gw = "agent_to_gateway_cross_secret_min32chars"

    gw_db_path = tmp_path / "gateway_e2e.db"
    from app.db import init_db

    engine, session_factory = init_db(f"sqlite:///{gw_db_path}")

    gw_settings = Settings(
        DATABASE_URL=f"sqlite:///{gw_db_path}",
        ENABLE_AI_AGENT_FORWARDING=True,
        AI_AGENT_BASE_URL="http://ai-agent:8000",
        GATEWAY_TO_AGENT_SECRET=secret_gw_to_agent,
        AGENT_TO_GATEWAY_SECRET=secret_agent_to_gw,
        TELEGRAM_BOT_TOKEN="test_bot_token",
    )

    mock_telegram = MagicMock(spec=TelegramClient)

    gw_service = create_telegram_service(
        settings=gw_settings,
        session_factory=session_factory,
        telegram_client=mock_telegram,
        odoo_client=None,
    )

    worker = TelegramAIWorker(
        settings=gw_settings,
        session_factory=session_factory,
        telegram_client=mock_telegram,
    )

    # Seed User & TelegramConnection in Gateway
    with session_factory() as db:
        user = User(
            id=10,
            tenant="https://corp.example.com",
            database="corp_db",
            odoo_uid=88,
            login="employee88@example.com",
            name="Vũ Hoàng Yến",
            tz="Asia/Bangkok",
            active=True,
        )
        conn = TelegramConnection(
            id=10,
            user_id=10,
            telegram_user_id=88888,
            private_chat_id=88888,
            status="connected",
        )
        db.add_all([user, conn])
        db.commit()

    return session_factory, gw_service, worker, mock_telegram, gw_settings


def test_end_to_end_telegram_to_ai_agent_and_delivered(cross_app_env):
    """End-to-end integration test spanning Auth Gateway and AI Agent:
    Telegram update -> Gateway Ingestion -> DB Pending -> Worker Claim ->
    AI Turn Request -> AI Response Parsing -> Chunk Delivery Tracking -> Telegram Send -> DB Processed.
    """
    session_factory, gw_service, worker, mock_telegram, gw_settings = cross_app_env

    # 1. Telegram update received (via webhook or poller ingestion)
    update = {
        "update_id": 9901,
        "message": {
            "chat": {"id": 88888, "type": "private"},
            "from": {"id": 88888, "is_bot": False},
            "text": "Hôm nay tôi có gì?",
        },
    }

    res = gw_service.ingest_update(update)
    assert res["status"] == "ok"

    with session_factory() as db:
        up = db.query(TelegramUpdate).filter_by(update_id=9901).first()
        assert up.status == "pending"
        assert up.delivery_status == "none"

    # 2. Mock AI Agent upstream HTTP response adhering to contract
    ai_turn_response = {
        "request_id": "tg-9901",
        "messages": [
            "👋 **Chào bạn Vũ Hoàng Yến!** Điểm tin ngày 26/10/2026:\n"
            "• Lịch: 09:00 Họp kế hoạch\n"
            "• Công việc: 2 việc đến hạn hôm nay\n"
            "• Gmail: 0 thư chưa đọc"
        ],
    }

    class MockHTTPResponse:
        status_code = 200
        text = json.dumps(ai_turn_response)

        def json(self):
            return ai_turn_response

    with patch("httpx.post", return_value=MockHTTPResponse()) as mock_post:
        # Worker processes the queue
        processed = worker.process_pending_once()
        assert processed == 1

        # Check call to AI Agent
        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        assert url == "http://ai-agent:8000/internal/v1/telegram/turn"
        assert kwargs["headers"]["Authorization"] == f"Bearer {gw_settings.GATEWAY_TO_AGENT_SECRET}"
        assert kwargs["json"]["request_id"] == "tg-9901"
        assert kwargs["json"]["gateway_user_id"] == 10
        assert kwargs["json"]["telegram_user_id"] == 88888

    # 3. Check DB update final status
    with session_factory() as db:
        up = db.query(TelegramUpdate).filter_by(update_id=9901).first()
        assert up.status == "processed"
        assert up.delivery_status == "delivered"
        assert up.user_id == 10
        assert up.attempts == 1
        assert up.ai_response is not None
        assert "Vũ Hoàng Yến" in up.ai_response

    # 4. Check Telegram received delivery
    mock_telegram.send_message.assert_called_once()
    args, kwargs = mock_telegram.send_message.call_args
    assert args[0] == 88888  # chat_id
    assert "Vũ Hoàng Yến" in args[1]
    assert kwargs.get("parse_mode") == ""
