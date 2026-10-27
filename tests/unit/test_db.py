import pytest
from sqlalchemy.exc import IntegrityError

from app.db import init_db
from app.models import OdooCredential, User


def test_db_persistence_and_wal(temp_db_path):
    engine1, session_factory1 = init_db(f"sqlite:///{temp_db_path}")
    with session_factory1() as session:
        user = User(
            tenant="test_tenant",
            database="test_db",
            odoo_uid=10,
            login="user1@example.com",
            name="Test User 1",
            partner_id=5,
            tz="Asia/Bangkok",
            active=True,
        )
        session.add(user)
        session.commit()
    engine1.dispose()

    # Reopen database to verify persistence
    engine2, session_factory2 = init_db(f"sqlite:///{temp_db_path}")
    with session_factory2() as session:
        saved_user = session.query(User).filter_by(odoo_uid=10).first()
        assert saved_user is not None
        assert saved_user.login == "user1@example.com"
        assert saved_user.name == "Test User 1"
    engine2.dispose()


def test_db_unique_tenant_db_uid(temp_db_path):
    engine, session_factory = init_db(f"sqlite:///{temp_db_path}")
    with session_factory() as session:
        u1 = User(
            tenant="tenant1",
            database="db1",
            odoo_uid=42,
            login="first@example.com",
            name="First",
        )
        session.add(u1)
        session.commit()

        u2 = User(
            tenant="tenant1",
            database="db1",
            odoo_uid=42,  # Duplicate tenant+db+uid
            login="second@example.com",
            name="Second",
        )
        session.add(u2)
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


def test_db_foreign_keys_enforced(temp_db_path):
    engine, session_factory = init_db(f"sqlite:///{temp_db_path}")
    with session_factory() as session:
        cred = OdooCredential(
            user_id=9999,  # Non-existent user
            credential_kind="password",
            encrypted_secret="enc_secret",
        )
        session.add(cred)
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
