# Báo Cáo Bàn Giao Sửa Lỗi Tích Hợp Auth Gateway & AI Agent

## 1. Mục tiêu & Kết quả tổng quan

Toàn bộ kế hoạch sửa lỗi tích hợp 7 nhiệm vụ (Task 1 đến Task 7) giữa **Auth Gateway** (`D:\ODOO\Auth_gateway\auth-gateway`) và **AI Agent** (`D:\ODOO\Module AI - Agent`) đã được hoàn thành 100%, tuân thủ nghiêm ngặt phương pháp Test-Driven Development (TDD) và nguyên tắc bảo toàn mã nguồn.

Luồng nghiệp vụ hoàn thiện:
**Đăng nhập Odoo tại Gateway ➔ Liên kết Telegram & Google (hỗ trợ Gmail cá nhân) ➔ Bot nhận diện đúng người dùng & quyền ➔ Xem trước (Freeze payload, TTL 5 phút) ➔ Xác nhận (`/confirm <id>`, single-use) ➔ Thực thi nguyên tử đúng một lần (Idempotent & Atomic).**

---

## 2. Chi tiết các hạng mục đã hoàn thành theo 7 Task

### Task 1 — Xác minh Odoo trước khi cấp quyền (Gateway)
- **Tạo hàm `validate_user_account` dùng chung:** Dùng credential của chính người dùng đọc lại `res.users` để xác minh tài khoản đang hoạt động (`active=True`) và là tài khoản nội bộ (`share=False`).
- **Từ chối cấp quyền khi credential bị thu hồi hoặc tài khoản bị khóa/external:** Chặn cả cấp identity và thực thi actions, yêu cầu người dùng xử lý lại trên Cổng Portal.
- **Xử lý Odoo timeout/mất kết nối:** Trả mã lỗi HTTP 503 (`ODOO_UNAVAILABLE`); tuyệt đối không trả identity giả mạo `OK` hoặc âm thầm cấp quyền khi chưa xác minh được với Odoo.
- **Phân biệt AccessError `hr.employee`:** Lỗi thiếu quyền đọc hồ sơ nhân viên chỉ làm trống chức danh/phòng ban (`job_title=None`, `department=None`), không đánh đồng thành lỗi xác thực danh tính.
- **Tính toán quyền CRM độc lập:** Quyền `crm.read`, `crm.create`, `crm.write` được thẩm định riêng biệt từng thao tác.
- **Kiểm tra Google OAuth Scopes chính xác:** `calendar.events.readonly` không cấp quyền ghi lịch; `gmail.send` không cấp quyền đọc thư.
- **Kiểm thử đạt:** 11/11 tests trong `tests/unit/test_odoo_account_verification.py`.

### Task 2 — Làm xác nhận và thao tác local nguyên tử (Agent)
- **Vòng đời trạng thái xác nhận chuẩn:** Chuyển đổi từ cơ chế boolean sang lifecycle: `pending ➔ executing ➔ completed / failed / unknown_result` (giữ `cancelled`, `expired`).
- **Atomic Claim bằng Conditional Update:** Áp dụng câu lệnh SQL có điều kiện `status = 'pending'` và kiểm tra `rowcount == 1`. Chỉ duy nhất 1 tiến trình claim thành công mới được quyền thực thi; tiến trình thứ hai lập tức bị từ chối.
- **Cố định `operation_id`:** Sinh và lưu trữ `operation_id` ngay từ khi tạo preview; mọi lần xác nhận hoặc retry đều dùng cùng một ID này.
- **Ràng buộc xác nhận chặt chẽ:** Gắn confirmation với `user_id`, `owner_key`, `chat_id`, `telegram_binding_id` và payload đóng băng.
- **Transaction nguyên tử cho Local Tasks:** Hợp nhất lưu trữ confirmations và task ledger vào cùng SQLite database (`tasks.db`); cập nhật thay đổi task và đánh dấu hoàn thành confirmation trong cùng một transaction.
- **Migration an toàn:** Bản ghi `confirmed` cũ không có bằng chứng kết quả chuyển thành `unknown_result`, tuyệt đối không tự ý thực thi lại.
- **Kiểm thử đạt:** 5/5 tests trong `tests/unit/test_atomic_confirmations.py`.

### Task 3 — Hoàn thiện Idempotency giữa hai dịch vụ
- **Xử lý cạnh tranh tại Gateway:** Bắt xung đột khóa `IntegrityError` khi insert `ActionOperation`, rollback và đọc lại bản ghi đã tồn tại; loại bỏ hoàn toàn lỗi HTTP 500 do race condition.
- **Theo dõi thao tác đang chạy tại Gateway:** Thiết lập timeout theo dõi 30 giây; nếu hết hạn chưa có kết quả thì chuyển sang `unknown_result`, không tự ý gọi lại dịch vụ ngoài để tránh trùng lặp email/lịch.
- **Đồng bộ Turn Ledger tại Agent:** Cập nhật `TurnLedger` thêm `telegram_binding_id` và `payload_hash`; từ chối request cùng ID nhưng khác payload (HTTP 409 Conflict) hoặc khác danh tính (HTTP 403 Forbidden).
- **Hỗ trợ Gateway Retry:** Trả HTTP 503 khi turn đang được xử lý (`in_progress`) để Gateway retry theo cơ chế có sẵn.
- **Không cache lỗi tạm thời:** Khi gặp lỗi kết nối tạm thời (`GatewayServiceError`), gọi `clear_turn` để lượt kế tiếp có thể thử lại, không biến lỗi tạm thành kết quả vĩnh viễn.
- **Kiểm thử đạt:** Tests idempotency trên cả Gateway và Agent đều PASS 100%.

### Task 4 — Sửa định tuyến và phân loại lệnh
- **Phân định rõ ràng tại Gateway:** Giữ `/crm` và `/crm <từ khóa>` cũ cho Gateway xử lý tra cứu nhanh; chuyển các lệnh ghi/sửa chuyên sâu `/crm search`, `/crm create`, `/crm update` sang Agent.
- **Ưu tiên lệnh ghi trước từ khóa đọc tại Agent:** Lệnh `/confirm`, `/cancel` và các lệnh ghi dữ liệu cụ thể (`_match_task_*`, `_match_calendar_*`, `_match_email_send`, `_match_crm_*`) được kiểm tra TRƯỚC các từ khóa đọc.
- **Làm sạch bộ nhận diện từ khóa rộng:** Loại bỏ từ khóa đơn lẻ `"calendar"`, `"gmail"` khỏi bộ nhận diện đọc thư/lịch để tránh trường hợp tiêu đề email hay nội dung công việc vô tình làm thay đổi ý định của người dùng.
- **Bổ sung đầy đủ cho `calendar.update`:** Hoàn thiện đường chuẩn bị preview và xử lý kết quả cho `calendar.update`.
- **Kiểm thử đạt:** Tests định tuyến Gateway (`test_task4_gateway_routing.py`) và Agent (`test_task4_agent_routing.py`) đều PASS.

### Task 5 — Sửa kết quả Calendar, Gmail và kiểm tra dữ liệu đích
- **Thống nhất Contract Lịch:** Gateway trả về danh sách `events` với `start` và `end` dạng chuỗi và cờ `is_all_day`. Agent cập nhật cả provider và formatter tương thích contract này.
- **Chống báo thành công giả cho Gmail & Calendar:**
  - Gmail chỉ đánh dấu `completed` khi Google trả về `id` (Message ID).
  - Lỗi Google `PERMISSION_DENIED` (HTTP 403) được Gateway truyền nguyên trạng sang Agent, không bọc thành công.
  - Calendar và CRM bắt buộc phải có ID bản ghi trả về mới coi là thành công.
- **Bổ sung API tra cứu chi tiết:**
  - `GET /internal/v1/users/{user_id}/calendar/events/{event_id}` (lấy chi tiết sự kiện kèm `etag`).
  - `GET /internal/v1/users/{user_id}/crm/leads/{lead_id}` (lấy chi tiết lead kèm `write_date`).
- **So sánh Snapshot và phát hiện xung đột trước khi ghi:**
  - Task cá nhân: so sánh `updated_at`, `title`, `status`, `priority`, `due_at`. Nếu bị thay đổi sau preview, hủy bỏ thao tác và thông báo cho người dùng.
  - Calendar: truyền `etag` và phát hiện xung đột Google (HTTP 412/409 Precondition Failed ➔ status `CONFLICT`).
  - CRM: truyền `expected_write_date`, nếu Odoo `write_date` không khớp ➔ status `CONFLICT`.
- **Giao diện Preview phong phú:** Hiển thị tài khoản Google đang kết nối, thời gian/múi giờ, người nhận thư và bảng so sánh trước–sau (Old ➔ New).
- **Kiểm thử đạt:** `test_task5_gateway_contract_and_checks.py` (5/5 PASS) và `test_task5_agent_calendar_snapshot.py` (5/5 PASS).

### Task 6 — Hoàn thiện hội thoại trong phạm vi đã mở
- **Ưu tiên Slash Commands xác định:** Các lệnh gõ trực tiếp luôn được xử lý trước bằng parser xác định.
- **Tích hợp LLM phân tích ngôn ngữ tự nhiên:**
  - Khi người dùng gửi câu tự nhiên, hệ thống dùng LLM (Gemini) để phân tích thành schema action có cấu trúc.
  - Danh mục công cụ cung cấp cho LLM được giới hạn chặt chẽ theo đúng các quyền người dùng đã được cấp trong `context.permissions`.
  - Mọi action ghi do LLM đề xuất bắt buộc phải qua server-side validation và trả về bản xem trước kèm `/confirm <id>`, không tự ý thực thi trực tiếp.
  - Giới hạn mỗi xác nhận ở một hành động ghi duy nhất; không thực thi batch.
- **Xử lý tham chiếu ngữ cảnh:**
  - "Gửi bản vừa soạn": truy xuất `last_draft_email` từ `conversation_state_manager` để tạo preview gửi thư.
  - "Sửa giờ cuộc họp đó": truy xuất `recent_calendar_events`. Nếu có nhiều cuộc họp, bot sẽ hỏi người dùng chọn cuộc họp cụ thể.
- **Hoàn thiện luồng bổ sung thông tin Calendar:** Xử lý `create_calendar` trong `_handle_multi_turn` khi người dùng nhập thiếu thời gian.
- **Cơ chế Fallback an toàn:** Khi LLM thiếu API key hoặc lỗi kết nối, slash commands vẫn hoạt động bình thường; câu tự nhiên không hiểu sẽ thông báo rõ ràng, tuyệt đối không đoán dữ liệu để ghi.
- **Kiểm thử đạt:** 5/5 tests trong `test_task6_conversation_and_llm.py`.

### Task 7 — Chuẩn hóa cấu hình và hướng dẫn vận hành
- **Chuẩn hóa cấu hình cổng:**
  - Auth Gateway chạy trên port `8080` (`PUBLIC_BASE_URL=http://localhost:8080`).
  - AI Agent chạy trên port `8000` (`AI_AGENT_BASE_URL=http://localhost:8000`).
- **Phân tách 2 Service Secret:**
  - `GATEWAY_TO_AGENT_SECRET` (Gateway ➔ Agent).
  - `AGENT_TO_GATEWAY_SECRET` (Agent ➔ Gateway).
  - Hai secret bắt buộc khác nhau, độ dài tối thiểu 32 ký tự và khớp chính xác giữa hai file `.env`.
- **Cho phép Gmail cá nhân:**
  - Để trống `GOOGLE_WORKSPACE_DOMAINS=` trong `.env.example` và cấu hình triển khai để cho phép mọi tài khoản Google cá nhân.
- **Bảo toàn OAuth State/Nonce & Cấp bổ sung scope:**
  - OAuth flow sử dụng PKCE và nonce, yêu cầu `prompt=consent` để cấp bổ sung scope `gmail.send` khi người dùng kết nối lại.
- **Độc quyền thu Telegram:**
  - Gateway là nơi duy nhất mở Telegram webhook hoặc chạy poller.
  - Agent tự động vô hiệu hóa direct webhook và chặn script poller khi `INTEGRATION_MODE=gateway`.
- **Docker Compose:**
  - Gateway expose và map port `8080:8000`.
  - Agent chia sẻ mạng chung `auth_gateway_net` để gọi nội bộ qua service name.
- **Bảo vệ Secret:** Không commit bất kỳ mật khẩu, token hay file `.env` nào lên git.

### 2.8. Hoàn tất 4 lỗi chặn nghiệp vụ trước khi cập nhật GitHub
1. **Sửa API đọc chi tiết Calendar/CRM (Gateway):** Khắc phục lỗi hai endpoint `GET /internal/v1/users/{id}/calendar/events/{event_id}` và `GET /internal/v1/users/{id}/crm/leads/{lead_id}` gọi sai chữ ký hàm xác thực; giải mã credential qua Fernet key, kiểm tra quyền `calendar.read` / `crm.read`, phân loại chuẩn xác 404 (không tồn tại), 403 (khóa/thiếu quyền), 503 (lỗi mạng Odoo).
2. **Xác minh danh tính trước khi đọc cache turn (Agent & Gateway Worker):** Agent resolve identity từ Gateway trước khi truy cập cache hoặc claim turn; đối chiếu `telegram_binding_id`, từ chối replay các bản ghi cũ thiếu binding/hash, và trả HTTP 503 để Gateway worker retry khi lỗi mạng tạm thời.
3. **Thống nhất kết quả CRM (Agent & Gateway):** Contract Gateway trả ID bản ghi trực tiếp trong `result["id"]`; Formatter Agent đọc trực tiếp `result.get("id")`, hiển thị chuẩn xác ID và chỉ hiển thị doanh thu khi có dữ liệu thật.
4. **Chuẩn hóa trạng thái chưa rõ kết quả (Agent):** Chuẩn hóa `UNKNOWN_RESULT` / timeout thành `"unknown_result"`, ngăn chặn exception handler ngoài ghi đè thành `failed`, giữ nguyên `operation_id` cố định từ preview và chặn thực thi nếu thiếu ID để ngăn chặn duplicate execution.
5. **Cập nhật Google Calendar an toàn (Gateway):** Chuyển từ `PUT` sang `PATCH` kèm header `If-Match: etag` để ngăn chặn việc xóa mất các trường không sửa trong sự kiện.

---


## 3. Kết quả kiểm thử toàn diện (Regression Test Results)

Hệ thống đã chạy toàn bộ regression test suite trên cả hai repository với tỷ lệ đạt **100%**:

| Repository | Test Suite | Số test | Kết quả | Thời gian |
|---|---|:---:|:---:|:---:|
| **Auth Gateway** | Toàn bộ Unit & Integration tests (`tests/`) | **134** | **134 PASSED** (0 failed) | 65.54s |
| **Module AI - Agent** | Toàn bộ Unit, Workflow & Integration tests (`tests/`) | **222** | **221 PASSED, 1 skipped** (0 failed) | 9.50s |
| **TỔNG HỢP** | **Toàn bộ hệ thống Gateway–Agent** | **356** | **355 PASSED (100%)** | ~75s |

---

## 4. Hướng dẫn khởi động môi trường Local

### 4.1. Khởi động Auth Gateway (Port 8080)
```powershell
cd D:\ODOO\Auth_gateway\auth-gateway
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```
*Healthcheck: `http://localhost:8080/healthz`*

### 4.2. Khởi động AI Agent (Port 8000)
```powershell
cd "D:\ODOO\Module AI - Agent"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
*Healthcheck: `http://localhost:8000/api/v1/health`*

---

## 5. Các điểm lưu ý khi đưa vào vận hành thật

1. **Phần chưa kiểm chứng bằng tài khoản thật:**
   - **Google Cloud Console:** Cần cấu hình OAuth 2.0 Web Client ID thật trên Google Cloud Console với redirect URI `http://localhost:8080/oauth/google/callback` (hoặc URL public qua Cloudflare Tunnel), đồng thời kích hoạt các API: Google Calendar API, Gmail API và bổ sung scope `https://www.googleapis.com/auth/gmail.send`.
   - **Odoo Online Enterprise SaaS:** Cần tài khoản Odoo có quyền nhân viên hợp lệ (`active=True`, `share=False`) và API key cá nhân để kiểm thử với database sống.
2. **Khuyến nghị an toàn:**
   - Khi đưa lên production, luôn bật Cloudflare Tunnel hoặc reverse proxy HTTPS cho URL công khai của Gateway.
   - Luôn sử dụng Telegram webhook secret token (`X-Telegram-Bot-Api-Secret-Token`).
   - Giữ nguyên cơ chế kiểm tra `rowcount == 1` và conditional updates trong SQLite để ngăn ngừa triệt để các hành vi thực thi trùng lặp.
