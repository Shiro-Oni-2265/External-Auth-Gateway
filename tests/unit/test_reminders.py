from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.calendar.reminders import ReminderService
from app.db import init_db
from app.models import (
    CalendarPair,
    GoogleConnection,
    OdooCredential,
    Outbox,
    TelegramConnection,
    User,
)
from app.security import encrypt_secret
from app.telegram.client import TelegramClient


@pytest.fixture
def reminder_components(temp_db_path, fernet_key):
    engine, session_factory = init_db(f"sqlite:///{temp_db_path}")

    # Create User
    with session_factory() as db:
        user = User(
            tenant="https://mycompany.odoo.com",
            database="mycompany",
            odoo_uid=88,
            login="user88@company.com",
            name="Meeting User",
            tz="Asia/Bangkok",
        )
        db.add(user)
        db.flush()
        db.add(
            OdooCredential(
                user_id=user.id,
                credential_kind="api_key",
                encrypted_secret=encrypt_secret("key_88", fernet_key),
            )
        )
        db.add(
            GoogleConnection(
                user_id=user.id,
                google_sub="sub_88",
                email="user88@company.com",
                scopes="calendar",
                encrypted_access_token="acc",
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                status="connected",
            )
        )
        db.add(
            TelegramConnection(
                user_id=user.id,
                telegram_user_id=88888,
                private_chat_id=88888,
                status="connected",
            )
        )
        db.commit()
        user_id = user.id

    mock_telegram = MagicMock(spec=TelegramClient)
    service = ReminderService(
        session_factory=session_factory,
        telegram_client=mock_telegram,
        encryption_key=fernet_key,
        odoo_base_url="https://mycompany.odoo.com",
    )

    yield service, session_factory, user_id, mock_telegram
    engine.dispose()


def test_reminder_enqueued_in_15_min_window(reminder_components):
    service, session_factory, user_id, _ = reminder_components

    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=UTC)
    meeting_start = now + timedelta(minutes=14)  # 14 mins from now (within 15m window)

    with session_factory() as db:
        pair = CalendarPair(
            user_id=user_id,
            google_subject="sub_88",
            google_calendar_id="primary",
            odoo_event_id=901,
            google_event_id="g_901",
            status="synced",
        )
        db.add(pair)
        db.commit()
        pair_id = pair.id

    mock_odoo_event = {
        "id": 901,
        "name": "Họp Ban Giám Đốc",
        "location": "Phòng Boardroom",
        "start": meeting_start.strftime("%Y-%m-%d %H:%M:%S"),
        "stop": (meeting_start + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "allday": False,
        "active": True,
    }

    count = service.enqueue_due(now_utc=now, mock_odoo_events={901: mock_odoo_event})
    assert count == 1

    # Verify outbox item created
    with session_factory() as db:
        out = db.query(Outbox).filter_by(user_id=user_id).first()
        assert out is not None
        assert out.status == "pending"
        assert f"reminder_{user_id}_{pair_id}" in out.dedup_key
        assert "Họp Ban Giám Đốc" in out.payload
        assert "Phòng Boardroom" in out.payload


def test_reminder_ignores_allday_and_past_events(reminder_components):
    service, session_factory, user_id, _ = reminder_components
    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=UTC)

    # 1. Past event (already started)
    past_start = now - timedelta(minutes=5)
    # 2. All-day event
    with session_factory() as db:
        p1 = CalendarPair(
            user_id=user_id,
            google_subject="sub_88",
            google_calendar_id="primary",
            odoo_event_id=902,
            google_event_id="g_902",
            status="synced",
        )
        p2 = CalendarPair(
            user_id=user_id,
            google_subject="sub_88",
            google_calendar_id="primary",
            odoo_event_id=903,
            google_event_id="g_903",
            status="synced",
        )
        db.add_all([p1, p2])
        db.commit()

    mock_events = {
        902: {
            "id": 902,
            "name": "Họp Quá Giờ",
            "start": past_start.strftime("%Y-%m-%d %H:%M:%S"),
            "allday": False,
            "active": True,
        },
        903: {
            "id": 903,
            "name": "Nghỉ Lễ",
            "start": now.strftime("%Y-%m-%d %H:%M:%S"),
            "allday": True,  # All day
            "active": True,
        },
    }

    count = service.enqueue_due(now_utc=now, mock_odoo_events=mock_events)
    assert count == 0

    with session_factory() as db:
        assert db.query(Outbox).count() == 0


def test_outbox_deliver_due(reminder_components):
    service, session_factory, user_id, mock_telegram = reminder_components
    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=UTC)

    with session_factory() as db:
        out = Outbox(
            user_id=user_id,
            dedup_key="test_remind_1",
            message_type="reminder",
            payload="⏰ Nhắc họp: Bạn có cuộc họp lúc 10:15!",
            status="pending",
            next_attempt_at=now,
        )
        db.add(out)
        db.commit()

    delivered = service.deliver_due(now_utc=now)
    assert delivered == 1

    mock_telegram.send_message.assert_called_once_with(
        chat_id=88888, text="⏰ Nhắc họp: Bạn có cuộc họp lúc 10:15!"
    )

    with session_factory() as db:
        delivered_out = db.query(Outbox).filter_by(dedup_key="test_remind_1").first()
        assert delivered_out.status == "sent"
