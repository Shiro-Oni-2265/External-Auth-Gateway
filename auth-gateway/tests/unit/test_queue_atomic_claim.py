import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base, TelegramUpdate
from app.security import ensure_utc
from app.telegram.worker import TelegramAIWorker


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session


def test_ensure_utc_sqlite_comparison(test_db):
    """Test that SQLite naive datetime when retrieved must be converted via ensure_utc()
    before comparing with datetime.now(UTC), otherwise offset-naive vs offset-aware error occurs.
    """
    _, Session = test_db
    with Session() as db:
        now_aware = datetime.now(UTC)
        up = TelegramUpdate(
            update_id=101,
            payload="{}",
            status="processing_ai",
            lease_expires_at=now_aware + timedelta(seconds=90),
        )
        db.add(up)
        db.commit()

        db.refresh(up)
        naive_dt = up.lease_expires_at

        with pytest.raises(TypeError):
            _ = naive_dt > now_aware

        utc_dt = ensure_utc(naive_dt)
        assert utc_dt > now_aware


def test_atomic_claim_with_claim_token(test_db):
    """Test that two workers attempting to claim the same pending update concurrently
    using atomic conditional UPDATE: only ONE worker gets rowcount == 1, the second gets 0.
    """
    _, Session = test_db
    settings = Settings()

    with Session() as db:
        up = TelegramUpdate(
            update_id=202,
            payload='{"message": {"chat": {"id": 123}, "from": {"id": 456}, "text": "Hello"}}',
            status="pending",
        )
        db.add(up)
        db.commit()
        up_id = up.id

    worker1 = TelegramAIWorker(settings=settings, session_factory=Session)
    worker2 = TelegramAIWorker(settings=settings, session_factory=Session)

    token1 = secrets.token_hex(16)
    token2 = secrets.token_hex(16)

    claimed1 = worker1._claim_update_for_ai(up_id, token1)
    assert claimed1 is True

    claimed2 = worker2._claim_update_for_ai(up_id, token2)
    assert claimed2 is False

    with Session() as db:
        record = db.query(TelegramUpdate).filter_by(id=up_id).first()
        assert record.status == "processing_ai"
        assert record.claim_token == token1
        assert record.attempts == 1


def test_claim_token_protects_mutations(test_db):
    """Test that if a worker loses its claim (e.g. lease expired and reclaimed),
    subsequent mutations using the old claim token fail and do not overwrite DB.
    """
    _, Session = test_db
    settings = Settings()

    with Session() as db:
        up = TelegramUpdate(
            update_id=303,
            payload='{"message": {"chat": {"id": 123}, "from": {"id": 456}, "text": "Hello"}}',
            status="pending",
        )
        db.add(up)
        db.commit()
        up_id = up.id

    worker = TelegramAIWorker(settings=settings, session_factory=Session)
    token1 = secrets.token_hex(16)
    token2 = secrets.token_hex(16)

    assert worker._claim_update_for_ai(up_id, token1) is True

    # Simulate worker 2 taking over by updating claim_token
    with Session() as db:
        db.query(TelegramUpdate).filter_by(id=up_id).update({"claim_token": token2})
        db.commit()

    # Worker 1 attempts to save AI response with old token1
    res = worker._save_ai_response(up_id, token1, '["AI reply"]')
    assert res is False
