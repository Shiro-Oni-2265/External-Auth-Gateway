"""Integration test demonstrating two independent worker processes coordinating OAuth refresh via PostgreSQL/SQLite lease."""
import asyncio
from datetime import datetime, timedelta, UTC
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, User, GoogleConnection
from app.workspace.models import OAuthRefreshLease
from app.google.oauth import GoogleOAuthService


@pytest.fixture
def multi_worker_db(tmp_path):
    db_file = tmp_path / "multi_worker.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        user = User(
            id=1,
            tenant="multitenant",
            database="testdb",
            odoo_uid=1,
            login="user1@company.com",
            name="User One",
        )
        db.add(user)
        db.commit()

        # Token expiring in 2 minutes (< 5 minutes threshold)
        conn = GoogleConnection(
            user_id=1,
            google_sub="sub_multi",
            email="user1@company.com",
            scopes="email calendar",
            encrypted_access_token="old_acc",
            encrypted_refresh_token="enc_refresh_tok",
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
            status="connected",
        )
        db.add(conn)
        db.commit()

    return session_factory


def test_two_workers_coordinate_oauth_refresh_single_execution(multi_worker_db):
    """Demonstrates that two independent worker service instances (Worker A & Worker B)
    attempting to get a valid token simultaneously coordinate through database lease locking,
    executing exactly ONE network refresh, incrementing connection_version, and both receiving the new token.
    """
    session_factory = multi_worker_db

    fernet_key = "_3pDgavXi1u3T76np_zSNJQcNS87hKW7fEdQXjlu3Ew="
    # Worker A service
    service_a = GoogleOAuthService(
        session_factory=session_factory,
        client_id="cid",
        client_secret="sec",
        redirect_uri="uri",
        encryption_key=fernet_key,
    )
    # Worker B service
    service_b = GoogleOAuthService(
        session_factory=session_factory,
        client_id="cid",
        client_secret="sec",
        redirect_uri="uri",
        encryption_key=fernet_key,
    )

    refresh_counter = 0

    def mock_perform_refresh(user_id):
        nonlocal refresh_counter
        refresh_counter += 1
        return "brand_new_token_coordinated_999", 3600

    service_a._perform_network_refresh = mock_perform_refresh
    service_b._perform_network_refresh = mock_perform_refresh

    # Worker A and Worker B call get_valid_access_token concurrently across threads
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_a = executor.submit(service_a.get_valid_access_token, 1)
        fut_b = executor.submit(service_b.get_valid_access_token, 1)
        res_a = fut_a.result(timeout=10.0)
        res_b = fut_b.result(timeout=10.0)

    # Both workers received the valid refreshed token
    assert res_a == "brand_new_token_coordinated_999"
    assert res_b == "brand_new_token_coordinated_999"

    # Exactly 1 network refresh took place
    assert refresh_counter == 1

    # In database, connection_version was incremented
    with session_factory() as db:
        lease = db.query(OAuthRefreshLease).filter_by(user_id=1).first()
        assert lease is not None
        assert lease.connection_version >= 2
        assert lease.lease_holder is None  # Lease released after completion
