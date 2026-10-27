import ssl
import xmlrpc.client
from typing import Any
from urllib.parse import urlparse


class OdooError(Exception):
    """Base exception for Odoo errors."""

    pass


class OdooAuthError(OdooError):
    """Raised when authentication fails."""

    pass


class OdooAccessError(OdooError):
    """Raised when access is denied / permission error."""

    pass


class OdooNetworkError(OdooError):
    """Raised on connection timeout or network failure."""

    pass


class OdooAccountInactiveError(OdooAccessError):
    """Raised when Odoo account is inactive (active=False)."""

    pass


class OdooExternalUserError(OdooAccessError):
    """Raised when Odoo account is an external/portal user (share=True)."""

    pass


class TimeoutTransport(xmlrpc.client.SafeTransport):
    """XML-RPC transport with configurable timeout and TLS context."""

    def __init__(self, timeout: int = 20, context: ssl.SSLContext | None = None):
        super().__init__(context=context)
        self.timeout = timeout

    def make_connection(self, host: Any):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


class OdooClient:
    def __init__(self, url: str, db: str, timeout: int = 20):
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            self.url = f"{parsed.scheme}://{parsed.netloc}"
        else:
            self.url = url.rstrip("/")
        self.db = db
        self.timeout = timeout

    def _get_proxy(self, service: str) -> xmlrpc.client.ServerProxy:
        endpoint = f"{self.url}/xmlrpc/2/{service}"
        if endpoint.startswith("https://"):
            context = ssl.create_default_context()
            transport = TimeoutTransport(timeout=self.timeout, context=context)
            return xmlrpc.client.ServerProxy(endpoint, transport=transport, allow_none=True)
        else:
            transport = xmlrpc.client.Transport()
            # Set default socket timeout for http
            return xmlrpc.client.ServerProxy(endpoint, transport=transport, allow_none=True)

    def authenticate(self, login: str, secret: str) -> int:
        try:
            common = self._get_proxy("common")
            uid = common.authenticate(self.db, login, secret, {})
            if not uid or not isinstance(uid, int):
                raise OdooAuthError("Invalid Odoo credentials or user not found.")
            return uid
        except (TimeoutError, ConnectionError, OSError) as e:
            raise OdooNetworkError(f"Failed to connect to Odoo server: {e}") from e
        except xmlrpc.client.Fault as e:
            if "AccessDenied" in e.faultString:
                raise OdooAuthError(f"Access Denied: {e.faultString}") from e
            raise OdooError(f"Odoo RPC Fault: {e.faultString}") from e

    def execute(
        self,
        uid: int,
        secret: str,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        kwargs = kwargs or {}
        try:
            models = self._get_proxy("object")
            return models.execute_kw(self.db, uid, secret, model, method, args, kwargs)
        except (TimeoutError, ConnectionError, OSError) as e:
            raise OdooNetworkError(f"Network error during Odoo call: {e}") from e
        except xmlrpc.client.Fault as e:
            fault_str = f"{getattr(e, 'faultCode', '')} {getattr(e, 'faultString', '')}"
            if "AccessError" in fault_str or "AccessDenied" in fault_str:
                raise OdooAccessError(f"Odoo Access Denied: {fault_str}") from e
            raise OdooError(f"Odoo RPC Fault: {fault_str}") from e
