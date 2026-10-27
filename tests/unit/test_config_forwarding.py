import pytest
from pydantic import ValidationError

from app.config import Settings


def test_gateway_secrets_default_empty():
    s = Settings(
        ENABLE_AI_AGENT_FORWARDING=False,
        GATEWAY_TO_AGENT_SECRET="",
        AGENT_TO_GATEWAY_SECRET="",
    )
    assert s.GATEWAY_TO_AGENT_SECRET == ""
    assert s.AGENT_TO_GATEWAY_SECRET == ""


def test_gateway_forwarding_requires_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENABLE_AI_AGENT_FORWARDING=True,
            GATEWAY_TO_AGENT_SECRET="",
            AGENT_TO_GATEWAY_SECRET="",
        )
    assert "GATEWAY_TO_AGENT_SECRET" in str(exc_info.value) or "AGENT_TO_GATEWAY_SECRET" in str(
        exc_info.value
    )


def test_gateway_forwarding_requires_min_32_chars():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENABLE_AI_AGENT_FORWARDING=True,
            GATEWAY_TO_AGENT_SECRET="short_secret_under_32_chars",
            AGENT_TO_GATEWAY_SECRET="another_short_secret_under_32",
        )
    assert "32" in str(exc_info.value)


def test_gateway_forwarding_requires_different_secrets():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENABLE_AI_AGENT_FORWARDING=True,
            GATEWAY_TO_AGENT_SECRET="this_is_a_very_long_secret_that_has_at_least_32_characters",
            AGENT_TO_GATEWAY_SECRET="this_is_a_very_long_secret_that_has_at_least_32_characters",
        )
    assert (
        "different" in str(exc_info.value).lower()
        or "distinct" in str(exc_info.value).lower()
        or "khác nhau" in str(exc_info.value).lower()
    )
