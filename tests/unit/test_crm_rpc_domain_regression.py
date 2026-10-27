import xmlrpc.client
from unittest.mock import patch

import pytest

from app.odoo.client import OdooAccessError, OdooClient
from app.odoo.service import OdooService


class StrictOdooProxyMock:
    """Mock XML-RPC proxy that strictly validates domain parameter structure.
    Rejects malformed/nested domains [[[('id', '=', ...)]]] as real Odoo does.
    """

    def __init__(self, db: str = "test_db"):
        self.db = db
        self.calls = []
        self.stages_db = {
            1: {"id": 1, "name": "Mới"},
            2: {"id": 2, "name": "Đang xử lý"},
        }
        self.leads_db = {
            101: {
                "id": 101,
                "name": "Cơ hội khách hàng VIP",
                "contact_name": "Nguyễn Văn A",
                "email_from": "a@example.com",
                "phone": "0912345678",
                "expected_revenue": 50000000.0,
                "stage_id": [1, "Mới"],
                "write_date": "2026-10-27 08:00:00",
                "user_id": [10, "Nhân viên kinh doanh"],
            }
        }
        self.created_leads = []
        self.written_leads = []
        self.raise_on_model_method = {}

    def execute_kw(self, db, uid, secret, model, method, args, kwargs=None):
        kwargs = kwargs or {}
        self.calls.append({
            "db": db,
            "uid": uid,
            "secret": secret,
            "model": model,
            "method": method,
            "args": args,
            "kwargs": kwargs,
        })

        if (model, method) in self.raise_on_model_method:
            raise self.raise_on_model_method[(model, method)]

        if method == "search_read":
            # Odoo search_read takes [domain] as positional argument
            assert isinstance(args, list) and len(args) >= 1, "search_read requires at least [domain] in args"
            domain = args[0]
            if not isinstance(domain, list):
                raise xmlrpc.client.Fault(1, "Domain must be a list of criterion tuples")

            # Validate each criterion in domain: MUST BE a 3-tuple (field, operator, value)
            for item in domain:
                if isinstance(item, list | set):
                    raise xmlrpc.client.Fault(
                        1,
                        f"Invalid domain element {item!r}: criterion cannot be a nested list/set! "
                        f"Expected (field, op, val) tuple, got {type(item).__name__}."
                    )
                if not (isinstance(item, tuple) and len(item) == 3):
                    raise xmlrpc.client.Fault(
                        1,
                        f"Invalid domain criterion {item!r}: must be a 3-element tuple (field, op, val)."
                    )

            # Evaluate lookup by ID
            target_id = None
            for field, op, val in domain:
                if field == "id" and op == "=":
                    target_id = val

            if model == "crm.stage":
                if target_id in self.stages_db:
                    return [self.stages_db[target_id]]
                return []

            if model == "crm.lead":
                if target_id in self.leads_db:
                    rec = self.leads_db[target_id].copy()
                    fields = kwargs.get("fields")
                    if fields:
                        return [{k: rec.get(k) for k in fields}]
                    return [rec]
                return []

            return []

        elif method == "create":
            assert len(args) == 1 and isinstance(args[0], dict), "create requires [vals]"
            vals = args[0]
            new_id = 999
            self.created_leads.append(vals)
            return new_id

        elif method == "write":
            assert len(args) == 2, "write requires [[ids], vals]"
            ids, vals = args
            assert isinstance(ids, list) and len(ids) == 1, "write ids must be a list of record ids"
            assert isinstance(vals, dict), "write vals must be a dictionary"
            self.written_leads.append((ids[0], vals))
            return True

        raise NotImplementedError(f"Mock does not implement {model}.{method}")


@pytest.fixture
def odoo_service_with_strict_mock():
    client = OdooClient(url="https://test.odoo.com", db="test_db", timeout=10)
    mock_proxy = StrictOdooProxyMock(db="test_db")

    with patch.object(client, "_get_proxy", return_value=mock_proxy):
        service = OdooService(client=client)
        yield service, mock_proxy


def test_create_lead_with_stage_rpc_domain(odoo_service_with_strict_mock):
    """Test create_lead checks stage with valid domain [('id', '=', stage_id)] and calls create."""
    service, mock_proxy = odoo_service_with_strict_mock

    res = service.create_lead(
        uid=10,
        secret="valid_secret",
        name="Cơ hội phần mềm kế toán",
        contact_name="Trần B",
        email_from="tranb@example.com",
        phone="0987654321",
        description="Quan tâm gói Enterprise",
        expected_revenue=25000000.0,
        stage_id=1,
    )

    assert res["id"] == 999
    assert res["name"] == "Cơ hội phần mềm kế toán"

    # Verify search_read call for crm.stage
    stage_call = next(c for c in mock_proxy.calls if c["model"] == "crm.stage" and c["method"] == "search_read")
    assert stage_call["db"] == "test_db"
    assert stage_call["uid"] == 10
    assert stage_call["secret"] == "valid_secret"
    # Strict check: args must be [[('id', '=', 1)]], NOT [[([('id', '=', 1)])]]
    assert stage_call["args"] == [[("id", "=", 1)]]
    assert stage_call["kwargs"] == {"fields": ["id", "name"], "limit": 1}

    # Verify create call for crm.lead
    create_call = next(c for c in mock_proxy.calls if c["model"] == "crm.lead" and c["method"] == "create")
    assert create_call["args"][0]["user_id"] == 10
    assert create_call["args"][0]["stage_id"] == 1
    assert len(mock_proxy.created_leads) == 1


def test_get_lead_detail_rpc_domain(odoo_service_with_strict_mock):
    """Test get_lead_detail reads lead with valid domain [('id', '=', lead_id)]."""
    service, mock_proxy = odoo_service_with_strict_mock

    lead = service.get_lead_detail(uid=10, secret="valid_secret", lead_id=101)
    assert lead is not None
    assert lead["id"] == 101
    assert lead["name"] == "Cơ hội khách hàng VIP"

    call = next(c for c in mock_proxy.calls if c["model"] == "crm.lead" and c["method"] == "search_read")
    assert call["db"] == "test_db"
    assert call["uid"] == 10
    # Strict check: args must be [[('id', '=', 101)]]
    assert call["args"] == [[("id", "=", 101)]]
    assert "write_date" in call["kwargs"]["fields"]


def test_update_lead_with_stage_change_rpc_domain(odoo_service_with_strict_mock):
    """Test update_lead reads current lead, verifies new stage, and writes changes."""
    service, mock_proxy = odoo_service_with_strict_mock

    res = service.update_lead(
        uid=10,
        secret="valid_secret",
        lead_id=101,
        name="Cơ hội khách hàng VIP - Cập nhật",
        expected_revenue=60000000.0,
        stage_id=2,
        expected_write_date="2026-10-27 08:00:00",
    )

    assert res["id"] == 101

    # Verify existing lead lookup
    lead_lookup = [c for c in mock_proxy.calls if c["model"] == "crm.lead" and c["method"] == "search_read"][0]
    assert lead_lookup["args"] == [[("id", "=", 101)]]

    # Verify stage lookup
    stage_lookup = [c for c in mock_proxy.calls if c["model"] == "crm.stage" and c["method"] == "search_read"][0]
    assert stage_lookup["args"] == [[("id", "=", 2)]]

    # Verify write call
    write_call = next(c for c in mock_proxy.calls if c["model"] == "crm.lead" and c["method"] == "write")
    assert write_call["args"][0] == [101]
    assert write_call["args"][1]["name"] == "Cơ hội khách hàng VIP - Cập nhật"
    assert write_call["args"][1]["stage_id"] == 2
    assert write_call["args"][1]["expected_revenue"] == 60000000.0
    assert len(mock_proxy.written_leads) == 1


def test_create_lead_invalid_stage_rejects_and_does_not_create(odoo_service_with_strict_mock):
    """When stage does not exist, create_lead raises ValueError and never calls create."""
    service, mock_proxy = odoo_service_with_strict_mock

    with pytest.raises(ValueError, match=r"(?i)giai đoạn CRM.*không tồn tại"):
        service.create_lead(
            uid=10,
            secret="valid_secret",
            name="Test Lead",
            stage_id=9999,  # Non-existent
        )

    assert len(mock_proxy.created_leads) == 0
    assert not any(c["method"] == "create" for c in mock_proxy.calls)


def test_update_lead_not_found_rejects_and_does_not_write(odoo_service_with_strict_mock):
    """When lead does not exist, update_lead raises ValueError and never calls write."""
    service, mock_proxy = odoo_service_with_strict_mock

    with pytest.raises(ValueError, match=r"(?i)bản ghi CRM.*không tồn tại"):
        service.update_lead(
            uid=10,
            secret="valid_secret",
            lead_id=8888,  # Non-existent
            name="New name",
        )

    assert len(mock_proxy.written_leads) == 0
    assert not any(c["method"] == "write" for c in mock_proxy.calls)


def test_update_lead_write_date_conflict_rejects_and_does_not_write(odoo_service_with_strict_mock):
    """When expected_write_date mismatches, update_lead raises ValueError and does not write."""
    service, mock_proxy = odoo_service_with_strict_mock

    with pytest.raises(ValueError, match="đã bị thay đổi trên hệ thống sau khi tạo bản xem trước"):
        service.update_lead(
            uid=10,
            secret="valid_secret",
            lead_id=101,
            name="Conflicted edit",
            expected_write_date="2026-10-27 07:00:00",  # Mismatch with 08:00:00
        )

    assert len(mock_proxy.written_leads) == 0
    assert not any(c["method"] == "write" for c in mock_proxy.calls)


def test_update_lead_invalid_stage_rejects_and_does_not_write(odoo_service_with_strict_mock):
    """When target stage does not exist, update_lead raises ValueError and does not write."""
    service, mock_proxy = odoo_service_with_strict_mock

    with pytest.raises(ValueError, match=r"(?i)giai đoạn CRM.*không tồn tại"):
        service.update_lead(
            uid=10,
            secret="valid_secret",
            lead_id=101,
            stage_id=9999,  # Non-existent stage
            expected_write_date="2026-10-27 08:00:00",
        )

    assert len(mock_proxy.written_leads) == 0
    assert not any(c["method"] == "write" for c in mock_proxy.calls)


def test_permission_error_on_search_read_propagates_and_does_not_write(odoo_service_with_strict_mock):
    """When user lacks permission on crm.lead, OdooAccessError is raised and write is not called."""
    service, mock_proxy = odoo_service_with_strict_mock
    mock_proxy.raise_on_model_method[("crm.lead", "search_read")] = xmlrpc.client.Fault(
        1, "AccessError: You are not allowed to read crm.lead records."
    )

    with pytest.raises(OdooAccessError, match="Access Denied"):
        service.update_lead(
            uid=10,
            secret="valid_secret",
            lead_id=101,
            name="Unpermitted edit",
        )

    assert len(mock_proxy.written_leads) == 0
    assert not any(c["method"] == "write" for c in mock_proxy.calls)
