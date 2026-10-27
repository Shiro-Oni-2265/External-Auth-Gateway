# Bảng Kiểm Chứng Tiêu Chuẩn Nghiệm Thu — External Auth Gateway

Tài liệu đối chiếu và đánh giá tiêu chí hoàn thành theo đúng kế hoạch triển khai kỹ thuật.

> **Trạng thái hiện tại (2026-10-23):** Bộ kiểm thử tự động đạt, nhưng kiểm tra bổ sung đã phát hiện các khoảng trống chưa được test liên quan đến phân trang/xóa lịch, vòng đời rate limit, CSRF và thao tác xử lý xung đột. Các mục tương ứng bên dưới được đánh dấu **Chưa đạt**. Chưa triển khai với dữ liệu lịch thật cho đến khi các mục này được sửa và kiểm chứng lại.

---

## 1. Tóm tắt kết quả kiểm tra tự động

- **Thời điểm kiểm tra gần nhất**: 2026-10-23
- **Công cụ kiểm tra**: Pytest 8.3, Ruff (Linter & Formatter), HTTPX Mock (`respx`)
- **Kết quả lệnh**:
  - `python -m ruff check .`: **0 cảnh báo / 0 lỗi**
  - `python -m ruff format --check .`: **Đạt chuẩn 100%**
  - `python -m pytest -q`: **53/53 tests passed (100%)**

---

## 2. Bảng đối chiếu từng tiêu chí nghiệm thu

| STT | Hạng mục / Tiêu chí | Trạng thái | Bằng chứng kiểm thử & Ghi chú |
|:---:|---|:---:|---|
| **1** | **Khung ứng dụng & Bảo mật** | | |
| 1.1 | Chạy 1 container FastAPI, SQLite WAL, busy_timeout | **Đạt** | `test_db_persistence_and_wal`, `test_db_foreign_keys_enforced` |
| 1.2 | Mã hóa AES-256 Fernet cho credential và token | **Đạt** | `test_encryption_roundtrip`, `test_decryption_with_wrong_key` |
| 1.3 | Hash SHA-256 token tạm, session, CSRF | **Đạt** | `test_hash_token_sha256` |
| 1.4 | Từ chối khởi động production nếu thiếu khóa | **Đạt** | `test_config_production_requires_fernet_key`, `test_config_production_requires_https_urls` |
| 1.5 | Endpoint `/healthz` kiểm tra DB | **Đạt** | `test_healthz_endpoint` |
| **2** | **Xác thực Odoo & Phân quyền nhân viên** | | |
| 2.1 | Hỗ trợ 2 hình thức: Password và API Key cá nhân | **Đạt** | `test_odoo_client_authenticate_success`, `test_login_success_active_internal_user` |
| 2.2 | Chỉ cho phép `active=True, share=False` | **Đạt** | `test_login_rejects_portal_user`, `test_login_rejects_inactive_user` |
| 2.3 | Giới hạn 5 lần đăng nhập sai / 15 phút | **Chưa đạt** | Service rate limit được tạo lại theo từng HTTP request; 7 lần thử sai liên tiếp vẫn trả `401`, không trả `429`. |
| 2.4 | Script `scripts/check_odoo.py` an toàn qua prompt | **Đạt** | Triển khai trong `scripts/check_odoo.py`, ẩn ký tự nhập |
| **3** | **Google Workspace OAuth** | | |
| 3.1 | PKCE S256, state, nonce ngẫu nhiên một lần | **Đạt** | `test_begin_oauth_generates_url_and_link_token`, `test_callback_invalid_or_used_state` |
| 3.2 | Xác minh ID Token và kiểm tra claim `hd` miền công ty | **Đạt** | `test_callback_success_stores_connection`, `test_callback_rejects_unauthorized_domain` |
| 3.3 | Refresh token khóa theo user, xử lý `invalid_grant` | **Đạt** | `test_refresh_token_success_and_invalid_grant` |
| 3.4 | Chặn kết nối đè tài khoản Google khác subject | **Đạt** | `test_callback_reconnect_different_sub_rejected` |
| **4** | **Telegram Bot & Ghép nối an toàn** | | |
| 4.1 | Token một lần, QR code nội bộ không gọi dịch vụ ngoài | **Đạt** | `test_telegram_issue_link_and_qr` |
| 4.2 | Ghép nối private chat, chống chiếm đoạt danh tính | **Đạt** | `test_telegram_pairing_success`, `test_telegram_rejects_already_linked_to_different_user` |
| 4.3 | Webhook bắt buộc secret token, lưu DB trước khi ACK | **Đạt** | `test_telegram_webhook_secret_and_idempotency` |
| 4.4 | Xử lý sự kiện bot bị block (403) | **Đạt** | Xử lý `BotBlockedError` trong `ReminderService.deliver_due` |
| **5** | **Tra cứu CRM qua Telegram** | | |
| 5.1 | Lệnh `/crm`, `/crm từ_khóa`, `/crm@bot` | **Đạt** | `test_crm_without_keyword`, `test_crm_with_keyword_and_bot_suffix` |
| 5.2 | Phân quyền: chỉ đọc lead của chính mình (`user_id=UID`) | **Đạt** | Kiểm tra domain Odoo XML-RPC trong `test_crm_without_keyword` |
| 5.3 | Escape HTML chống injection, gắn link trực tiếp | **Đạt** | `html.escape` và định dạng link trong `test_crm_without_keyword` |
| 5.4 | Rate limit 10 lần/phút/user | **Chưa đạt** | Cùng vấn đề vòng đời service; cần kiểm thử qua nhiều HTTP request/webhook thực tế. |
| **6** | **Đồng bộ Lịch Google & Odoo** | | |
| 6.1 | Chuẩn hóa 2 chiều, loại bỏ recurrence và lịch mời | **Đạt** | `test_google_timed_event_normalization`, `test_event_eligibility_checks` |
| 6.2 | Chuyển đổi all-day inclusive (Odoo) & exclusive (Google) | **Đạt** | `test_all_day_event_inclusive_exclusive_conversion` |
| 6.3 | Fingerprint SHA-256 đối soát baseline | **Đạt** | `test_fingerprint_generation` |
| 6.4 | Deterministic Google Event ID, chống lặp create | **Đạt** | `test_sync_creates_google_event_from_odoo` |
| 6.5 | Phát hiện và giải quyết Conflict | **Chưa đạt** | Route Portal có thể chỉ đổi trạng thái sang `synced` mà không cập nhật lịch phía còn lại. |
| **7** | **Nhắc họp qua Telegram (Trước 15 phút)** | | |
| 7.1 | Quét sự kiện trong khoảng [now, now + 15m] | **Đạt** | `test_reminder_enqueued_in_15_min_window` |
| 7.2 | Bỏ qua sự kiện cả ngày hoặc sự kiện đã quá giờ | **Đạt** | `test_reminder_ignores_allday_and_past_events` |
| 7.3 | Outbox dedup_key chống gửi lặp | **Đạt** | `test_reminder_enqueued_in_15_min_window` |
| 7.4 | Gửi tin thành công chuyển trạng thái `sent` | **Đạt** | `test_outbox_deliver_due` |
| **8** | **Cổng Portal Người Dùng (Giao diện web)** | | |
| 8.1 | Giao diện tiếng Việt, responsive 360px+ và desktop | **Đạt** | `test_portal_login_page_renders_form`, CSS Grid/Flexbox |
| 8.2 | Bảo vệ CSRF, session cookie HttpOnly Lax | **Chưa đạt** | Cookie có `HttpOnly`/`SameSite=Lax`, nhưng một số POST thay đổi trạng thái chưa xác minh CSRF; login vẫn nhận request không có token. |
| 8.3 | Quản lý Google, Telegram, Calendar sync, Conflict | **Chưa đạt** | Bật/tạm dừng đồng bộ chưa thay đổi trạng thái; đối soát ghi sai trường và xử lý conflict chưa gọi service thực tế. |
| **9** | **Docker, Vận hành & Triển khai thực tế** | | |
| 9.1 | Dockerfile slim, non-root user `appuser`, 1 worker | **Đạt** | Dockerfile định nghĩa đầy đủ chỉ dẫn bảo mật |
| 9.2 | Compose có Gateway + Cloudflare Tunnel | **Chưa kiểm chứng lại** | Docker Engine hiện không kết nối; cần build và chạy smoke test khi Docker hoạt động. |
| 9.3 | Backup script bằng SQLite Online Backup API | **Đạt** | `scripts/backup.py` dùng `sqlite3.Connection.backup` |
| 9.4 | **Kiểm chứng trực tiếp trên Tenant Odoo thật** | **Chưa kiểm chứng** | Chưa có thông tin tenant URL và API Key thật của khách hàng. Agent cung cấp sẵn script `scripts/check_odoo.py` để chạy xác nhận ngay khi có credential. |

---

## 3. Các giới hạn kỹ thuật được ghi nhận rõ ràng

1. **Rủi ro xóa nhầm lịch**: Luồng hiện tại giới hạn 200 sự kiện Odoo và chỉ đọc một trang Google. Một sự kiện không xuất hiện trong tập kết quả có thể bị hiểu nhầm là đã xóa; phải bổ sung phân trang và xác minh riêng trước khi cho phép xóa phía còn lại.
2. **Odoo Create Timeout (Ambiguous Intent)**: Nếu request tạo lịch sang Odoo bị đứt mạng giữa chừng, Gateway không tự động tạo lại để tránh nhân đôi lịch, mà chuyển sang trạng thái `ambiguous` để người dùng đối soát trên Portal.
3. **Telegram Outbox Delivery Timeout**: Khi gửi tin sang Telegram gặp timeout mạng lúc chưa nhận HTTP status, tin nhắn chuyển sang `delivery_unknown` thay vì retry mù quáng để tránh spam nhân viên.
