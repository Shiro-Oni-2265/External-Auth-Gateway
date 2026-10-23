# Kế hoạch triển khai External Auth Gateway — tài liệu giao agent

## 1. Mục tiêu, phạm vi và nguyên tắc thực hiện

### 1.1. Mục tiêu

Xây ứng dụng web độc lập cho nhân viên liên kết tài khoản Odoo với Google Workspace và Telegram, phục vụ:

- Xác thực bằng email + mật khẩu hoặc API Key cá nhân Odoo.
- Tự liên kết/ngắt Google Workspace và Telegram.
- Tra cứu lead được giao qua Telegram.
- Đồng bộ tạo/sửa lịch cơ bản giữa Google Calendar và Odoo Calendar.
- Nhắc họp qua Telegram trước 15 phút.

**Đây là kế hoạch triển khai kỹ thuật. Agent thực hiện theo thứ tự nhiệm vụ ở mục 4, không tự mở rộng phạm vi.**

### 1.2. Ràng buộc bắt buộc

- Công ty đã chọn **Odoo Online Enterprise**, môi trường được cung cấp là 19.3.
- Không cài addon, thêm custom field hoặc triển khai mã Python lên Odoo.
- Không sửa dự án Odoo, addon cũ hoặc Docker Compose đang chạy.
- Tạo ứng dụng riêng trong thư mục `auth-gateway`.
- Một container ứng dụng; Cloudflare Tunnel chạy riêng.
- Không thêm Redis, Celery, PostgreSQL, dịch vụ SaaS hoặc phần mềm trả phí.
- Không dùng credential quản trị chung để đọc dữ liệu nhân viên.
- Không đưa credential thật vào source code, test fixture, log hoặc tài liệu.
- Giao diện tiếng Việt, dùng được trên điện thoại và máy tính.
- Mục tiêu 1–2 ngày dành cho bản thử nghiệm; không coi đây là cam kết hoàn thiện sản phẩm cho toàn công ty.

### 1.3. Trạng thái đầu vào

Bên hệ thống đã phản hồi rằng tenant hỗ trợ External API và API Key cá nhân. Agent vẫn phải kiểm tra trực tiếp khi có credential:

1. Xác thực tài khoản nhân viên.
2. Đọc thông tin chính nhân viên đó.
3. Đọc CRM theo quyền.
4. Tạo, đọc lại và sửa một lịch thử.

Nếu chưa có credential, tiếp tục xây dựng và kiểm thử bằng mock; ghi rõ kiểm chứng tenant thật chưa thực hiện. Không báo cáo tích hợp thực tế đã thành công.

### 1.4. Ngoài phạm vi phiên bản này

- Lịch lặp và ngoại lệ của lịch lặp.
- Đồng bộ khách mời, RSVP, phòng họp và Google Meet.
- Xóa lịch hai chiều.
- Giao diện duyệt Drive hoặc chỉnh sửa Sheets.
- Thông báo thay đổi CRM tự động.
- Nhiều tenant Odoo, nhiều Google account cho một nhân viên.
- Dashboard quản trị toàn công ty.

---

## 2. Kiến trúc, cấu trúc mã và hợp đồng dữ liệu

### 2.1. Công nghệ

- Python 3.12 (hoặc 3.11+ tương thích).
- FastAPI, Uvicorn, Jinja2.
- HTML/CSS và JavaScript thuần.
- SQLAlchemy 2 + SQLite.
- HTTPX cho Google/Telegram.
- `xmlrpc.client` cho Odoo.
- `cryptography`/Fernet để mã hóa bí mật.
- `google-auth` để xác minh Google ID token.
- Thư viện tạo QR chạy nội bộ; không gửi link ghép nối tới dịch vụ QR bên ngoài.
- Pytest, Ruff; HTTPX mock bằng `respx`.

Chốt phiên bản dependency tương thích và lưu lockfile khi triển khai. Không dùng version nổi trong bản bàn giao.

**Mô hình chạy:**

- Một Uvicorn worker, không dùng `--reload` khi triển khai.
- Route nghiệp vụ và service dùng synchronous I/O.
- Background scheduler chạy qua FastAPI lifespan; đưa tác vụ blocking vào thread.
- Dùng khóa trong tiến trình theo nhân viên để tránh tác vụ nền và request cùng cập nhật một liên kết.
- Không giữ transaction SQLite trong lúc chờ API bên ngoài.
- Tối đa 4 tác vụ nhân viên chạy đồng thời.

### 2.2. Cấu trúc dự án

```text
auth-gateway/
  app/
    main.py
    config.py
    db.py
    models.py
    security.py
    auth/
      routes.py
      service.py
    odoo/
      client.py
      service.py
    google/
      routes.py
      oauth.py
      client.py
    telegram/
      routes.py
      client.py
      service.py
    calendar/
      normalize.py
      service.py
      reminders.py
    jobs/
      scheduler.py
      service.py
    portal/
      routes.py
      templates/
      static/
  tests/
    unit/
    integration/
  scripts/
    check_odoo.py
    backup.py
  docs/
    SETUP.md
    OPERATIONS.md
    ACCEPTANCE.md
  pyproject.toml
  Dockerfile
  compose.yaml
  .env.example
  .gitignore
  README.md
```

Các service nhận định danh nhân viên từ session hoặc job đã được lưu; không lấy `odoo_uid` từ request rồi tin trực tiếp.

### 2.3. Cấu hình

| Biến | Nội dung |
|---|---|
| `APP_ENV` | `development`, `test`, `production` |
| `PUBLIC_BASE_URL` | URL Portal; production bắt buộc HTTPS |
| `ODOO_URL` | Tenant cố định, bắt buộc HTTPS |
| `ODOO_DB` | Database cố định |
| `DATABASE_URL` | SQLite file trong volume |
| `ENCRYPTION_KEY` | Khóa Fernet, lưu ngoài database |
| `GOOGLE_CLIENT_ID` | OAuth client |
| `GOOGLE_CLIENT_SECRET` | OAuth secret |
| `GOOGLE_WORKSPACE_DOMAINS` | Danh sách Workspace domain được phép |
| `TELEGRAM_BOT_TOKEN` | Token bot dùng riêng cho Gateway |
| `TELEGRAM_BOT_USERNAME` | Username bot |
| `TELEGRAM_WEBHOOK_SECRET` | Secret webhook bắt buộc |
| `CLOUDFLARE_TUNNEL_TOKEN` | Chỉ dùng cho dịch vụ cloudflared |

Các mặc định trong mã: session 8 giờ; token liên kết 10 phút; refresh job 60 giây; Calendar 120 giây; reminder 60 giây; múi giờ dự phòng `Asia/Bangkok`.

Ứng dụng phải từ chối khởi động production nếu thiếu cấu hình bảo mật. Không sinh khóa mã hóa mới sau mỗi lần khởi động.

### 2.4. Dữ liệu và ràng buộc

| Bảng | Nội dung và ràng buộc chính |
|---|---|
| `users` | Tenant, database, Odoo UID, login, tên, partner ID, múi giờ, trạng thái; unique tenant + database + UID |
| `odoo_credentials` | Một credential/user; loại `password` hoặc `api_key`, secret đã mã hóa |
| `sessions` | Hash session token, user, CSRF secret, hạn dùng |
| `link_tokens` | Hash token, mục đích, user/session, hạn dùng, thời điểm đã sử dụng; OAuth lưu thêm nonce và PKCE verifier đã mã hóa |
| `google_connections` | Google `sub`, email, scope, token mã hóa, hạn dùng, trạng thái; một liên kết/user và unique `sub` |
| `telegram_connections` | Telegram user ID, private chat ID, username, trạng thái; unique user ID và chat ID |
| `calendar_pairs` | User, Google subject/calendar, Odoo event ID, Google event ID, snapshot/fingerprint, trạng thái, lỗi |
| `sync_intents` | Ý định tạo/cập nhật bên ngoài, nguồn/đích, trạng thái và kết quả; chống gọi tạo lại khi chưa rõ kết quả |
| `telegram_updates` | Unique `update_id`, dữ liệu tối thiểu cần xử lý, trạng thái, lần thử |
| `outbox` | Tin chờ gửi, khóa chống lặp, trạng thái, thời điểm thử tiếp theo |
| `job_runs` | Job/user, lần thành công, mã lỗi và thời điểm thử tiếp theo |

Quy định:

- SQLite bật WAL, foreign keys và busy timeout.
- Token tạm/session lưu hash SHA-256; credential và OAuth token mã hóa.
- `calendar_pairs` có unique theo user + Odoo event và Google subject + calendar + event.
- Không xóa mapping khi ngắt Google; giữ để kết nối lại không nhân đôi lịch.
- Phiên bản đầu chỉ cho kết nối lại cùng Google `sub`. Đổi Google account cần quy trình migration riêng.
- Xóa token tạm/session hết hạn; dọn update, outbox và lịch sử job hoàn tất sau 30 ngày. Không dọn mapping hoặc intent chưa đối soát.

---

## 3. Quy tắc chức năng phải triển khai

*(Tham khảo chi tiết tại phần 3 của tài liệu đặc tả: Xác thực Odoo, Google OAuth, Telegram, CRM, Calendar sync, Nhắc họp).*

---

## 4. Tiến độ các nhiệm vụ

### Nhiệm vụ 1 — Khung ứng dụng, cấu hình và lưu trữ
- [x] Tạo FastAPI app factory cho test độc lập.
- [x] Thêm SQLite schema và bootstrap có version; không làm mất dữ liệu khi restart.
- [x] Thêm mã hóa/giải mã, hash token và session store.
- [x] Thêm startup validation, secret redaction và `/healthz`.
- [x] Tạo `.env.example` với giá trị minh họa, `.gitignore` loại secrets/database.

### Nhiệm vụ 2 — Odoo client và đăng nhập
- [x] XML-RPC có timeout 20 giây, xác minh TLS; tạo client riêng mỗi thao tác/thread.
- [x] Chuẩn hóa lỗi xác thực, quyền, network và lỗi nghiệp vụ.
- [x] Triển khai login bằng hai loại credential, session, CSRF, logout, rate limit.
- [x] Lưu/cập nhật credential sau login thành công; không lưu credential sai.
- [x] Tạo `scripts/check_odoo.py`, nhập secret qua prompt hoặc biến môi trường, không qua command-line argument.

### Nhiệm vụ 3 — Google OAuth và refresh
- [x] Port logic được phép kế thừa từ addon.
- [x] State/nonce/PKCE và ID token verification.
- [x] Kiểm tra Workspace domain, Google subject uniqueness, granted scopes.
- [x] Lưu token mã hóa; refresh có khóa; disconnect và reconnect cùng subject.
- [x] Giữ helper Drive/Sheets dạng service nội bộ, không mở route thao tác dữ liệu.

### Nhiệm vụ 4 — Telegram linking và webhook bền vững
- [x] Link và QR dùng token một lần.
- [x] Ghép private chat trong transaction, enforce uniqueness.
- [x] Webhook secret, validate JSON và lưu update trước ACK.
- [x] Processor đọc các update chưa hoàn tất, khôi phục sau restart.
- [x] `/start`, `/help`, `/status`, tin thử, ngắt liên kết.
- [x] Không tự gọi `setWebhook` hoặc đổi webhook bot khi ứng dụng khởi động.

### Nhiệm vụ 5 — CRM theo quyền nhân viên
- [x] Parse `/crm`, `/crm từ_khóa`, `/crm@bot`.
- [x] Domain, fields, order, limit đúng mục 3.3.
- [x] Escape kết quả và tạo link Odoo từ base URL cấu hình.
- [x] Lỗi quyền trả thông báo; không dùng fallback tài khoản khác.
- [x] Thêm rate limit theo user.

### Nhiệm vụ 6 — Chuẩn hóa và đồng bộ Calendar
- [x] Kiểu sự kiện chuẩn hóa và chuyển đổi hai chiều.
- [x] Kiểm tra eligibility, privacy, all-day và múi giờ.
- [x] Pagination, cửa sổ quét và kiểm tra mapping ngoài cửa sổ.
- [x] Fingerprint/baseline, create/update và conflict.
- [x] Intent trước create, retry Google xác định ID, Odoo ambiguous và đối soát.
- [x] Missing/unavailable, owner change, recurrence change.
- [x] Đọc lại sau ghi trước khi lưu baseline.

### Nhiệm vụ 7 — Scheduler, outbox và nhắc họp
- [x] Một scheduler khởi động/dừng cùng lifespan.
- [x] Refresh 60 giây, sync 120 giây, reminders và update/outbox 60 giây hoặc sớm hơn khi có việc.
- [x] Không chạy chồng cùng user/job; tối đa 4 user đồng thời.
- [x] Retry network/5xx với 30, 60, 120 giây, sau đó tối đa 15 phút; dùng jitter và lưu `next_attempt_at`.
- [x] Một user lỗi không chặn các user khác.
- [x] Dừng lịch sự; phục hồi trạng thái in-flight theo quy tắc ambiguous/delivery_unknown.
- [x] Cleanup dữ liệu hết hạn.

### Nhiệm vụ 8 — Portal hoàn chỉnh
- [x] Login và dashboard tiếng Việt.
- [x] Hai khối Google/Telegram; quyền đã cấp, trạng thái, lỗi, QR và đếm hạn.
- [x] Form bật/tạm dừng Calendar, nêu rõ phạm vi nhập ban đầu.
- [x] Form resolve conflict và đối soát ambiguous.
- [x] Xác nhận ngắt liên kết; giải thích logout không dừng tác vụ.
- [x] Escape nội dung, không đưa token hoặc credential vào HTML/localStorage.
- [x] Responsive tại 360px và desktop; hỗ trợ label, bàn phím và focus rõ.

### Nhiệm vụ 9 — Docker, vận hành và kiểm chứng thực tế
- [x] Image Python slim, user không phải root, một worker, healthcheck, restart policy.
- [x] Volume `/data`; không mount source Odoo hoặc addon vào ứng dụng.
- [x] Compose có dịch vụ gateway và cloudflared; chỉ cloudflared đưa ứng dụng ra ngoài.
- [x] Hướng dẫn Named Tunnel, Google OAuth Internal, redirect URI và đăng ký webhook bằng lệnh riêng.
- [x] Backup bằng SQLite backup API; hướng dẫn phục hồi DB và khóa mã hóa.
- [x] Hướng dẫn xử lý key hết hạn, Google reconnect, bot blocked, conflict, ambiguous và rollback.
- [x] Chạy acceptance trên tenant khi đủ cấu hình; thiếu gì phải ghi rõ.
