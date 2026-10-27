from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Base, TelegramUpdate
from app.telegram.worker import TelegramAIWorker


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings()
    worker = TelegramAIWorker(settings=settings, session_factory=Session)
    return Session, worker


def test_sweep_expired_sending_at_startup_and_process_pending(env):
    """Test that sweep_expired_sending transitions expired sending records to delivery_unknown
    both at startup and at the start of process_pending_once, but leaves active leases intact.
    """
    Session, worker = env
    now = datetime.now(UTC)

    with Session() as db:
        # Job 1: Sending with active lease (40 seconds remaining)
        up_active = TelegramUpdate(
            update_id=7001,
            user_id=1,
            payload="{}",
            status="ai_received",
            delivery_status="sending",
            lease_expires_at=now + timedelta(seconds=40),
            claim_token="active_token",
            delivery_index=1,
            delivery_chunks='["c1", "c2"]',
        )
        # Job 2: Sending with expired lease (expired 10 seconds ago)
        up_expired = TelegramUpdate(
            update_id=7002,
            user_id=1,
            payload="{}",
            status="ai_received",
            delivery_status="sending",
            lease_expires_at=now - timedelta(seconds=10),
            claim_token="expired_token",
            delivery_index=1,
            delivery_chunks='["c1", "c2"]',
        )
        db.add_all([up_active, up_expired])
        db.commit()

    # 1. Startup recovery sweeps expired
    worker.recover_on_startup()

    with Session() as db:
        rec_active = db.query(TelegramUpdate).filter_by(update_id=7001).first()
        rec_expired = db.query(TelegramUpdate).filter_by(update_id=7002).first()

        # Active job must remain untouched!
        assert rec_active.delivery_status == "sending"
        assert rec_active.claim_token == "active_token"

        # Expired job marked delivery_unknown and failed, claim cleared, chunks/index preserved
        assert rec_expired.delivery_status == "delivery_unknown"
        assert rec_expired.status == "failed"
        assert rec_expired.claim_token is None
        assert rec_expired.delivery_index == 1
        assert rec_expired.delivery_chunks == '["c1", "c2"]'

    # 2. Fast forward: active job now expires
    with Session() as db:
        db.query(TelegramUpdate).filter_by(update_id=7001).update(
            {"lease_expires_at": now - timedelta(seconds=5)}
        )
        db.commit()

    # Calling process_pending_once() should sweep Job 1 on its own without needing a restart!
    worker.process_pending_once()

    with Session() as db:
        rec_active_now_swept = db.query(TelegramUpdate).filter_by(update_id=7001).first()
        assert rec_active_now_swept.delivery_status == "delivery_unknown"
        assert rec_active_now_swept.status == "failed"
        assert rec_active_now_swept.claim_token is None
        assert rec_active_now_swept.delivery_index == 1


def test_renewed_lease_not_swept_and_delivery_unknown_not_retried(env):
    """Test that renewed leases are not swept, and jobs in delivery_unknown
    are never picked up or retried by process_pending_once.
    """
    Session, worker = env
    now = datetime.now(UTC)

    with Session() as db:
        # Job A: lease renewed in future
        job_a = TelegramUpdate(
            update_id=7010,
            user_id=1,
            payload="{}",
            status="ai_received",
            delivery_status="sending",
            lease_expires_at=now + timedelta(seconds=60),
            claim_token="active_lease_token",
            delivery_index=1,
            delivery_chunks='["c1", "c2"]',
        )
        # Job B: already in delivery_unknown
        job_b = TelegramUpdate(
            update_id=7011,
            user_id=1,
            payload="{}",
            status="failed",
            delivery_status="delivery_unknown",
            lease_expires_at=None,
            claim_token=None,
            delivery_index=1,
            delivery_chunks='["c1", "c2"]',
        )
        db.add_all([job_a, job_b])
        db.commit()

    # Run sweep
    swept_count = worker._sweep_expired_sending()
    assert swept_count == 0

    # Run process_pending_once
    processed = worker.process_pending_once()
    assert processed == 0

    with Session() as db:
        rec_a = db.query(TelegramUpdate).filter_by(update_id=7010).first()
        rec_b = db.query(TelegramUpdate).filter_by(update_id=7011).first()

        # Job A remains sending with active token
        assert rec_a.delivery_status == "sending"
        assert rec_a.claim_token == "active_lease_token"

        # Job B remains delivery_unknown, never retried blindly
        assert rec_b.delivery_status == "delivery_unknown"
        assert rec_b.status == "failed"
