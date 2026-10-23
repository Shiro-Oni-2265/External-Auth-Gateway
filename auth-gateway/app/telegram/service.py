import base64
import html
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import qrcode
from sqlalchemy.orm import sessionmaker

from app.models import LinkToken, OdooCredential, TelegramConnection, User
from app.odoo.client import OdooAccessError, OdooClient, OdooError
from app.security import decrypt_secret, ensure_utc, generate_secure_token, hash_token
from app.telegram.client import TelegramClient

_logger = logging.getLogger(__name__)


@dataclass
class TelegramLink:
    url: str
    qr_code_data_uri: str
    expires_at: datetime


class TelegramService:
    def __init__(
        self,
        session_factory: sessionmaker,
        telegram_client: TelegramClient,
        bot_username: str,
        webhook_secret: str,
        odoo_client: OdooClient | None = None,
        encryption_key: str = "",
        odoo_base_url: str = "",
    ):
        self.session_factory = session_factory
        self.telegram_client = telegram_client
        self.bot_username = bot_username.lstrip("@").strip()
        self.webhook_secret = webhook_secret
        self.odoo_client = odoo_client
        self.encryption_key = encryption_key
        self.odoo_base_url = odoo_base_url.rstrip("/")

        # Rate limiter: user_id -> list of query timestamps (max 10 / min)
        self._crm_rate_limits: dict[int, list[datetime]] = {}

    def issue_link(self, user_id: int) -> TelegramLink:
        raw_token = generate_secure_token(24)
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=10)

        # Invalidate / delete old unused telegram_link tokens for this user
        with self.session_factory() as db:
            db.query(LinkToken).filter(
                LinkToken.user_id == user_id,
                LinkToken.purpose == "telegram_link",
                LinkToken.used_at.is_(None),
            ).delete()

            lt = LinkToken(
                token_hash=token_hash,
                purpose="telegram_link",
                user_id=user_id,
                expires_at=expires_at,
            )
            db.add(lt)
            db.commit()

        link_url = f"https://t.me/{self.bot_username}?start={raw_token}"

        # Generate QR code locally (strictly in-process, no external API)
        qr_img = qrcode.make(link_url)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        qr_data_uri = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

        return TelegramLink(
            url=link_url,
            qr_code_data_uri=qr_data_uri,
            expires_at=expires_at,
        )

    def process_update_dict(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        # We only accept commands from private chats with non-bots
        if chat.get("type") != "private" or from_user.get("is_bot", False):
            return

        telegram_user_id = from_user.get("id")
        username = from_user.get("username")

        # Normalize command (e.g. /crm@MyBot -> /crm)
        cmd_part = text.split()[0].lower()
        base_cmd = cmd_part.split("@")[0]

        if base_cmd == "/start":
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                token_arg = parts[1].strip()
                self._handle_pairing(chat_id, telegram_user_id, username, token_arg)
            else:
                self._handle_start_help(chat_id, telegram_user_id)
        elif base_cmd == "/help":
            self._handle_help(chat_id)
        elif base_cmd == "/status":
            self._handle_status(chat_id, telegram_user_id)
        elif base_cmd == "/crm":
            parts = text.split(maxsplit=1)
            search_query = parts[1].strip() if len(parts) > 1 else None
            self._handle_crm(chat_id, telegram_user_id, search_query)

    def _handle_pairing(
        self,
        chat_id: int,
        telegram_user_id: int,
        username: str | None,
        token_arg: str,
    ) -> None:
        token_hash = hash_token(token_arg)
        now = datetime.now(UTC)

        with self.session_factory() as db:
            # 1. Check if token is valid and unused
            lt = (
                db.query(LinkToken)
                .filter(
                    LinkToken.token_hash == token_hash,
                    LinkToken.purpose == "telegram_link",
                    LinkToken.used_at.is_(None),
                )
                .first()
            )

            if not lt or ensure_utc(lt.expires_at) <= now:
                self.telegram_client.send_message(
                    chat_id,
                    "⚠️ <b>Mã liên kết không hợp lệ hoặc đã hết hạn!</b>\n\n"
                    "Vui lòng đăng nhập vào Cổng Auth Gateway trên trình duyệt và bấm <b>'Liên kết Telegram'</b> để lấy mã mới.",
                )
                return

            # 2. Check if this telegram_user_id is already linked to another Odoo user
            existing_conn = (
                db.query(TelegramConnection).filter_by(telegram_user_id=telegram_user_id).first()
            )
            if existing_conn and existing_conn.user_id != lt.user_id:
                self.telegram_client.send_message(
                    chat_id,
                    "⚠️ <b>Tài khoản Telegram này đã được liên kết với một nhân viên khác!</b>\n"
                    "Vui lòng ngắt liên kết cũ trước khi ghép nối tài khoản mới.",
                )
                return

            # 3. Mark token as used
            lt.used_at = now
            target_user = db.query(User).filter_by(id=lt.user_id).first()
            if not target_user:
                return
            target_user_name = target_user.name

            # 4. Upsert TelegramConnection
            if existing_conn:
                existing_conn.private_chat_id = chat_id
                existing_conn.username = username
                existing_conn.status = "connected"
            else:
                user_conn = db.query(TelegramConnection).filter_by(user_id=target_user.id).first()
                if user_conn:
                    user_conn.telegram_user_id = telegram_user_id
                    user_conn.private_chat_id = chat_id
                    user_conn.username = username
                    user_conn.status = "connected"
                else:
                    db.add(
                        TelegramConnection(
                            user_id=target_user.id,
                            telegram_user_id=telegram_user_id,
                            private_chat_id=chat_id,
                            username=username,
                            status="connected",
                        )
                    )
            db.commit()

        safe_name = html.escape(target_user_name)
        welcome_text = (
            f"🎉 <b>Liên kết thành công!</b>\n\n"
            f"Xin chào <b>{safe_name}</b>, tài khoản Telegram của bạn đã được kết nối an toàn với Odoo.\n\n"
            f"Các lệnh hỗ trợ:\n"
            f"👉 <code>/crm</code> : Tra cứu 5 cơ hội (Lead) mới nhất của bạn\n"
            f"👉 <code>/crm từ_khóa</code> : Tìm kiếm cơ hội theo tên\n"
            f"👉 <code>/status</code> : Xem trạng thái kết nối tài khoản\n"
            f"👉 <code>/help</code> : Hướng dẫn sử dụng"
        )
        try:
            self.telegram_client.send_message(chat_id, welcome_text)
        except Exception as e:
            _logger.warning(f"Could not send welcome message: {e}")

    def _handle_start_help(self, chat_id: int, telegram_user_id: int) -> None:
        with self.session_factory() as db:
            conn = (
                db.query(TelegramConnection)
                .filter_by(telegram_user_id=telegram_user_id, status="connected")
                .first()
            )
            if conn:
                user = db.query(User).filter_by(id=conn.user_id).first()
                user_name = user.name if user else "bạn"
                msg = (
                    f"👋 Xin chào <b>{html.escape(user_name)}</b>! Bạn đã liên kết với hệ thống Odoo.\n\n"
                    f"Gõ <code>/crm</code> để tra cứu khách hàng hoặc <code>/help</code> để xem hướng dẫn."
                )
            else:
                msg = (
                    "👋 Xin chào! Để liên kết tài khoản Odoo của bạn với bot này, "
                    "vui lòng đăng nhập vào Cổng Portal Auth Gateway trên trình duyệt và bấm <b>'Liên kết Telegram'</b>."
                )
        try:
            self.telegram_client.send_message(chat_id, msg)
        except Exception as e:
            _logger.warning(f"Could not send start help message: {e}")

    def _handle_help(self, chat_id: int) -> None:
        help_text = (
            "📖 <b>Danh sách lệnh hỗ trợ:</b>\n\n"
            "🔹 <code>/crm</code> : Xem danh sách 5 cơ hội bán hàng được giao cho bạn\n"
            "🔹 <code>/crm từ_khóa</code> : Tìm kiếm nhanh cơ hội theo tên khách hàng\n"
            "🔹 <code>/status</code> : Kiểm tra trạng thái liên kết Odoo & Google\n"
            "🔹 <code>/help</code> : Hiển thị bảng trợ giúp này"
        )
        self.telegram_client.send_message(chat_id, help_text)

    def _handle_status(self, chat_id: int, telegram_user_id: int) -> None:
        with self.session_factory() as db:
            conn = (
                db.query(TelegramConnection)
                .filter_by(telegram_user_id=telegram_user_id, status="connected")
                .first()
            )
            if not conn:
                self.telegram_client.send_message(
                    chat_id,
                    "⚠️ Bạn chưa liên kết tài khoản Odoo. Vui lòng truy cập Portal để kết nối.",
                )
                return

            user = db.query(User).filter_by(id=conn.user_id).first()
            google_conn = user.google_connection if user else None
            g_status = (
                "Đã kết nối (" + google_conn.email + ")"
                if (google_conn and google_conn.status == "connected")
                else "Chưa kết nối"
            )

            status_text = (
                f"👤 <b>Hồ sơ liên kết:</b>\n"
                f"• Nhân viên: <b>{html.escape(user.name)}</b>\n"
                f"• Email Odoo: <code>{html.escape(user.login)}</code>\n"
                f"• Google Workspace: {html.escape(g_status)}\n"
                f"• Telegram: Đã liên kết (@{html.escape(conn.username or 'N/A')})"
            )
        self.telegram_client.send_message(chat_id, status_text)

    def _handle_crm(self, chat_id: int, telegram_user_id: int, search_query: str | None) -> None:
        # 1. Resolve user and check connection
        with self.session_factory() as db:
            conn = (
                db.query(TelegramConnection)
                .filter_by(telegram_user_id=telegram_user_id, status="connected")
                .first()
            )
            if not conn:
                self.telegram_client.send_message(
                    chat_id,
                    "⚠️ Bạn chưa liên kết tài khoản Odoo. Vui lòng đăng nhập Cổng Portal để liên kết trước khi tra cứu CRM.",
                )
                return

            user = db.query(User).filter_by(id=conn.user_id).first()
            if not user or not user.active:
                self.telegram_client.send_message(
                    chat_id, "⚠️ Tài khoản người dùng đã bị vô hiệu hóa."
                )
                return

            user_id = user.id
            odoo_uid = user.odoo_uid

            cred = db.query(OdooCredential).filter_by(user_id=user_id).first()
            if not cred or not cred.encrypted_secret:
                self.telegram_client.send_message(
                    chat_id,
                    "⚠️ Chưa có thông tin xác thực Odoo của bạn. Vui lòng đăng nhập lại vào Portal.",
                )
                return

            secret = decrypt_secret(cred.encrypted_secret, self.encryption_key)

        # 2. Rate limiting check (max 10 queries per minute per user)
        now = datetime.now(UTC)
        window_start = now - timedelta(minutes=1)
        history = self._crm_rate_limits.get(user_id, [])
        valid_history = [t for t in history if t > window_start]
        self._crm_rate_limits[user_id] = valid_history

        if len(valid_history) >= 10:
            self.telegram_client.send_message(
                chat_id,
                "⚠️ <b>Bạn thao tác quá nhanh!</b> Giới hạn tra cứu là 10 lần/phút. Vui lòng chờ giây lát.",
            )
            return

        self._crm_rate_limits[user_id].append(now)

        # 3. Query Odoo via OdooClient using employee's credentials
        if not self.odoo_client:
            self.telegram_client.send_message(chat_id, "⚠️ Dịch vụ Odoo chưa sẵn sàng.")
            return

        domain: list[Any] = [
            ("user_id", "=", odoo_uid),
            ("active", "=", True),
        ]
        if search_query:
            clean_search = search_query.strip()[:100]
            domain.append(("name", "ilike", clean_search))

        fields = ["id", "name", "stage_id"]
        order = "write_date desc, id desc"
        limit = 5

        try:
            records = self.odoo_client.execute(
                odoo_uid,
                secret,
                "crm.lead",
                "search_read",
                [domain],
                {"fields": fields, "order": order, "limit": limit},
            )
        except OdooAccessError:
            self.telegram_client.send_message(
                chat_id,
                "⚠️ <b>Không có quyền truy cập CRM:</b> Tài khoản Odoo của bạn không được cấp quyền đọc cơ hội bán hàng.",
            )
            return
        except OdooError as e:
            _logger.error(f"Error querying CRM for user {user_id}: {e}")
            self.telegram_client.send_message(
                chat_id,
                "⚠️ Có lỗi xảy ra khi kết nối đến máy chủ Odoo. Vui lòng thử lại sau.",
            )
            return

        # 4. Format response
        if not records:
            if search_query:
                self.telegram_client.send_message(
                    chat_id,
                    f"🔍 Không tìm thấy cơ hội bán hàng nào với từ khóa: <b>{html.escape(search_query)}</b>.",
                )
            else:
                self.telegram_client.send_message(
                    chat_id,
                    "📭 Hiện tại bạn chưa có cơ hội bán hàng (Lead) nào được giao phụ trách.",
                )
            return

        base_url = self.odoo_base_url or "https://odoo.com"
        lines = [f"📊 <b>Cơ hội bán hàng của bạn</b> ({len(records)} kết quả mới nhất):\n"]

        for idx, lead in enumerate(records, start=1):
            lead_id = lead.get("id")
            lead_name = html.escape(lead.get("name") or "Chưa đặt tên")
            stage_val = lead.get("stage_id")
            stage_name = (
                html.escape(stage_val[1])
                if isinstance(stage_val, list | tuple) and len(stage_val) > 1
                else "N/A"
            )

            lead_url = f"{base_url}/odoo/crm/{lead_id}"
            lines.append(f'{idx}. <a href="{lead_url}"><b>{lead_name}</b></a>')
            lines.append(f"   • Giai đoạn: <code>{stage_name}</code>")

        lines.append("\n💡 <i>Gõ <code>/crm từ_khóa</code> để tìm kiếm cơ hội cụ thể.</i>")
        response_text = "\n".join(lines)
        self.telegram_client.send_message(chat_id, response_text)

    def disconnect(self, user_id: int) -> None:
        with self.session_factory() as db:
            conn = db.query(TelegramConnection).filter_by(user_id=user_id).first()
            if conn:
                conn.status = "disconnected"
                db.commit()

    # Alias for flexibility
    process_update_payload = process_update_dict
