"""Integration test demonstrating two independent worker processes coordinating OAuth refresh via database lease
and preventing stale refresh tokens from overwriting newly relinked accounts.
"""
import concurrent.futures
from datetime import datetime, timedelta, UTC
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import socket
import subprocess
import sys
import threading
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, User, GoogleConnection
from app.workspace.models import OAuthRefreshLease
from app.google.oauth import GoogleOAuthService


FERNET_KEY = "_3pDgavXi1u3T76np_zSNJQcNS87hKW7fEdQXjlu3Ew="


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def multi_worker_db(tmp_path):
    db_file = tmp_path / "multi_worker.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    from app.security import encrypt_secret
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
            encrypted_access_token=encrypt_secret("old_access_token", FERNET_KEY),
            encrypted_refresh_token=encrypt_secret("test_refresh_token", FERNET_KEY),
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
            status="connected",
            connection_version=1,
        )
        db.add(conn)
        db.commit()

    return session_factory, db_file


def test_two_workers_coordinate_oauth_refresh_single_execution(multi_worker_db):
    """Demonstrates that two independent worker service instances (Worker A & Worker B)
    both calling get_valid_access_token with force_refresh=True simultaneously coordinate
    through database lease locking with simulated network delay, executing exactly ONE
    network refresh, incrementing connection_version, and both receiving the new token.
    """
    session_factory, _ = multi_worker_db

    service_a = GoogleOAuthService(
        session_factory=session_factory,
        client_id="cid",
        client_secret="sec",
        redirect_uri="uri",
        encryption_key=FERNET_KEY,
    )
    service_b = GoogleOAuthService(
        session_factory=session_factory,
        client_id="cid",
        client_secret="sec",
        redirect_uri="uri",
        encryption_key=FERNET_KEY,
    )

    refresh_counter = 0

    def mock_perform_refresh(user_id):
        nonlocal refresh_counter
        refresh_counter += 1
        # Simulate real network I/O latency to Google
        time.sleep(0.25)
        return "brand_new_token_coordinated_999", 3600

    service_a._perform_network_refresh = mock_perform_refresh
    service_b._perform_network_refresh = mock_perform_refresh

    # Worker A and Worker B call get_valid_access_token with force_refresh=True concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_a = executor.submit(service_a.get_valid_access_token, 1, force_refresh=True)
        fut_b = executor.submit(service_b.get_valid_access_token, 1, force_refresh=True)
        res_a = fut_a.result(timeout=10.0)
        res_b = fut_b.result(timeout=10.0)

    # Both workers received the valid refreshed token
    assert res_a == "brand_new_token_coordinated_999"
    assert res_b == "brand_new_token_coordinated_999"

    # Exactly 1 network refresh took place (no thundering herd / no duplicate refresh)
    assert refresh_counter == 1

    # In database, connection_version was incremented on both GoogleConnection and OAuthRefreshLease
    with session_factory() as db:
        conn = db.query(GoogleConnection).filter_by(user_id=1).first()
        lease = db.query(OAuthRefreshLease).filter_by(user_id=1).first()
        assert conn is not None
        assert conn.connection_version >= 2
        assert lease is not None
        assert lease.connection_version >= 2
        assert lease.lease_holder is None  # Lease was released


def test_relink_during_refresh_discards_stale_token_and_preserves_new_connection(multi_worker_db):
    """Demonstrates that if a user changes or relinks their Google connection while a token refresh
    is in-flight over the network, the worker detects the connection_version mismatch upon completion,
    discards the stale token from the old account, does NOT overwrite the new account's credentials,
    and returns the new connection's valid token.
    """
    session_factory, _ = multi_worker_db

    service = GoogleOAuthService(
        session_factory=session_factory,
        client_id="cid",
        client_secret="sec",
        redirect_uri="uri",
        encryption_key=FERNET_KEY,
    )

    def mock_perform_refresh_with_concurrent_relink(user_id):
        # Simulate network latency
        time.sleep(0.1)

        # WHILE network call is in flight, the user relinks Google account on Portal!
        with session_factory() as db:
            conn = db.query(GoogleConnection).filter_by(user_id=user_id).first()
            conn.google_sub = "new_sub_user_456"
            conn.email = "new_user@company.com"
            conn.encrypted_access_token = service._encrypt("brand_new_relinked_token_888")
            conn.encrypted_refresh_token = service._encrypt("brand_new_relinked_refresh_888")
            conn.connection_version = conn.connection_version + 1  # version 1 -> 2
            db.commit()

        time.sleep(0.1)
        # Returns the stale token of the old account
        return "stale_token_from_old_account_111", 3600

    service._perform_network_refresh = mock_perform_refresh_with_concurrent_relink

    # Call get_valid_access_token
    token = service.get_valid_access_token(1, force_refresh=True)

    # Must return the new relinked connection's token, NOT the stale one!
    assert token == "brand_new_relinked_token_888"

    # In database, the new account's tokens and sub must NOT be overwritten!
    with session_factory() as db:
        conn = db.query(GoogleConnection).filter_by(user_id=1).first()
        assert conn.google_sub == "new_sub_user_456"
        assert conn.email == "new_user@company.com"
        assert service._decrypt(conn.encrypted_access_token) == "brand_new_relinked_token_888"
        assert service._decrypt(conn.encrypted_refresh_token) == "brand_new_relinked_refresh_888"
        assert conn.connection_version >= 2


def test_two_os_processes_coordinate_oauth_refresh_single_network_call(multi_worker_db):
    """Spawns TWO REAL SEPARATE OS PYTHON PROCESSES that concurrently call get_valid_access_token
    against a shared database and a mock Google token HTTP endpoint.
    Proves that cross-process coordination works and only EXACTLY 1 network refresh request is dispatched.
    """
    session_factory, db_file = multi_worker_db

    class MockGoogleTokenHandler(BaseHTTPRequestHandler):
        refresh_requests = 0

        def do_POST(self):
            # Simulate real network latency (300ms)
            time.sleep(0.3)
            MockGoogleTokenHandler.refresh_requests += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"access_token": "token_multi_proc_999", "expires_in": 3600}')

        def log_message(self, format, *args):
            pass

    port = find_free_port()
    MockGoogleTokenHandler.refresh_requests = 0
    server = HTTPServer(("127.0.0.1", port), MockGoogleTokenHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    worker_script = f"""
import sys
import os
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.google.oauth import GoogleOAuthService

db_url = "sqlite:///" + sys.argv[1].replace("\\\\", "/")
token_port = sys.argv[2]
worker_name = sys.argv[3]

engine = create_engine(db_url)
session_factory = sessionmaker(bind=engine)

service = GoogleOAuthService(
    session_factory=session_factory,
    client_id="cid",
    client_secret="sec",
    redirect_uri="uri",
    encryption_key="{FERNET_KEY}",
)
service.TOKEN_ENDPOINT = f"http://127.0.0.1:{{token_port}}/token"

token = service.get_valid_access_token(1, force_refresh=True)
print(f"SUCCESS:{{token}}")
"""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    # Spawn two independent OS processes
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_script, str(db_file), str(port), "proc_1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_script, str(db_file), str(port), "proc_2"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    out1, err1 = p1.communicate(timeout=15.0)
    out2, err2 = p2.communicate(timeout=15.0)

    server.shutdown()
    server.server_close()

    assert p1.returncode == 0, f"Process 1 failed: {err1}"
    assert p2.returncode == 0, f"Process 2 failed: {err2}"

    assert "SUCCESS:token_multi_proc_999" in out1
    assert "SUCCESS:token_multi_proc_999" in out2

    # Exactly 1 HTTP POST network request arrived at Google token endpoint!
    assert MockGoogleTokenHandler.refresh_requests == 1

    with session_factory() as db:
        conn = db.query(GoogleConnection).filter_by(user_id=1).first()
        lease = db.query(OAuthRefreshLease).filter_by(user_id=1).first()
        assert conn.connection_version >= 2
        assert lease.connection_version >= 2
