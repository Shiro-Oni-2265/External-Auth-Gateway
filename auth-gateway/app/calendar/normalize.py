import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any


@dataclass
class NormalizedEvent:
    name: str
    description: str
    location: str
    allday: bool
    start_dt: datetime | None  # UTC aware
    stop_dt: datetime | None  # UTC aware
    start_date: date | None  # inclusive
    stop_date: date | None  # inclusive
    privacy: str  # 'public', 'private', 'confidential'


def is_google_event_eligible(event: dict[str, Any]) -> bool:
    # eventType must be default
    if event.get("eventType", "default") != "default":
        return False
    # Must not be canceled status
    if event.get("status") == "cancelled":
        return False
    # Raw RRULE template without expansion is skipped (singleEvents=true expands instances)
    if event.get("recurrence"):
        return False

    # Must have start time or date
    start_obj = event.get("start", {})
    if not start_obj or (not start_obj.get("dateTime") and not start_obj.get("date")):
        return False

    # User is organizer
    organizer = event.get("organizer", {})
    if organizer.get("self", False):
        return True

    # User is attendee who has not declined
    attendees = event.get("attendees", [])
    if attendees:
        for att in attendees:
            if att.get("self", False):
                return att.get("responseStatus") != "declined"
        return False

    # If organizer is explicitly another person and no attendees list contains self -> not eligible
    if organizer and not organizer.get("self", False):
        return False

    return True


def is_odoo_event_eligible(
    event: dict[str, Any], odoo_uid: int, partner_id: int | None = None
) -> bool:
    if not event.get("active", True):
        return False
    # Must belong to user or user is attendee
    user_val = event.get("user_id")
    event_uid = user_val[0] if isinstance(user_val, list | tuple) else user_val
    is_owner = event_uid == odoo_uid

    partners = event.get("partner_ids", [])
    is_partner = bool(partner_id and partner_id in partners)

    if not is_owner and not is_partner:
        return False
    # No recurrency
    if event.get("recurrency", False) or event.get("recurrent", False):
        return False
    return True


def google_to_normalized(event: dict[str, Any], user_tz: str = "Asia/Bangkok") -> NormalizedEvent:
    name = (event.get("summary") or "Chưa đặt tên").strip()
    description = (event.get("description") or "").strip()
    location = (event.get("location") or "").strip()

    # Privacy / Visibility
    visibility = event.get("visibility", "default")
    if visibility in ("private", "confidential"):
        privacy = "private"
    else:
        privacy = "public"

    start_obj = event.get("start", {})
    end_obj = event.get("end", {})

    if "date" in start_obj:
        s_date = date.fromisoformat(start_obj["date"])
        # Google end.date is exclusive. For 1-day event: start=2026-10-21, end=2026-10-22
        # Odoo uses inclusive stop_date, so subtract 1 day from Google end date
        e_date_raw = date.fromisoformat(end_obj.get("date", start_obj["date"]))
        stop_date = e_date_raw - timedelta(days=1)
        if stop_date < s_date:
            stop_date = s_date

        return NormalizedEvent(
            name=name,
            description=description,
            location=location,
            allday=True,
            start_dt=None,
            stop_dt=None,
            start_date=s_date,
            stop_date=stop_date,
            privacy=privacy,
        )
    else:
        s_raw = start_obj.get("dateTime") or datetime.now(UTC).isoformat()
        e_raw = end_obj.get("dateTime") or s_raw

        start_dt = datetime.fromisoformat(s_raw).astimezone(UTC)
        stop_dt = datetime.fromisoformat(e_raw).astimezone(UTC)

        return NormalizedEvent(
            name=name,
            description=description,
            location=location,
            allday=False,
            start_dt=start_dt,
            stop_dt=stop_dt,
            start_date=None,
            stop_date=None,
            privacy=privacy,
        )


def odoo_to_normalized(event: dict[str, Any], user_tz: str = "Asia/Bangkok") -> NormalizedEvent:
    name = (event.get("name") or "Chưa đặt tên").strip()
    description = (event.get("description") or "").strip()
    location = (event.get("location") or "").strip()
    privacy = event.get("privacy", "public")
    if privacy not in ("public", "private", "confidential"):
        privacy = "public"

    allday = bool(event.get("allday", False))

    if allday:
        s_str = event.get("start_date") or event.get("start", "")[:10]
        e_str = event.get("stop_date") or event.get("stop", "")[:10]
        s_date = date.fromisoformat(s_str)
        e_date = date.fromisoformat(e_str) if e_str else s_date

        return NormalizedEvent(
            name=name,
            description=description,
            location=location,
            allday=True,
            start_dt=None,
            stop_dt=None,
            start_date=s_date,
            stop_date=e_date,
            privacy=privacy,
        )
    else:
        s_str = event.get("start", "")
        e_str = event.get("stop", "")

        # Odoo timed datetime is stored in UTC as naive "YYYY-MM-DD HH:MM:SS"
        s_naive = datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
        e_naive = datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")

        start_dt = s_naive.replace(tzinfo=UTC)
        stop_dt = e_naive.replace(tzinfo=UTC)

        return NormalizedEvent(
            name=name,
            description=description,
            location=location,
            allday=False,
            start_dt=start_dt,
            stop_dt=stop_dt,
            start_date=None,
            stop_date=None,
            privacy=privacy,
        )


def normalized_to_google(norm: NormalizedEvent) -> dict[str, Any]:
    visibility = "private" if norm.privacy in ("private", "confidential") else "public"

    result: dict[str, Any] = {
        "summary": norm.name,
        "description": norm.description,
        "location": norm.location,
        "visibility": visibility,
    }

    if norm.allday:
        # Google end.date is exclusive, so add 1 day
        g_end_date = norm.stop_date + timedelta(days=1)
        result["start"] = {"date": norm.start_date.isoformat()}
        result["end"] = {"date": g_end_date.isoformat()}
    else:
        result["start"] = {"dateTime": norm.start_dt.isoformat()}
        result["end"] = {"dateTime": norm.stop_dt.isoformat()}

    return result


def normalized_to_odoo(norm: NormalizedEvent, odoo_uid: int) -> dict[str, Any]:
    privacy = "private" if norm.privacy in ("private", "confidential") else "public"

    result: dict[str, Any] = {
        "name": norm.name,
        "description": norm.description,
        "location": norm.location,
        "privacy": privacy,
        "user_id": odoo_uid,
        "allday": norm.allday,
    }

    if norm.allday:
        result["start_date"] = norm.start_date.isoformat()
        result["stop_date"] = norm.stop_date.isoformat()
    else:
        # Odoo timed datetime expects UTC string "YYYY-MM-DD HH:MM:SS"
        result["start"] = norm.start_dt.strftime("%Y-%m-%d %H:%M:%S")
        result["stop"] = norm.stop_dt.strftime("%Y-%m-%d %H:%M:%S")

    return result


def calculate_event_fingerprint(norm: NormalizedEvent) -> str:
    payload = {
        "name": norm.name,
        "description": norm.description,
        "location": norm.location,
        "allday": norm.allday,
        "privacy": norm.privacy,
        "start": norm.start_dt.isoformat()
        if norm.start_dt
        else (norm.start_date.isoformat() if norm.start_date else ""),
        "stop": norm.stop_dt.isoformat()
        if norm.stop_dt
        else (norm.stop_date.isoformat() if norm.stop_date else ""),
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
