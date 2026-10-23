import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import Base


def create_sqlite_engine(db_url: str) -> Engine:
    # Ensure directory exists for file-based sqlite
    if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:///:memory:"):
        db_file = db_url.replace("sqlite:///", "")
        db_path = Path(db_file)
        if not db_path.is_absolute():
            db_path = Path(os.getcwd()) / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    return engine


def init_db(db_url: str) -> tuple[Engine, sessionmaker]:
    engine = create_sqlite_engine(db_url)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, session_factory


def get_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
