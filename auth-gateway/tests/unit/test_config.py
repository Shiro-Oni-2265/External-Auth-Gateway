import pytest
from pydantic import ValidationError

from app.config import Settings


def test_config_dev_defaults():
    settings = Settings(
        APP_ENV="development",
        PUBLIC_BASE_URL="http://localhost:8080",
        ODOO_URL="https://mycompany.odoo.com",
        ODOO_DB="test_db",
        ENCRYPTION_KEY="dev_dummy_key_not_fernet_in_dev",
    )
    assert settings.APP_ENV == "development"
    assert settings.SESSION_LIFETIME_HOURS == 8
    assert settings.LINK_TOKEN_LIFETIME_MINUTES == 10


def test_config_production_requires_fernet_key():
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            PUBLIC_BASE_URL="https://hub.example.com",
            ODOO_URL="https://company.odoo.com",
            ODOO_DB="production_db",
            ENCRYPTION_KEY="not_a_valid_fernet_key",
        )


def test_config_production_requires_https_urls(fernet_key):
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            PUBLIC_BASE_URL="http://insecure.example.com",
            ODOO_URL="https://company.odoo.com",
            ODOO_DB="production_db",
            ENCRYPTION_KEY=fernet_key,
        )

    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            PUBLIC_BASE_URL="https://secure.example.com",
            ODOO_URL="http://insecure-odoo.com",
            ODOO_DB="production_db",
            ENCRYPTION_KEY=fernet_key,
        )
