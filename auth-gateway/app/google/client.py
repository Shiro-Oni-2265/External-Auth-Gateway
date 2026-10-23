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
        resp = self._request(user_id, "PUT", url, json_data=event_data, headers=headers)
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
