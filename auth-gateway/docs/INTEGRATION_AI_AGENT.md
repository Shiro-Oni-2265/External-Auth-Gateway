# Hướng dẫn Vận hành & Tích hợp: AI Agent & External Auth Gateway

Tài liệu này hướng dẫn cách kết nối, vận hành, kiểm thử và xử lý sự cố giữa **AI Agent** (`D:\Module AI - Agent`) và **External Auth Gateway** (`D:\Auth_gateway\auth-gateway`).

---

## 1. Kiến trúc tổng quan & Phân định trách nhiệm

```text
[Người dùng Telegram]
        │
        ▼ (Tin nhắn /start, /help, /status, /crm) ──► Xử lý trực tiếp tại Gateway
  [Auth Gateway: 8080]
        │
        ▼ (Tin nhắn /today, "Hôm nay tôi có gì?", câu hỏi nghiệp vụ)
    [POST /internal/v1/telegram/turn]
        │
        ▼
   [AI Agent: 8000]
        │
        ├─► POST /internal/v1/identity/resolve (Gateway) ──► Xác thực (tenant, db, odoo_uid)
        ├─► GET  /internal/v1/users/{id}/calendar/events (Gateway) ──► Dữ liệu Google Calendar thật
        ├─► GET  /internal/v1/users/{id}/gmail/unread (Gateway)    ──► Dữ liệu Gmail thật
        └─► LocalTaskProvider (Kho SQLite cục bộ AI Agent)         ──► Lọc task theo owner_key
```

### Phân công trách nhiệm
- **Gateway (Cổng xác thực & ngoại vi):**
  - Quản lý đăng nhập Odoo, mã hóa token AES-256 Fernet.
  - Quản lý OAuth Google (Calendar, Gmail) và duy trì làm mới token tự động.
  - Điểm tiếp nhận Telegram bot duy nhất (Webhook hoặc Poller).
  - Cổng Portal người dùng (`http://localhost:8080/portal/dashboard`).
- **AI Agent (Điều phối nghiệp vụ & Điểm tin):**
  - Xử lý ngôn ngữ tự nhiên, phân tích ngữ cảnh người dùng.
  - Tổng hợp điểm tin `/today` từ 100% dữ liệu thật, không dùng số liệu minh họa.
  - Quản lý công việc cá nhân (`LocalTaskProvider`) phân tách theo `owner_key = "{tenant}:{database}:{odoo_uid}"`.

---

## 2. Cấu hình biến môi trường (`.env`)

### Trên AI Agent (`D:\Module AI - Agent\.env`)
```bash
PORT=8000
INTEGRATION_MODE=gateway
GATEWAY_BASE_URL=http://localhost:8080
GATEWAY_TO_AGENT_SECRET=your_gateway_to_agent_secret_min32chars
AGENT_TO_GATEWAY_SECRET=your_agent_to_gateway_secret_min32chars
```

### Trên Auth Gateway (`D:\Auth_gateway\auth-gateway\.env`)
```bash
PUBLIC_BASE_URL=http://localhost:8080
ENABLE_AI_AGENT_FORWARDING=true
AI_AGENT_BASE_URL=http://localhost:8000
GATEWAY_TO_AGENT_SECRET=your_gateway_to_agent_secret_min32chars
AGENT_TO_GATEWAY_SECRET=your_agent_to_gateway_secret_min32chars
```

> [!IMPORTANT]
> Hai secret `GATEWAY_TO_AGENT_SECRET` và `AGENT_TO_GATEWAY_SECRET` phải khớp nhau ở cả 2 phía. Mọi endpoint nội bộ đều sử dụng so sánh hằng số thời gian (`secrets.compare_digest`) để chống timing attack.

---

## 3. Quy trình kết nối của nhân viên (Portal)

1. **Bước 1 — Đăng nhập Portal:**
   - Mở trình duyệt truy cập `http://localhost:8080`.
   - Nhập thông tin tài khoản Odoo của nhân viên (Email và Mật khẩu hoặc Personal API Key).
2. **Bước 2 — Ghép nối Telegram:**
   - Tại màn hình Dashboard, bấm **"Liên kết Telegram"**.
   - Bấm vào link mở Telegram bot hoặc quét mã QR trên màn hình.
   - Nhấn **Start** trên Telegram. Gateway xác thực liên kết bảo mật vào tài khoản Odoo của nhân viên.
3. **Bước 3 — Cấp quyền Google Workspace:**
   - Bấm **"Kết nối Google Workspace"**.
   - Đăng nhập tài khoản Google công ty (kiểm tra miền `hd`).
   - Cấp đủ 2 quyền: **Google Calendar** và **Gmail (chỉ đọc)**.
   *(Nếu tài khoản đã liên kết từ trước thiếu quyền Gmail, trên Portal sẽ hiển thị nút cấp bổ sung quyền Gmail).*

---

## 4. Kiểm thử luồng điểm tin trên Telegram

Mở bot Telegram và gửi một trong các câu lệnh:
- `/today`
- *"Hôm nay tôi có gì?"*
- *"Hôm nay tôi cần làm gì?"*

### Kết quả mong đợi:
Agent trả về điểm tin định dạng Markdown chuẩn:
1. **Lịch làm việc hôm nay:** Giờ bắt đầu - kết thúc, tên sự kiện, đường link Google Calendar (xử lý chuẩn sự kiện cả ngày).
2. **Công việc cần xử lý:** Phân tách rõ ràng giữa *Việc quá hạn* và *Việc đến hạn hôm nay*, không hiển thị task của nhân viên khác.
3. **Hộp thư Gmail:** Tổng số thư chưa đọc thật từ hộp thư đến và danh sách tối đa 5 email gần nhất kèm link mở trực tiếp.
4. **Thời điểm truy xuất:** Hiển thị rõ mốc giờ và múi giờ nhân viên (`Asia/Bangkok`).

---

## 5. Quy trình Rollback & Tách rời độc lập

Nếu cần tạm dừng tích hợp để bảo trì hoặc điều tra sự cố:
1. Đặt `ENABLE_AI_AGENT_FORWARDING=false` trong file `.env` của Auth Gateway.
2. Khởi động lại dịch vụ Gateway.
3. **Trạng thái sau rollback:**
   - Gateway vẫn hoạt động bình thường, phục vụ đăng nhập portal, liên kết tài khoản và các lệnh `/start`, `/help`, `/status`, `/crm`.
   - Các tin nhắn trò chuyện hoặc `/today` sẽ được Gateway phản hồi lịch sự rằng tính năng AI đang bảo trì, không chuyển tiếp sang AI Agent.
