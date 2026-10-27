import pytest
from unittest.mock import MagicMock
from app.odoo.service import OdooService


def test_calculate_user_permissions_with_crm_and_google():
    mock_client = MagicMock()
    # Mock check_access_rights for crm.lead: read and create pass, write fails
    def mock_execute(uid, secret, model, method, args, kwargs=None):
        if model == "crm.lead" and method == "check_access_rights":
            operation = args[0]
            if operation in ("read", "create"):
                return True
            return False
        return True
    mock_client.execute.side_effect = mock_execute

    service = OdooService(mock_client)
    perms = service.compute_permissions(
        uid=101,
        secret="key",
        is_internal_user=True,
        google_status="connected",
        google_scopes=[
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    )
    assert "tasks.read" in perms
    assert "tasks.write" in perms
    assert "crm.read" in perms
    assert "crm.create" in perms
    assert "crm.write" not in perms
    assert "calendar.read" in perms
    assert "calendar.write" in perms
    assert "gmail.read" in perms
    assert "gmail.send" not in perms


def test_calculate_user_permissions_revoked_google():
    mock_client = MagicMock()
    mock_client.execute.return_value = True
    service = OdooService(mock_client)

    perms = service.compute_permissions(
        uid=101,
        secret="key",
        is_internal_user=True,
        google_status="reconnect_required",
        google_scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    assert "tasks.read" in perms
    assert "tasks.write" in perms
    assert "calendar.read" not in perms
    assert "gmail.read" not in perms


def test_calculate_user_permissions_inactive_user():
    mock_client = MagicMock()
    service = OdooService(mock_client)
    perms = service.compute_permissions(
        uid=101,
        secret="key",
        is_internal_user=False,
        google_status="disconnected",
        google_scopes=[],
    )
    assert "tasks.read" not in perms
    assert "tasks.write" not in perms
