# External Auth Gateway

Dịch vụ FastAPI quản lý đăng nhập Odoo, liên kết Telegram/Google và cung cấp API nội bộ cho các ứng dụng sử dụng danh tính đã xác minh. Gateway giữ credential người dùng, gọi Odoo/Google và nhận/gửi tin Telegram.

README này hướng dẫn cài đặt, cấu hình và vận hành riêng dự án gateway. Tất cả lệnh chạy từ thư mục chứa `pyproject.toml`; không cần mã nguồn của ứng dụng khác.

> **Trạng thái:** bản phát triển có kiểm thử tự động; chưa nghiệm thu toàn bộ với tài khoản thật. Các thao tác ghi còn giới hạn được nêu cuối README.

## Chức năng và trách nhiệm

- Đăng nhập bằng tài khoản Odoo và mật khẩu hoặc API key cá nhân.
- Ghép nối Telegram qua liên kết một lần từ portal.
- Google OAuth, lưu token đã mã hóa và làm mới token.
- Identity contract v2: user ID, tenant, database, Odoo UID, chức vụ, phòng ban, quản lý, quyền và Telegram binding.
- API đọc Calendar, Gmail chưa đọc, CRM; API action tạo/sửa lịch, gửi thư, tạo/sửa lead.
- Hàng đợi Telegram, chuyển tiếp sang agent và sổ thực thi action theo operation ID.
- Các module đồng bộ lịch Odoo–Google và nhắc hẹn có sẵn; cần kiểm chứng riêng trước khi bật trên lịch thật.

Chuyển tiếp AI là tích hợp tùy chọn qua HTTP. Ứng dụng nhận yêu cầu cần tuân thủ contract API và tự quản lý luồng hội thoại, bản xem trước, xác nhận.

## Yêu cầu

- Python 3.11 trở lên; ví dụ dưới dùng Python 3.11 trên Windows PowerShell.
- Tenant Odoo cho phép truy cập API và tài khoản nhân viên có quyền phù hợp.
- Google OAuth client, các API Google cần dùng và Telegram bot token.
- Nếu bật chuyển tiếp AI, cần URL của dịch vụ nhận yêu cầu và hai secret giao tiếp được thống nhất.

Gateway dùng SQLite, không cần PostgreSQL hoặc Redis. Docker và Cloudflare Tunnel là lựa chọn triển khai bổ sung.

## Cài đặt local

Chạy từ thư mục chứa `pyproject.toml`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Bỏ qua nếu đã có .env, tránh ghi đè cấu hình hiện tại.
Copy-Item .env.example .env
```

Sinh khóa Fernet và hai secret dịch vụ, rồi lưu vào `.env`:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48)); print(secrets.token_urlsafe(48))"
```

Thay mọi giá trị minh họa trong `.env.example`. Không thay khóa mã hóa của database đang có.

## Cấu hình

| Biến | Cách sử dụng |
|---|---|
| `APP_ENV` | `development` cho local; `production` yêu cầu URL HTTPS và khóa Fernet hợp lệ |
| `PUBLIC_BASE_URL` | URL portal, local thường là `http://localhost:8080` |
| `ODOO_URL`, `ODOO_DB` | URL tenant và tên database Odoo |
| `DATABASE_URL` | Ví dụ `sqlite:///./data/gateway.db`, tính từ thư mục chạy |
| `ENCRYPTION_KEY` | Khóa Fernet mã hóa credential và OAuth token |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client của gateway |
| `GOOGLE_WORKSPACE_DOMAINS` | Để trống cho mọi tài khoản Google, gồm Gmail cá nhân; điền miền để giới hạn |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | Bot nhận/gửi tin và tạo liên kết ghép nối |
| `TELEGRAM_WEBHOOK_SECRET` | Secret kiểm tra webhook Telegram |
| `ENABLE_AI_AGENT_FORWARDING` | `true` để chuyển hội thoại sang agent; `false` để chạy gateway độc lập |
| `AI_AGENT_BASE_URL` | URL agent truy cập được từ gateway, local thường là `http://localhost:8000` |
| `GATEWAY_TO_AGENT_SECRET` | Secret chiều gateway gọi agent |
| `AGENT_TO_GATEWAY_SECRET` | Secret chiều agent gọi gateway |

Để chạy gateway độc lập, đặt `ENABLE_AI_AGENT_FORWARDING=false`. Portal, OAuth và các lệnh Telegram xử lý tại gateway vẫn có thể sử dụng mà không cần dịch vụ AI.

Nếu bật forwarding, hai secret phải khác nhau, mỗi secret ít nhất 32 ký tự và khớp với bên gọi/nhận API. Code mặc định forwarding là `false`; file mẫu có bật sẵn nên cần kiểm tra trước khi chạy.

### Google OAuth

Đăng ký redirect URI đúng bằng `<PUBLIC_BASE_URL>/oauth/google/callback`, ví dụ:

```text
http://localhost:8080/oauth/google/callback
```

Cấu hình OAuth consent và test users phù hợp với môi trường Google. Mã hiện yêu cầu OpenID/email, Calendar và Gmail đọc/gửi. Tài khoản đã liên kết trước đây có thể cần cấp quyền lại.

Khi không giới hạn miền, email Google không bắt buộc khớp email Odoo. Việc liên kết Google không cấp thêm quyền CRM hoặc chức vụ Odoo.

## Khởi động và sử dụng

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

- Portal: [http://localhost:8080/](http://localhost:8080/).
- Healthcheck: [http://localhost:8080/healthz](http://localhost:8080/healthz).

Quy trình nhân viên: đăng nhập Odoo trên portal → liên kết Telegram và bấm Start trong bot → liên kết Google. Có thể dùng `/status`, `/help`, `/crm` và `/crm <từ khóa>` trên bot. Yêu cầu AI chỉ được chuyển tiếp khi cấu hình forwarding đã bật.

Để nhận Telegram khi phát triển local, mở terminal thứ hai trong cùng thư mục:

```powershell
.\.venv\Scripts\python.exe scripts/telegram_poller.py
```

Chọn poller hoặc webhook cho cùng bot; poller cần bot không có webhook đang hoạt động. Chỉ vận hành một nguồn thu update cho bot. API và poller phải dùng cùng database/cấu hình.

## API tích hợp

API nội bộ dùng `Authorization: Bearer <AGENT_TO_GATEWAY_SECRET>`.

| Endpoint | Chức năng |
|---|---|
| `POST /internal/v1/identity/resolve` | Xác minh Telegram user/private chat; agent yêu cầu contract v2 |
| `GET /internal/v1/users/{id}/connections` | Trạng thái dịch vụ và Google scopes |
| `GET /internal/v1/users/{id}/calendar/events` | Lịch theo `time_min`, `time_max` |
| `GET /internal/v1/users/{id}/gmail/unread` | Gmail chưa đọc |
| `GET /internal/v1/users/{id}/crm/leads` | Tra cứu lead cá nhân |
| `GET /internal/v1/users/{id}/calendar/events/{event_id}` | Chi tiết sự kiện lịch (kèm ETag để bảo đảm cập nhật một phần qua PATCH) |
| `GET /internal/v1/users/{id}/crm/leads/{lead_id}` | Chi tiết cơ hội CRM (kèm `expected_write_date` chống ghi đè xung đột) |
| `POST /internal/v1/users/{id}/actions` | Action giới hạn theo quyền, binding và operation ID |

Action có trong mã: `calendar.create`, `calendar.update`, `gmail.send`, `crm.create`, `crm.update`. Agent gọi sau khi người dùng xác nhận. API không cung cấp quyền gọi tùy ý model/method Odoo.

## Docker

`compose.yaml` gồm gateway và Cloudflare Tunnel, ánh xạ gateway `8080:8000`, lưu SQLite trong volume `auth_gateway_data`.

```powershell
# Chỉ chạy gateway nếu chưa dùng Cloudflare Tunnel.
docker compose up -d --build gateway
docker compose logs -f gateway
```

Cấu hình `CLOUDFLARE_TUNNEL_TOKEN` trước khi chạy thêm service `tunnel`.

Nếu chuyển tiếp tới dịch vụ AI chạy trong container khác, đặt `AI_AGENT_BASE_URL` theo địa chỉ truy cập được từ container gateway. Có thể dùng mạng `auth_gateway_net` và tên service/alias của dịch vụ đích. `localhost` trong container chỉ trỏ về chính container; cổng nội bộ gateway là `8000`.

## Kiểm thử

```powershell
# Chạy toàn bộ test suite của Gateway (134/134 test đạt):
.\.venv\Scripts\python.exe -m pytest -v
```

Toàn bộ **134 test** tự động (bao gồm unit test, contract API, worker idempotency và integration cross-app) đều đã chạy thành công. Các test hệ thống dùng cơ sở dữ liệu tạm thời và mock ở biên Odoo/Google/Telegram.

## Giới hạn hiện tại và lưu ý vận hành

- API action trả về trạng thái `UNKNOWN_RESULT` khi gặp sự cố mạng hoặc timeout. Agent giữ nguyên `operation_id` đã lưu từ preview để đối soát, không tự động sinh lại ID mới hay thực thi lặp lại.
- Cập nhật Google Calendar sử dụng HTTP `PATCH` kèm header `If-Match: etag` để tránh mất các trường không sửa; cập nhật CRM sử dụng `expected_write_date` để phát hiện xung đột dữ liệu.
- `.env.example` cung cấp các tham số mẫu. Cần kiểm tra forwarding, secrets (tối thiểu 32 ký tự và phân biệt hai chiều), địa chỉ service và cấu hình miền Google trong môi trường triển khai thực tế.
- Healthcheck phản hồi thành công xác nhận server hoạt động, không thay thế việc kiểm tra hiệu lực liên kết OAuth hoặc tài khoản Odoo của từng cá nhân.

## Dữ liệu và tài liệu

Không commit `.env`, token, database hoặc bản sao lưu. Giữ database cùng khóa mã hóa tương ứng; không xóa volume để xử lý một lỗi kết nối.

- [Cấu hình dịch vụ](docs/SETUP.md).
- [Tích hợp AI Agent](docs/INTEGRATION_AI_AGENT.md).
- [Vận hành và sao lưu](docs/OPERATIONS.md).
- [Checklist nghiệm thu](docs/ACCEPTANCE.md).

Một số tài liệu chi tiết phản ánh giai đoạn trước; đối chiếu README và mã hiện tại khi có khác biệt.
