# ruff: noqa: E402
import sys
from pathlib import Path

root_dir = str(Path(__file__).resolve().parent.parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import argparse
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app
from app.telegram.client import TelegramClient
from app.telegram.worker import TelegramAIWorker

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("GatewayTestServer")


def main():
    parser = argparse.ArgumentParser(
        description="Run live Gateway test server for cross-app E2E testing"
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db-url", type=str, required=True)
    parser.add_argument("--agent-url", type=str, required=True)
    parser.add_argument("--gw-to-agent-secret", type=str, required=True)
    parser.add_argument("--agent-to-gw-secret", type=str, required=True)
    parser.add_argument("--tg-mock-url", type=str, required=True)
    parser.add_argument("--webhook-secret", type=str, default="test_webhook_secret_token")
    from cryptography.fernet import Fernet
    parser.add_argument(
        "--encryption-key",
        type=str,
        default=Fernet.generate_key().decode(),
    )

    args = parser.parse_args()

    # Route TelegramClient calls to mock server at outermost boundary
    TelegramClient.API_BASE = args.tg_mock_url.rstrip("/")

    from app.odoo.service import OdooService
    OdooService.validate_user_account = lambda self, uid, secret: {"id": uid, "active": True, "share": False}
    OdooService.compute_permissions = lambda self, uid, secret, is_internal_user=True, google_status="disconnected", google_scopes=None: [
        "tasks.read", "tasks.write", "calendar.read", "calendar.write", "gmail.read", "gmail.send", "crm.read", "crm.create", "crm.write"
    ]

    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=args.db_url,
        ENABLE_AI_AGENT_FORWARDING=True,
        AI_AGENT_BASE_URL=args.agent_url,
        GATEWAY_TO_AGENT_SECRET=args.gw_to_agent_secret,
        AGENT_TO_GATEWAY_SECRET=args.agent_to_gw_secret,
        TELEGRAM_WEBHOOK_SECRET=args.webhook_secret,
        TELEGRAM_BOT_TOKEN="mock_test_token",
        PUBLIC_BASE_URL=f"http://127.0.0.1:{args.port}",
        SECRET_KEY="test_secret_key_32_chars_minimum__",
        ENCRYPTION_KEY=args.encryption_key,
    )

    base_app = create_app(settings)

    # Lifespan wrapper to cleanly manage worker lifecycle
    tele_client = TelegramClient(bot_token=settings.TELEGRAM_BOT_TOKEN)
    worker = TelegramAIWorker(
        settings=settings,
        session_factory=base_app.state.session_factory,
        telegram_client=tele_client,
    )

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        _logger.info("Starting TelegramAIWorker in test server...")
        worker.start()
        yield
        _logger.info("Stopping TelegramAIWorker in test server...")
        worker.stop()

    base_app.router.lifespan_context = test_lifespan

    _logger.info("Launching Gateway test server on port %s...", args.port)
    uvicorn.run(base_app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
