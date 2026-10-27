# Hướng dẫn Vận hành & Tích hợp: AI Agent & External Auth Gateway

Tài liệu này hướng dẫn cách kết nối, vận hành, kiểm thử và xử lý sự cố giữa **AI Agent** (`D:\Module AI - Agent`) và **External Auth Gateway** (`D:\Auth_gateway\auth-gateway`).

---

## 1. Kiến trúc tổng quan & Phân định trách nhiệm

```text
[Người dùng Telegram]
        │
        ▼ (Tin nhắn /start, /help, /status, /crm) ──► Xử lý trực tiếp tại Gateway
  [Auth Gateway: 8080]
        │ (Lưu update_id vào queue: pending, ACK webhook ngay lập tức)
        ▼ (TelegramAIWorker chạy ngầm nhận turn với lease 90s)
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
  - Quản lý hàng đợi bền vững (`telegram_updates`) với state machine, lease và cơ chế phòng ngừa gửi trùng.
  - Cổng Portal người dùng (`http://localhost:8080/portal/dashboard`).
- **AI Agent (Điều phối nghiệp vụ & Điểm tin):**
  - Xử lý ngôn ngữ tự nhiên, phân tích ngữ cảnh người dùng qua `handle_user_turn()`.
  - Tổng hợp điểm tin `/today` từ 100% dữ liệu thật, loại bỏ toàn bộ số liệu minh họa/giả lập.
  - Quản lý công việc cá nhân (`LocalTaskProvider`) phân tách nghiêm ngặt theo `owner_key = "{tenant}:{database}:{odoo_uid}"`.

---

## 2. Cấu hình biến môi trường (`.env`)

### Trên AI Agent (`D:\Module AI - Agent\.env`)
```bash
PORT=8000
INTEGRATION_MODE=gateway
GATEWAY_BASE_URL=http://localhost:8080
GATEWAY_TO_AGENT_SECRET=your_gateway_to_agent_secret_min32chars_unique_1
AGENT_TO_GATEWAY_SECRET=your_agent_to_gateway_secret_min32chars_unique_2
```

### Trên Auth Gateway (`D:\Auth_gateway\auth-gateway\.env`)
```bash
PUBLIC_BASE_URL=http://localhost:8080
ENABLE_AI_AGENT_FORWARDING=true
AI_AGENT_BASE_URL=http://localhost:8000
GATEWAY_TO_AGENT_SECRET=your_gateway_to_agent_secret_min32chars_unique_1
AGENT_TO_GATEWAY_SECRET=your_agent_to_gateway_secret_min32chars_unique_2
```

> [!IMPORTANT]
> 1. Cả hai service secret `GATEWAY_TO_AGENT_SECRET` và `AGENT_TO_GATEWAY_SECRET` mặc định là chuỗi rỗng (`""`). Khi bật `INTEGRATION_MODE=gateway` hoặc `ENABLE_AI_AGENT_FORWARDING=true`, hệ thống bắt buộc hai secret này phải:
>    - Có độ dài tối thiểu **32 ký tự**.
>    - **Khác nhau hoàn toàn** giữa 2 chiều (Gateway→Agent và Agent→Gateway).
>    - Nếu cấu hình rỗng, trùng hoặc quá ngắn, cả AI Agent và Gateway sẽ từ chối khởi động ngay lập tức.
> 2. Mọi endpoint nội bộ đều sử dụng so sánh hằng số thời gian (`secrets.compare_digest`) để chống timing attack.
> 3. Không để lộ secret sử dụng được trong mã nguồn hay `.env.example`.

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

## 4. Quản lý hàng đợi Telegram & Cơ chế Chống gửi trùng

### 4.1. Vòng đời xử lý bản ghi Telegram (`telegram_updates`)

```text
pending ──► processing_ai ──► ai_received ──► processed
                │
                ├─► retry_wait ──► processing_ai (khi gặp lỗi mạng/timeout/5xx, tối đa 3 lần)
                │
                └─► failed (khi gặp HTTP 4xx, phản hồi sai cấu trúc, hoặc hết lượt retry)
```

- **Tiếp nhận không nghẽn:** Webhook lưu update và phản hồi HTTP 200 (ACK) ngay; poller lưu vào DB trước khi tăng offset. Update trùng bị chặn bởi unique index `update_id`.
- **Worker xử lý nền (`TelegramAIWorker`):** Chạy thread riêng, quét mỗi giây; nhận bản ghi theo cơ chế cập nhật trạng thái có điều kiện (`UPDATE ... WHERE status IN ('pending', 'retry_wait') OR (status = 'processing_ai' AND lease_expires_at < :now)`).
- **Lease và Timeout:**
  - Lease gán cho worker là **90 giây**.
  - Timeout gọi HTTP sang AI Agent là **25 giây**.
- **Chính sách Retry:**
  - Chỉ retry khi lỗi kết nối, timeout hoặc HTTP 5xx từ AI Agent.
  - Tối đa **3 lần gọi** tổng cộng (chờ 5 giây lần 1, 30 giây lần 2).
  - Lỗi HTTP 4xx hoặc cấu trúc phản hồi không hợp lệ chuyển ngay sang `failed`, không retry.
  - Phản hồi từ AI được lưu ngay vào `ai_response` khi nhận thành công (`ai_received`); không bao giờ gọi lại AI nếu đã có phản hồi.

### 4.2. Giao nhận tin nhắn Telegram & Trạng thái `delivery_unknown`

- **Định dạng tin:** Phản hồi từ AI Agent được gửi qua Telegram bằng plain text chuẩn (`parse_mode=""`). Chỉ khi Telegram trả về mã lỗi xác nhận từ chối do định dạng (`can't parse entities` / `formatting`) mới fallback sang plain text.
- **Theo dõi tiến độ chia đoạn (Chunk Delivery Tracking):**
  - Danh sách đoạn tin được tuần tự hóa vào `delivery_chunks` (JSON).
  - Sau mỗi đoạn được Telegram API xác nhận thành công (HTTP 200 từ Telegram), Gateway cập nhật `delivery_index`.
  - Các đoạn đã gửi thành công **tuyệt đối không gửi lại**.
- **Xử lý `delivery_unknown` (Không thể xác định trạng thái giao):**
  - Nếu gặp timeout mạng hoặc mất kết nối trong khi gửi một đoạn tin đến Telegram (không biết Telegram đã nhận và hiển thị cho người dùng hay chưa):
    - Đánh dấu `delivery_status = 'delivery_unknown'`.
    - Cập nhật `status = 'failed'`.
    - **Dừng gửi ngay lập tức và không tự động retry**.
  - Khi khởi động lại dịch vụ (restart / crash recovery), các bản ghi còn dang dở ở trạng thái `sending` tự động chuyển sang `delivery_unknown` và `failed`, không tự phát lại để tránh spam người dùng.

---

## 5. Quy trình Vận hành & Đối soát thủ công (`delivery_unknown`)

Khi gặp bản ghi có `delivery_status = 'delivery_unknown'`:

1. **Truy vấn danh sách bản ghi nghi vấn:**
   ```sql
   SELECT id, update_id, telegram_chat_id, delivery_index, delivery_chunks, last_error, updated_at
   FROM telegram_updates
   WHERE delivery_status = 'delivery_unknown'
   ORDER BY updated_at DESC;
   ```
2. **Đối soát:**
   - Lấy `update_id` và mã tương quan `request_id` từ log hệ thống.
   - Kiểm tra trên Telegram chat xem người dùng đã nhận được nội dung đoạn `delivery_index` hay chưa.
   - Ghi chú: Nhật ký hệ thống chỉ ghi nhận mã định danh, `update_id` và `request_id`, tuyệt đối **không log nội dung email, thông tin nhạy cảm hay token**.
3. **Quyết định thủ công:**
   - Nếu tin chưa đến người dùng: Người dùng có thể gửi lại câu lệnh trên bot Telegram.
   - Nếu tin đã đến: Giữ nguyên trạng thái `delivery_unknown` để phục vụ audit, không cần can thiệp DB.

---

## 6. Quy trình Khởi động lại & Rollback

### Khởi động lại an toàn (Startup Recovery)
Khi Gateway khởi động:
- Tự động chạy migration an toàn bổ sung các cột: `lease_expires_at`, `retry_at`, `delivery_chunks`, `delivery_index`, `delivery_status`, `last_error`, `updated_at`.
- Thực hiện recovery:
  - Bản ghi `sending` dang dở -> chuyển sang `delivery_unknown` và `failed`.
  - Bản ghi `processing_ai` hết hạn lease -> cho phép worker nhận lại.
  - Bản ghi `retry_wait` và `pending` -> tiếp tục xử lý bình thường.
  - Bản ghi `ai_received` -> worker tiếp tục giao các đoạn còn lại chưa gửi.

### Rollback & Tách rời độc lập
Nếu cần tạm dừng tích hợp AI Agent để bảo trì:
1. Đặt `ENABLE_AI_AGENT_FORWARDING=false` trong `.env` của Auth Gateway.
2. Khởi động lại dịch vụ Gateway.
3. **Trạng thái sau rollback:**
   - Gateway không nhận hoặc xử lý yêu cầu AI mới.
   - Vẫn phục vụ người dùng bình thường: Portal, OAuth Google, các lệnh `/start`, `/help`, `/status`, `/crm`.
   - Các tin nhắn trò chuyện hoặc `/today` được Gateway phản hồi thông báo bảo trì, không gọi sang AI Agent.
