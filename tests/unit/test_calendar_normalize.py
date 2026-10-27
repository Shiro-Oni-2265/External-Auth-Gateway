from datetime import UTC, date, datetime

from app.calendar.normalize import (
    NormalizedEvent,
    calculate_event_fingerprint,
    google_to_normalized,
    is_google_event_eligible,
    is_odoo_event_eligible,
    normalized_to_google,
    normalized_to_odoo,
)


def test_google_timed_event_normalization():
    google_event = {
        "id": "g_timed_1",
        "summary": "Họp Kế Hoạch Tuần",
        "description": "Thảo luận mục tiêu tháng mới.",
        "location": "Phòng họp số 3",
        "start": {"dateTime": "2026-10-21T09:00:00+07:00"},
        "end": {"dateTime": "2026-10-21T10:30:00+07:00"},
        "visibility": "private",
        "eventType": "default",
        "organizer": {"self": True},
    }

    assert is_google_event_eligible(google_event) is True

    norm = google_to_normalized(google_event, user_tz="Asia/Bangkok")
    assert norm.name == "Họp Kế Hoạch Tuần"
    assert norm.location == "Phòng họp số 3"
    assert norm.allday is False
    assert norm.privacy == "private"
    assert norm.start_dt.astimezone(UTC) == datetime(2026, 10, 21, 2, 0, tzinfo=UTC)
    assert norm.stop_dt.astimezone(UTC) == datetime(2026, 10, 21, 3, 30, tzinfo=UTC)

    # Convert to Odoo dict
    odoo_vals = normalized_to_odoo(norm, odoo_uid=10)
    assert odoo_vals["name"] == "Họp Kế Hoạch Tuần"
    assert odoo_vals["allday"] is False
    assert odoo_vals["start"] == "2026-10-21 02:00:00"
    assert odoo_vals["stop"] == "2026-10-21 03:30:00"
    assert odoo_vals["privacy"] == "private"
    assert odoo_vals["user_id"] == 10


def test_all_day_event_inclusive_exclusive_conversion():
    # Google all-day 1-day event: start 2026-10-25, end 2026-10-26 (exclusive)
    google_event = {
        "id": "g_allday_1",
        "summary": "Team Building 1 Ngày",
        "start": {"date": "2026-10-25"},
        "end": {"date": "2026-10-26"},
        "eventType": "default",
        "organizer": {"self": True},
    }

    norm = google_to_normalized(google_event, user_tz="Asia/Bangkok")
    assert norm.allday is True
    assert norm.start_date == date(2026, 10, 25)
    # Odoo uses inclusive stop_date, so 1-day event has stop_date == 2026-10-25!
    assert norm.stop_date == date(2026, 10, 25)

    odoo_vals = normalized_to_odoo(norm, odoo_uid=5)
    assert odoo_vals["allday"] is True
    assert odoo_vals["start_date"] == "2026-10-25"
    assert odoo_vals["stop_date"] == "2026-10-25"

    # Convert back from Odoo to Google
    g_vals = normalized_to_google(norm)
    assert g_vals["start"] == {"date": "2026-10-25"}
    assert g_vals["end"] == {"date": "2026-10-26"}  # Exclusive date restored!


def test_event_eligibility_checks():
    # Google recurrence event -> not eligible
    g_recurrence = {
        "id": "g_rec",
        "summary": "Họp định kỳ",
        "recurrence": ["RRULE:FREQ=WEEKLY"],
        "organizer": {"self": True},
    }
    assert is_google_event_eligible(g_recurrence) is False

    # Google invited event (not organizer, no attendee self) -> not eligible
    g_invited = {
        "id": "g_inv",
        "summary": "Họp khách mời",
        "organizer": {"self": False},
    }
    assert is_google_event_eligible(g_invited) is False

    # Google invited meeting where user is attendee and accepted -> eligible
    g_invited_accepted = {
        "id": "g_inv_acc",
        "summary": "Cuộc họp đối tác",
        "organizer": {"email": "boss@client.com", "self": False},
        "attendees": [{"email": "me@company.com", "self": True, "responseStatus": "accepted"}],
        "start": {"dateTime": "2026-10-25T10:00:00+07:00"},
        "end": {"dateTime": "2026-10-25T11:00:00+07:00"},
    }
    assert is_google_event_eligible(g_invited_accepted) is True

    # Google recurring expanded instance -> eligible
    g_instance = {
        "id": "g_inst_1",
        "recurringEventId": "parent_series_123",
        "summary": "Họp giao ban tuần",
        "organizer": {"self": True},
        "start": {"dateTime": "2026-10-26T09:00:00+07:00"},
        "end": {"dateTime": "2026-10-26T10:00:00+07:00"},
    }
    assert is_google_event_eligible(g_instance) is True

    # Odoo event recurrent -> not eligible
    odoo_rec = {
        "id": 1,
        "name": "Lịch lặp Odoo",
        "recurrency": True,
        "user_id": 10,
        "active": True,
    }
    assert is_odoo_event_eligible(odoo_rec, odoo_uid=10) is False

    # Odoo event of another user without user as partner -> not eligible
    odoo_other = {
        "id": 2,
        "name": "Lịch người khác",
        "recurrency": False,
        "user_id": 99,
        "active": True,
        "partner_ids": [101, 102],
    }
    assert is_odoo_event_eligible(odoo_other, odoo_uid=10, partner_id=50) is False

    # Odoo meeting where user is partner -> eligible
    odoo_partner_meeting = {
        "id": 3,
        "name": "Cuộc họp phòng ban",
        "recurrency": False,
        "user_id": 99,
        "active": True,
        "partner_ids": [101, 50],
    }
    assert is_odoo_event_eligible(odoo_partner_meeting, odoo_uid=10, partner_id=50) is True


def test_fingerprint_generation():
    norm1 = NormalizedEvent(
        name="Meeting A",
        description="Desc",
        location="Room 1",
        allday=False,
        start_dt=datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
        stop_dt=datetime(2026, 10, 21, 11, 0, tzinfo=UTC),
        start_date=None,
        stop_date=None,
        privacy="public",
    )
    norm2 = NormalizedEvent(
        name="Meeting A",
        description="Desc",
        location="Room 1",
        allday=False,
        start_dt=datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
        stop_dt=datetime(2026, 10, 21, 11, 0, tzinfo=UTC),
        start_date=None,
        stop_date=None,
        privacy="public",
    )
    fp1 = calculate_event_fingerprint(norm1)
    fp2 = calculate_event_fingerprint(norm2)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Change title -> fingerprint must change
    norm2.name = "Meeting B"
    assert calculate_event_fingerprint(norm2) != fp1
