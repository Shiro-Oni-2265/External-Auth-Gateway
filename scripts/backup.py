#!/usr/bin/env python3
"""
SQLite Online Backup Script for External Auth Gateway
Utilizes sqlite3.Connection.backup() to produce a hot, consistent snapshot
even while SQLite WAL writes are concurrently occurring.
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_logger = logging.getLogger("backup")


def backup_database(source_db_path: str, destination_dir: str) -> Path:
    source_path = Path(source_db_path).resolve()
    if not source_path.exists():
        _logger.error(f"Source database file not found: {source_path}")
        sys.exit(1)

    dest_dir = Path(destination_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = dest_dir / f"gateway_backup_{timestamp}.db"

    _logger.info(f"Starting online backup from {source_path} to {backup_file}...")

    src_conn = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(backup_file)

    try:
        # SQLite online backup API
        with dest_conn:
            src_conn.backup(dest_conn, pages=100)
        _logger.info(
            f"Backup completed successfully: {backup_file} ({backup_file.stat().st_size} bytes)"
        )
        return backup_file
    except Exception as e:
        _logger.error(f"Backup failed: {e}")
        if backup_file.exists():
            backup_file.unlink()
        sys.exit(1)
    finally:
        dest_conn.close()
        src_conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backup External Auth Gateway SQLite database")
    parser.add_argument(
        "--source",
        default=os.environ.get("DATABASE_PATH", "/data/gateway.db"),
        help="Path to source SQLite database file",
    )
    parser.add_argument(
        "--dest",
        default=os.environ.get("BACKUP_DIR", "./backups"),
        help="Destination directory for backups",
    )
    args = parser.parse_args()

    # If DATABASE_URL is in environment as sqlite:///path, extract path
    db_url = os.environ.get("DATABASE_URL")
    src = args.source
    if db_url and db_url.startswith("sqlite:///"):
        src = db_url.replace("sqlite:///", "")

    backup_database(src, args.dest)


if __name__ == "__main__":
    main()
