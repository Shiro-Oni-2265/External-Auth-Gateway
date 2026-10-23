from unittest.mock import MagicMock, patch

import pytest

from app.odoo.client import (
    OdooAccessError,
    OdooAuthError,
    OdooClient,
    OdooNetworkError,
)


def test_odoo_client_authenticate_success():
    client = OdooClient(url="https://test.odoo.com", db="test_db", timeout=20)
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 42
        mock_proxy.return_value = mock_common

        uid = client.authenticate(login="user@example.com", secret="valid_key")
        assert uid == 42
        mock_common.authenticate.assert_called_once_with(
            "test_db", "user@example.com", "valid_key", {}
        )


def test_odoo_client_authenticate_failure():
    client = OdooClient(url="https://test.odoo.com", db="test_db")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_common = MagicMock()
        mock_common.authenticate.return_value = False
        mock_proxy.return_value = mock_common

        with pytest.raises(OdooAuthError):
            client.authenticate(login="user@example.com", secret="wrong_key")


def test_odoo_client_execute_success():
    client = OdooClient(url="https://test.odoo.com", db="test_db")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_models = MagicMock()
        mock_models.execute_kw.return_value = [{"id": 1, "name": "Lead 1"}]
        mock_proxy.return_value = mock_models

        result = client.execute(
            uid=42,
            secret="valid_key",
            model="crm.lead",
            method="search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id", "name"], "limit": 5},
        )
        assert result == [{"id": 1, "name": "Lead 1"}]
        mock_models.execute_kw.assert_called_once_with(
            "test_db",
            42,
            "valid_key",
            "crm.lead",
            "search_read",
            [[("active", "=", True)]],
            {"fields": ["id", "name"], "limit": 5},
        )


def test_odoo_client_access_error():
    client = OdooClient(url="https://test.odoo.com", db="test_db")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_models = MagicMock()
        import xmlrpc.client

        mock_models.execute_kw.side_effect = xmlrpc.client.Fault(
            "odoo.exceptions.AccessError", "Access Denied"
        )
        mock_proxy.return_value = mock_models

        with pytest.raises(OdooAccessError):
            client.execute(
                uid=42,
                secret="valid_key",
                model="crm.lead",
                method="search_read",
                args=[],
            )


def test_odoo_client_network_error():
    client = OdooClient(url="https://test.odoo.com", db="test_db")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_common = MagicMock()
        mock_common.authenticate.side_effect = TimeoutError("timed out")
        mock_proxy.return_value = mock_common

        with pytest.raises(OdooNetworkError):
            client.authenticate(login="user@example.com", secret="any_key")
