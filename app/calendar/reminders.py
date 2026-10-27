import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker

from app.models import CalendarPair, OdooCredential, Outbox, TelegramConnection, User
from app.odoo.client import OdooClient
from app.security import decrypt_secret, ensure_utc
from app.telegram.client import BotBlockedError, RateLimitError, TelegramClient

_logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(
        self,
        session_factory: sessionmaker,
        telegram_client: TelegramClient,
        encryption_key: str,
        odoo_client: OdooClient | None = None,
        odoo_base_url: str = "",
    ):
        self.session_factory = session_factory
        self.telegram_client = telegram_client
        self.encryption_key = encryption_key
        self.odoo_client = odoo_client
        self.odoo_base_url = odoo_base_url.rstrip("/")

    def enqueue_due(
        self,
        now_utc: datetime,
        mock_odoo_events: dict[int, dict[str, Any]] | None = None,
    ) -> int:
        now_utc = ensure_utc(now_utc)
        fifteen_mins_ahead = now_utc + timedelta(minutes=15)
        enqueued_count = 0

        with self.session_factory() as db:
            active_pairs = db.query(CalendarPair).filter(CalendarPair.status == "synced").all()

            for pair in active_pairs:
                user = db.query(User).filter_by(id=pair.user_id, active=True).first()
                if not user:
                    continue

                # Get event data
                event_data = None
                if mock_odoo_events and pair.odoo_event_id in mock_odoo_events:
                    event_data = mock_odoo_events[pair.odoo_event_id]
                elif self.odoo_client:
                    cred = db.query(OdooCredential).filter_by(user_id=user.id).first()
                    if cred and cred.encrypted_secret:
                        secret = decrypt_secret(cred.encrypted_secret, self.encryption_key)
                        try:
                            recs = self.odoo_client.execute(
                                user.odoo_uid,
                                secret,
                                "calendar.event",
                                "read",
                                [[pair.odoo_event_id]],
                                {"fields": ["id", "name", "start", "stop", "allday", "location"]},
                            )
                            if recs:
                                event_data = recs[0]
                        except Exception as e:
                            _logger.warning(f"Failed to read Odoo event {pair.odoo_event_id}: {e}")

                if not event_data:
                    continue

                # Skip all-day events
                if event_data.get("allday", False):
                    continue

                start_str = event_data.get("start", "")
                if not start_str:
                    continue

                try:
                    start_naive = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                    start_dt_utc = start_naive.replace(tzinfo=UTC)
                except ValueError:
                    continue

                # Condition: now_utc <= start_dt_utc < fifteen_mins_ahead
                if now_utc <= start_dt_utc <= fifteen_mins_ahead:
                    dedup_key = (
                        f"reminder_{user.id}_{pair.id}_{start_dt_utc.strftime('%Y%m%d%H%M')}"
                    )
                    existing_out = db.query(Outbox).filter_by(dedup_key=dedup_key).first()
                    if existing_out:
                        continue

                    # Format message using user's timezone
                    tz_name = user.tz or "Asia/Bangkok"
                    try:
                        user_tz = ZoneInfo(tz_name)
                    except Exception:
                        user_tz = ZoneInfo("Asia/Bangkok")

                    local_start = start_dt_utc.astimezone(user_tz)
                    meeting_name = html.escape(event_data.get("name") or "Cuộc họp")
                    location = html.escape(event_data.get("location") or "Trực tuyến / Chưa ghi")
                    odoo_link = f"{self.odoo_base_url}/web#id={pair.odoo_event_id}&model=calendar.event&view_type=form"

                    msg_text = (
                        f"⏰ <b>NHẮC HỌP (còn 15 phút):</b>\n\n"
                        f"📌 <b>{meeting_name}</b>\n"
                        f"🕒 Giờ: <b>{local_start.strftime('%H:%M, ngày %d/%m/%Y')}</b> ({tz_name})\n"
                        f"📍 Địa điểm: <b>{location}</b>\n"
                        f"🔗 <a href=\"{odoo_link}\">Xem chi tiết trên Odoo</a>"
                    )

                    outbox_item = Outbox(
                        user_id=user.id,
                        dedup_key=dedup_key,
                        message_type="reminder",
                        payload=msg_text,
                        status="pending",
                        next_attempt_at=now_utc,
                    )
                    db.add(outbox_item)
                    enqueued_count += 1

            if enqueued_count > 0:
                db.commit()

        return enqueued_count

    def deliver_due(self, now_utc: datetime) -> int:
        now_utc = ensure_utc(now_utc)
        delivered_count = 0

        with self.session_factory() as db:
            pending_items = (
                db.query(Outbox)
                .filter(
                    Outbox.status.in_(["pending", "sending"]),
                    Outbox.next_attempt_at <= now_utc,
                )
                .limit(50)
                .all()
            )

            for item in pending_items:
                conn = (
                    db.query(TelegramConnection)
                    .filter_by(user_id=item.user_id, status="connected")
                    .first()
                )
                if not conn or not conn.private_chat_id:
                    item.status = "failed"
                    continue

                item.status = "sending"
                db.commit()

                try:
                    self.telegram_client.send_message(
                        chat_id=conn.private_chat_id,
                        text=item.payload,
                    )
                    item.status = "sent"
                    delivered_count += 1
                except BotBlockedError:
                    _logger.warning(
                        f"Bot blocked by user {item.user_id}. Marking connection blocked."
                    )
                    conn.status = "blocked"
                    item.status = "failed"
                except RateLimitError as e:
                    item.status = "pending"
                    item.attempts += 1
                    item.next_attempt_at = now_utc + timedelta(seconds=e.retry_after)
                except Exception as e:
                    _logger.error(f"Error sending outbox item {item.id}: {e}")
                    item.attempts += 1
                    item.status = "delivery_unknown"
                    # Exponential backoff with ceiling
                    backoff = min(30 * (2 ** min(item.attempts, 5)), 900)
                    item.next_attempt_at = now_utc + timedelta(seconds=backoff)

                db.commit()

        return delivered_count
