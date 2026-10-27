import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base, TelegramConnection, TelegramUpdate, User
from app.telegram.client import TelegramClient
from app.telegram.worker import TelegramAIWorker


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings()
    mock_client = MagicMock(spec=TelegramClient)
    worker = TelegramAIWorker(
        settings=settings, session_factory=Session, telegram_client=mock_client
    )

    # Seed User 1 & Connection
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


def test_recipient_revoked_after_first_chunk_stops_second_chunk(env):
    """Test that if a user is deactivated or connection disconnected AFTER chunk 1,
    chunk 2 is NOT sent, delivery_index remains at 1, and status becomes failed.
    """
    Session, worker, mock_client = env

    chunk1 = "Chunk 1 content"
    chunk2 = "Chunk 2 content"
    messages = [chunk1, chunk2]

    with Session() as db:
        up = TelegramUpdate(
            update_id=6001,
            user_id=1,
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps(messages),
            delivery_status="none",
        )
        db.add(up)
        db.commit()

    send_call_count = 0

    def mock_send(chat_id, text, parse_mode=""):
        nonlocal send_call_count
        send_call_count += 1
        if send_call_count == 1:
            # During/after chunk 1 delivery, user 1 gets deactivated in DB!
            with Session() as db:
                db.query(User).filter_by(id=1).update({"active": False})
                db.commit()
            return {"ok": True}
        return {"ok": True}

    mock_client.send_message.side_effect = mock_send

    # Process delivery
    worker.process_pending_once()

    # Crucial assertions:
    # 1. TelegramClient.send_message was called ONLY ONCE (for chunk 1), NOT for chunk 2!
    assert send_call_count == 1

    # 2. Database record state:
    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=6001).first()
        # delivery_index preserved at 1 (chunk 1 was confirmed)
        assert record.delivery_index == 1
        assert record.status == "failed"
        assert record.delivery_status == "failed"
        assert (
            "deactivated" in record.last_error.lower() or "recipient" in record.last_error.lower()
        )


def test_recipient_rebound_after_first_chunk_stops_second_chunk(env):
    """Test that if connection is rebound to another user after chunk 1,
    chunk 2 is NOT sent to prevent sending private data to wrong user.
    """
    Session, worker, mock_client = env

    chunk1 = "Confidential report part 1"
    chunk2 = "Confidential report part 2"

    with Session() as db:
        # Create user 2
        user2 = User(
            id=2,
            tenant="t1",
            database="db1",
            odoo_uid=102,
            login="u2@test.com",
            name="User 2",
            active=True,
        )
        db.add(user2)
        up = TelegramUpdate(
            update_id=6002,
            user_id=1,
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps([chunk1, chunk2]),
            delivery_status="none",
        )
        db.add(up)
        db.commit()

    send_call_count = 0

    def mock_send(chat_id, text, parse_mode=""):
        nonlocal send_call_count
        send_call_count += 1
        if send_call_count == 1:
            # Rebind telegram connection 111 to user 2
            with Session() as db:
                db.query(TelegramConnection).filter_by(telegram_user_id=111).update({"user_id": 2})
                db.commit()
            return {"ok": True}
        return {"ok": True}

    mock_client.send_message.side_effect = mock_send

    worker.process_pending_once()

    assert send_call_count == 1
    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=6002).first()
        assert record.delivery_index == 1
        assert record.status == "failed"
        assert record.delivery_status == "failed"


def test_recipient_disconnected_after_first_chunk_stops_second_chunk(env):
    """Test that if connection status becomes disconnected after chunk 1,
    chunk 2 is NOT sent and delivery_index is preserved at 1.
    """
    Session, worker, mock_client = env

    chunk1 = "Important notice part 1"
    chunk2 = "Important notice part 2"

    with Session() as db:
        up = TelegramUpdate(
            update_id=6003,
            user_id=1,
            payload=json.dumps(
                {"message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "/today"}}
            ),
            status="ai_received",
            ai_response=json.dumps([chunk1, chunk2]),
            delivery_status="none",
        )
        db.add(up)
        db.commit()

    send_call_count = 0

    def mock_send(chat_id, text, parse_mode=""):
        nonlocal send_call_count
        send_call_count += 1
        if send_call_count == 1:
            with Session() as db:
                db.query(TelegramConnection).filter_by(telegram_user_id=111).update(
                    {"status": "disconnected"}
                )
                db.commit()
            return {"ok": True}
        return {"ok": True}

    mock_client.send_message.side_effect = mock_send

    worker.process_pending_once()

    assert send_call_count == 1
    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(update_id=6003).first()
        assert record.delivery_index == 1
        assert record.status == "failed"
        assert record.delivery_status == "failed"
        assert record.claim_token is None
        assert record.lease_expires_at is None
