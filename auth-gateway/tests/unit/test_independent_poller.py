from unittest.mock import MagicMock, patch

from app.config import Settings
from scripts.telegram_poller import setup_poller_worker


def test_poller_starts_worker_when_forwarding_enabled():
    settings = Settings(
        ENABLE_AI_AGENT_FORWARDING=True,
        GATEWAY_TO_AGENT_SECRET="a" * 32,
        AGENT_TO_GATEWAY_SECRET="b" * 32,
    )
    mock_session_factory = MagicMock()
    mock_client = MagicMock()

    with patch("scripts.telegram_poller.TelegramAIWorker") as mock_worker_cls:
        worker_instance = MagicMock()
        mock_worker_cls.return_value = worker_instance

        worker = setup_poller_worker(settings, mock_session_factory, mock_client)

        assert worker is not None
        mock_worker_cls.assert_called_once_with(
            settings=settings,
            session_factory=mock_session_factory,
            telegram_client=mock_client,
        )
        worker_instance.start.assert_called_once()


def test_poller_does_not_start_worker_when_forwarding_disabled():
    settings = Settings(ENABLE_AI_AGENT_FORWARDING=False)
    mock_session_factory = MagicMock()
    mock_client = MagicMock()

    with patch("scripts.telegram_poller.TelegramAIWorker") as mock_worker_cls:
        worker = setup_poller_worker(settings, mock_session_factory, mock_client)

        assert worker is None
        mock_worker_cls.assert_not_called()
