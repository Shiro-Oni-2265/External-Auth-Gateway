#!/usr/bin/env python3
"""
Development Telegram Poller
Polls Telegram updates via getUpdates and processes them locally
Allowing full Telegram bot testing on localhost without setting up a public webhook tunnel.
"""

import logging
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import Settings
from app.db import init_db
from app.odoo.client import OdooClient
from app.telegram.client import TelegramClient
from app.telegram.service import TelegramService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_logger = logging.getLogger("telegram_poller")


def run_poller():
    settings = Settings()
    if not settings.TELEGRAM_BOT_TOKEN or "dummy" in settings.TELEGRAM_BOT_TOKEN:
        _logger.error("TELEGRAM_BOT_TOKEN is not set properly in .env")
        return

    _, session_factory = init_db(settings.DATABASE_URL)
    telegram_client = TelegramClient(bot_token=settings.TELEGRAM_BOT_TOKEN)
    odoo_client = OdooClient(url=settings.ODOO_URL, db=settings.ODOO_DB)

    telegram_service = TelegramService(
        session_factory=session_factory,
        telegram_client=telegram_client,
        bot_username=settings.TELEGRAM_BOT_USERNAME,
        webhook_secret=settings.TELEGRAM_WEBHOOK_SECRET,
        odoo_client=odoo_client,
        encryption_key=settings.ENCRYPTION_KEY or "",
        odoo_base_url=settings.ODOO_URL,
    )

    _logger.info(f"Starting Telegram Poller for @{settings.TELEGRAM_BOT_USERNAME}...")
    _logger.info("Listening for Telegram messages/clicks in real-time...")

    offset = None
    api_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset

            resp = httpx.get(api_url, params=params, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg_text = update.get("message", {}).get("text")
                        _logger.info(
                            f"Processing Telegram Update #{update['update_id']} | Text: {msg_text}"
                        )
                        telegram_service.process_update_dict(update)
            else:
                _logger.warning(f"Failed to fetch updates from Telegram: {resp.text}")
                time.sleep(2)
        except Exception as e:
            _logger.error(f"Error in poller loop: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run_poller()
