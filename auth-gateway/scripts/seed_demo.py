#!/usr/bin/env python3
"""
Seed a demo user and session for local UI testing
"""

import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.db import init_db
from app.models import (
    CalendarPair,
    GoogleConnection,
    OdooCredential,
    Session,
    SyncIntent,
    TelegramConnection,
    User,
)
from app.security import encrypt_secret, hash_token


def seed():
    settings = Settings()
    _, session_factory = init_db(settings.DATABASE_URL)

    with session_factory() as db:
        # Check if demo user exists
        user = db.query(User).filter_by(login="demo@company.com").first()
        if not user:
            user = User(
                tenant=settings.ODOO_URL,
                database=settings.ODOO_DB,
                odoo_uid=99,
                login="demo@company.com",
                name="Nguyễn Văn Demo",
                tz="Asia/Bangkok",
                active=True,
            )
            db.add(user)
            db.flush()

            # Credential
            db.add(
                OdooCredential(
                    user_id=user.id,
                    credential_kind="api_key",
                    encrypted_secret=encrypt_secret("demo_api_key_123", settings.ENCRYPTION_KEY),
                )
            )

            # Sample Google Connection
            db.add(
                GoogleConnection(
                    user_id=user.id,
                    google_sub="google_sub_demo_99",
                    email="demo@company.com",
                    scopes="openid email https://www.googleapis.com/auth/calendar.events",
                    encrypted_access_token=encrypt_secret(
                        "demo_access_token", settings.ENCRYPTION_KEY
                    ),
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                    status="connected",
                )
            )

            # Sample Telegram Connection
            db.add(
                TelegramConnection(
                    user_id=user.id,
                    telegram_user_id=987654321,
                    private_chat_id=987654321,
                    username="demo_telegram_user",
                    status="connected",
                )
            )

            # Sample Conflict
            db.add(
                CalendarPair(
                    user_id=user.id,
                    google_subject="google_sub_demo_99",
                    google_calendar_id="primary",
                    odoo_event_id=101,
                    google_event_id="demo_google_event_101",
                    status="conflict",
                )
            )

            # Sample Ambiguous Intent
            db.add(
                SyncIntent(
                    user_id=user.id,
                    source="google",
                    target="odoo",
                    source_event_id="g_intent_202",
                    status="ambiguous",
                )
            )

        # Create or update an active session for quick browser login
        demo_session_token = "demo_session_secret_token_12345"
        token_hash = hash_token(demo_session_token)

        existing_sess = db.query(Session).filter_by(token_hash=token_hash).first()
        if not existing_sess:
            db.add(
                Session(
                    token_hash=token_hash,
                    user_id=user.id,
                    csrf_secret=secrets.token_urlsafe(16),
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
        db.commit()

    print("\n=== Demo data seeded successfully! ===")
    print("User Login: demo@company.com")
    print("Demo Session Cookie: session_id=demo_session_secret_token_12345\n")


if __name__ == "__main__":
    seed()
