import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base, TelegramConnection, TelegramUpdate, User
from app.telegram.client import TelegramClient, TelegramDeliveryUnknownError
from app.telegram.worker import TelegramAIWorker


@pytest.fixture
def test_env():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings()
    mock_client = MagicMock(spec=TelegramClient)
    worker = TelegramAIWorker(
        settings=settings, session_factory=Session, telegram_client=mock_client
    )

    # Seed user 1 and connection
    with Session() as db:
        user1 = User(
            id=1,
            tenant="t1",
            database="db1",
            odoo_uid=101,
            login="u1@test.com",
            name="User 1",
            active=True,
        )
        conn1 = TelegramConnection(
            id=1, user_id=1, telegram_user_id=111, private_chat_id=111, status="connected"
        )
        db.add_all([user1, conn1])
        db.commit()

    return Session, worker, mock_client


def test_telegram_client_raises_delivery_unknown_error():
    """Test that TelegramClient.send_message raises TelegramDeliveryUnknownError on network error / timeout."""
    client = TelegramClient(bot_token="test_token")
    with patch("httpx.post", side_effect=httpx.ConnectTimeout("Connection timed out")):
        with pytest.raises(TelegramDeliveryUnknownError) as exc_info:
            client.send_message(12345, "hello")
        assert "Connection timed out" in str(exc_info.value)


def test_recipient_deactivation_stops_delivery(test_env):
    """Test that if user is deactivated before delivery, delivery halts and marks failed."""
    Session, worker, mock_client = test_env

    with Session() as db:
        up = TelegramUpdate(
            update_id=5001,
            user_id=1,
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps(["Secret briefing for User 1"]),
            delivery_status="none",
        )
        db.add(up)
        # Deactivate user 1
        db.query(User).filter_by(id=1).update({"active": False})
        db.commit()

    # Process delivery
    worker.process_pending_once()

    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=5001).first()
        assert record.status == "failed"
        assert "deactivated" in record.last_error.lower()

    # Client was never called to deliver message
    mock_client.send_message.assert_not_called()


def test_recipient_rebound_stops_delivery(test_env):
    """Test that if Telegram connection is rebound to another user during processing,
    delivery to wrong recipient is prevented.
    """
    Session, worker, mock_client = test_env

    with Session() as db:
        # Create user 2
        user2 = User(
            id=2,
            tenant="t2",
            database="db2",
            odoo_uid=102,
            login="u2@test.com",
            name="User 2",
            active=True,
        )
        db.add(user2)
        # Rebind telegram connection 111 to user 2
        db.query(TelegramConnection).filter_by(telegram_user_id=111).update({"user_id": 2})

        # Update was bound to user 1
        up = TelegramUpdate(
            update_id=5002,
            user_id=1,
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps(["Secret briefing for User 1"]),
            delivery_status="none",
        )
        db.add(up)
        db.commit()

    worker.process_pending_once()

    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=5002).first()
        assert record.status == "failed"
        assert "rebound" in record.last_error.lower()

    mock_client.send_message.assert_not_called()


def test_old_record_without_owner_withheld(test_env):
    """Test that old records with ai_response but missing user_id are withheld for manual audit."""
    Session, worker, mock_client = test_env

    with Session() as db:
        up = TelegramUpdate(
            update_id=5003,
            user_id=None,  # Missing owner
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps(["Briefing"]),
            delivery_status="none",
        )
        db.add(up)
        db.commit()

    worker.process_pending_once()

    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=5003).first()
        assert record.status == "failed"
        assert "missing verified owner" in record.last_error.lower()

    mock_client.send_message.assert_not_called()


def test_recovery_respects_active_lease(test_env):
    """Test that startup recovery does NOT mark 'sending' as 'delivery_unknown'
    if another worker still holds an active lease!
    """
    Session, worker, mock_client = test_env
    now = datetime.now(UTC)

    with Session() as db:
        # Active sending job with 60 seconds lease remaining
        up_active = TelegramUpdate(
            update_id=5004,
            user_id=1,
            payload="{}",
            status="ai_received",
            delivery_status="sending",
            lease_expires_at=now + timedelta(seconds=60),
        )
        # Expired sending job
        up_expired = TelegramUpdate(
            update_id=5005,
            user_id=1,
            payload="{}",
            status="ai_received",
            delivery_status="sending",
            lease_expires_at=now - timedelta(seconds=10),
        )
        db.add_all([up_active, up_expired])
        db.commit()

    worker.recover_on_startup()

    with Session() as db:
        rec_active = db.query(TelegramUpdate).filter_by(update_id=5004).first()
        rec_expired = db.query(TelegramUpdate).filter_by(update_id=5005).first()

        # Active job kept untouched!
        assert rec_active.delivery_status == "sending"
        assert rec_active.status == "ai_received"

        # Expired job marked delivery_unknown and failed!
        assert rec_expired.delivery_status == "delivery_unknown"
        assert rec_expired.status == "failed"
