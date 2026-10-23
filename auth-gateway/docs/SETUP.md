# Hướng Dẫn Cấu Hình Hệ Thống — External Auth Gateway

Tài liệu hướng dẫn từng bước chuẩn bị và cấu hình tích hợp giữa Odoo Online Enterprise 19.3, Google Workspace, Telegram Bot và Cloudflare Tunnel.

---

## 1. Cấu hình Odoo Online Enterprise 19.3

Hệ thống kết nối trực tiếp qua chuẩn XML-RPC của Odoo (`common` và `object/execute_kw`). **Tuyệt đối không cần cài đặt bất kỳ addon hay chỉnh sửa code trên Odoo.**

### 1.1. Lấy thông tin cơ bản
- **Odoo URL**: Địa chỉ tenant HTTPS của công ty, ví dụ: `https://mycompany.odoo.com`.
- **Database Name**: Tên database Odoo Online (thường trùng với subdomain tenant, ví dụ: `mycompany`).

### 1.2. Tạo API Key cá nhân cho nhân viên
1. Đăng nhập vào Odoo bằng tài khoản nhân viên.
2. Nhấp vào ảnh đại diện góc trên bên phải &rarr; chọn **Preferences** (hoặc **Tùy chọn**).
3. Chuyển sang tab **Account Security** (Bảo mật tài khoản).
4. Tìm mục **Developer API Keys** &rarr; nhấp vào **New API Key**.
5. Đặt tên (ví dụ: `External Gateway`), nhập lại mật khẩu tài khoản và sao chép mã khóa được sinh ra.
6. Sử dụng API Key này để đăng nhập vào External Auth Gateway.

---

## 2. Cấu hình Google Cloud Console (Google Workspace OAuth)

### 2.1. Tạo dự án & Cấu hình OAuth Consent Screen
1. Truy cập [Google Cloud Console](https://console.cloud.google.com).
2. Tạo dự án mới hoặc chọn dự án Google Workspace của công ty.
3. Vào **APIs & Services** &rarr; **OAuth consent screen**:
   - Chọn loại User Type: **Internal** (chỉ cho phép nhân viên thuộc Workspace công ty đăng nhập).
   - Điền App Name: `Company Auth Gateway`.
   - Support email & Developer email: Email quản trị của công ty.
4. Thêm các Scopes sau:
   - `openid`
   - `email`
   - `https://www.googleapis.com/auth/calendar.events` (Đọc/ghi sự kiện lịch)
   - `https://www.googleapis.com/auth/drive.file` (Tạo file Drive do app sinh ra)
   - `https://www.googleapis.com/auth/spreadsheets` (Đọc/ghi bảng tính)

### 2.2. Tạo OAuth Client ID
1. Vào **APIs & Services** &rarr; **Credentials** &rarr; **Create Credentials** &rarr; **OAuth Client ID**.
2. Loại ứng dụng: **Web application**.
3. **Authorized redirect URIs**:
   - `https://your-public-domain.com/oauth/google/callback`
   *(Thay `your-public-domain.com` bằng tên miền public cấu hình qua Cloudflare Tunnel).*
4. Lưu lại:
   - `Client ID` &rarr; gán vào biến môi trường `GOOGLE_CLIENT_ID`.
   - `Client Secret` &rarr; gán vào biến môi trường `GOOGLE_CLIENT_SECRET`.
5. Đặt danh sách domain được phép trong `GOOGLE_WORKSPACE_DOMAINS` (ví dụ: `company.com`).

---

## 3. Cấu hình Telegram Bot & Webhook

### 3.1. Tạo Bot mới
1. Mở Telegram, tìm kiếm bot chính thức `@BotFather`.
2. Gửi lệnh `/newbot`, đặt tên hiển thị và username kết thúc bằng `bot` (ví dụ: `CompanySyncBot`).
3. `@BotFather` sẽ trả về mã **HTTP API Token** (dạng `123456789:ABCdefGHIjklMNO...`).
4. Gán token vào biến `TELEGRAM_BOT_TOKEN` và username vào `TELEGRAM_BOT_USERNAME`.

### 3.2. Cấu hình Webhook Secret & Đăng ký Webhook
Tạo một chuỗi ngẫu nhiên bảo mật cao làm `TELEGRAM_WEBHOOK_SECRET` (ví dụ: dùng `openssl rand -hex 24`).

Chạy lệnh đăng ký webhook độc lập (chỉ chạy một lần, ứng dụng Gateway không tự ý ghi đè):

```bash
curl -F "url=https://your-public-domain.com/webhooks/telegram" \
     -F "secret_token=YOUR_TELEGRAM_WEBHOOK_SECRET" \
     -F "allowed_updates=[\"message\"]" \
     "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook"
```

Kiểm tra trạng thái webhook:
```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

---

## 4. Cấu hình Cloudflare Tunnel (Named Tunnel)

Đưa Gateway ra ngoài Internet an toàn qua HTTPS mà không cần mở port router.

1. Đăng nhập [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com).
2. Vào **Networks** &rarr; **Tunnels** &rarr; nhấp **Create a tunnel**.
3. Đặt tên tunnel (ví dụ: `auth-gateway-tunnel`).
4. Sao chép `TUNNEL_TOKEN` và đưa vào biến môi trường `CLOUDFLARE_TUNNEL_TOKEN` trong `.env`.
5. Trong cấu hình **Public Hostname**:
   - Subdomain: `gateway` (hoặc domain bạn chọn).
   - Service Type: `HTTP`.
   - URL: `gateway:8000` (tên service trong `compose.yaml`).

---

## 5. Tạo Khóa Mã Hóa Fernet

Chạy Python một dòng để tạo khóa bảo mật 32-byte Fernet base64:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Sao chép chuỗi kết quả và lưu vào biến `ENCRYPTION_KEY` trong `.env`. **Giữ khóa này cẩn thận và sao lưu an toàn! Mất khóa này sẽ không thể giải mã các credential và token đã lưu.**
