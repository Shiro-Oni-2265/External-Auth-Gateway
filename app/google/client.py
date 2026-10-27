from typing import Any

import httpx

from app.google.oauth import GoogleOAuthService


class GoogleClient:
    CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
    SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, oauth_service: GoogleOAuthService):
        self.oauth_service = oauth_service

    def _request(
        self,
        user_id: int,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_on_401: bool = True,
    ) -> httpx.Response:
        token = self.oauth_service.get_valid_access_token(user_id)
        req_headers = headers.copy() if headers else {}
        req_headers["Authorization"] = f"Bearer {token}"

        resp = httpx.request(
            method, url, params=params, json=json_data, headers=req_headers, timeout=25.0
        )

        # Retry once on 401 if token expired unexpectedly
        if resp.status_code == 401 and retry_on_401:
            # Force refresh by expiring the token in memory/DB or calling get_valid_access_token
            # In our implementation, we can retry with a refreshed token
            try:
                # Discard current access token and re-fetch
                token = self.oauth_service.get_valid_access_token(user_id)
                req_headers["Authorization"] = f"Bearer {token}"
                resp = httpx.request(
                    method, url, params=params, json=json_data, headers=req_headers, timeout=25.0
                )
            except Exception:
                pass

        return resp

    # --- Calendar API ---
    def list_calendar_events(
        self,
        user_id: int,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        page_token: str | None = None,
        single_events: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "singleEvents": "true" if single_events else "false",
            "maxResults": 250,
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if page_token:
            params["pageToken"] = page_token

        url = f"{self.CALENDAR_API_BASE}/calendars/{calendar_id}/events"
        resp = self._request(user_id, "GET", url, params=params)
        resp.raise_for_status()
        return resp.json()

    def create_calendar_event(
        self,
        user_id: int,
        calendar_id: str = "primary",
        event_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.CALENDAR_API_BASE}/calendars/{calendar_id}/events"
        resp = self._request(user_id, "POST", url, json_data=event_data)
        resp.raise_for_status()
        return resp.json()

    def update_calendar_event(
        self,
        user_id: int,
        calendar_id: str,
        event_id: str,
        event_data: dict[str, Any],
        etag: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        headers = {}
        if etag:
            headers["If-Match"] = etag
        resp = self._request(user_id, "PATCH", url, json_data=event_data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_calendar_event(
        self,
        user_id: int,
        calendar_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        url = f"{self.CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        resp = self._request(user_id, "GET", url)
        resp.raise_for_status()
        return resp.json()

    def delete_calendar_event(
        self,
        user_id: int,
        calendar_id: str = "primary",
        event_id: str = "",
    ) -> bool:
        url = f"{self.CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}"
        resp = self._request(user_id, "DELETE", url)
        if resp.status_code in (200, 204, 404, 410):
            return True
        resp.raise_for_status()
        return True

    # --- Internal Drive & Sheets Helpers ---
    def upload_drive_file(
        self,
        user_id: int,
        name: str,
        content_bytes: bytes,
        mime_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        # Upload using Drive multipart or upload API
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=media"
        headers = {"Content-Type": mime_type}
        token = self.oauth_service.get_valid_access_token(user_id)
        headers["Authorization"] = f"Bearer {token}"
        resp = httpx.post(url, content=content_bytes, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def append_sheet_row(
        self,
        user_id: int,
        spreadsheet_id: str,
        range_name: str,
        values: list[Any],
    ) -> dict[str, Any]:
        url = f"{self.SHEETS_API_BASE}/{spreadsheet_id}/values/{range_name}:append?valueInputOption=USER_ENTERED"
        resp = self._request(user_id, "POST", url, json_data={"values": [values]})
        resp.raise_for_status()
        return resp.json()

    # --- Gmail API ---
    def get_user_google_connection(self, user_id: int):
        from app.models import GoogleConnection

        with self.oauth_service.session_factory() as db:
            return db.query(GoogleConnection).filter_by(user_id=user_id).first()

    def check_has_scope(self, user_id: int, scope_fragment: str) -> bool:
        conn = self.get_user_google_connection(user_id)
        if not conn or not conn.scopes:
            return False
        return any(scope_fragment in s for s in conn.scopes.split())

    def list_unread_emails(self, user_id: int, max_results: int = 5) -> dict[str, Any]:
        """Fetch unread emails for user. Checks scope, status, and handles errors."""
        conn = self.get_user_google_connection(user_id)
        if not conn or conn.status == "disconnected":
            return {"status": "NOT_CONNECTED", "total_unread": 0, "messages": []}
        if conn.status == "reconnect_required":
            return {"status": "RECONNECT_REQUIRED", "total_unread": 0, "messages": []}

        # Check actual granted scope
        if not self.check_has_scope(user_id, "gmail.readonly"):
            return {
                "status": "PERMISSION_DENIED",
                "total_unread": 0,
                "messages": [],
                "instruction": "Vui lòng cấp thêm quyền đọc Gmail qua Cổng Portal.",
            }

        gmail_base = "https://gmail.googleapis.com/gmail/v1/users/me"
        try:
            # 1. Get messages
            resp = self._request(
                user_id,
                "GET",
                f"{gmail_base}/messages",
                params={"q": "in:inbox is:unread", "maxResults": max_results},
            )
            if resp.status_code == 403:
                return {
                    "status": "PERMISSION_DENIED",
                    "total_unread": 0,
                    "messages": [],
                    "instruction": "Vui lòng cấp thêm quyền đọc Gmail qua Cổng Portal.",
                }
            resp.raise_for_status()
            data = resp.json()

            msg_items = data.get("messages", [])

            # Accurate unread count strictly from INBOX label
            accurate_total = None
            try:
                lbl_resp = self._request(user_id, "GET", f"{gmail_base}/labels/INBOX")
                if lbl_resp.status_code == 200:
                    lbl_data = lbl_resp.json()
                    accurate_total = lbl_data.get("messagesUnread")
            except Exception:
                pass

            messages = []
            for item in msg_items[:max_results]:
                msg_id = item["id"]
                try:
                    detail_resp = self._request(
                        user_id,
                        "GET",
                        f"{gmail_base}/messages/{msg_id}",
                        params={
                            "format": "metadata",
                            "metadataHeaders": ["From", "Subject", "Date"],
                        },
                    )
                    if detail_resp.status_code == 200:
                        m_data = detail_resp.json()
                        headers = {
                            h.get("name", "").lower(): h.get("value", "")
                            for h in m_data.get("payload", {}).get("headers", [])
                        }
                        messages.append(
                            {
                                "id": msg_id,
                                "from": headers.get("from", "Không rõ người gửi"),
                                "subject": headers.get("subject", "Không có tiêu đề"),
                                "date": headers.get("date", ""),
                                "link": f"https://mail.google.com/mail/u/0/#inbox/{msg_id}",
                            }
                        )
                except Exception:
                    continue

            return {
                "status": "OK",
                "total_unread": accurate_total,
                "messages": messages,
            }
        except Exception as e:
            from app.google.oauth import GoogleAuthRevokedError

            if isinstance(e, GoogleAuthRevokedError):
                return {"status": "RECONNECT_REQUIRED", "total_unread": 0, "messages": []}
            return {"status": "ERROR", "total_unread": 0, "messages": [], "message": str(e)}

    def send_email(
        self,
        user_id: int,
        to: list[str] | str,
        subject: str,
        body_text: str,
    ) -> dict[str, Any]:
        """Gửi email qua Gmail API dưới tài khoản Google đã liên kết của người dùng."""
        import base64
        from email.mime.text import MIMEText

        to_str = ", ".join(to) if isinstance(to, list) else to
        msg = MIMEText(body_text, _charset="utf-8")
        msg["To"] = to_str
        msg["Subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        resp = self._request(user_id, "POST", url, json_data={"raw": raw})
        if resp.status_code == 403:
            return {
                "status": "PERMISSION_DENIED",
                "message": "Chưa được cấp quyền gửi Gmail (cần scope gmail.send). Vui lòng mở Portal để cấp thêm quyền.",
            }
        resp.raise_for_status()
        data = resp.json()
        return {"status": "OK", "id": data.get("id"), "thread_id": data.get("threadId")}
