# External Auth Gateway

Cổng xác thực và liên kết độc lập giữa **Odoo Online Enterprise 19.3**, **Google Workspace** và **Telegram** dành cho nhân viên công ty.

---

## 1. Giới thiệu & Nguyên tắc thiết kế

- **Độc lập hoàn toàn**: Không cài đặt addon, không tạo custom field, không can thiệp mã nguồn Odoo Online SaaS.
- **Bảo mật theo nhân viên**: Mỗi nhân viên sử dụng định danh và API Key cá nhân của chính mình. Không dùng credential quản trị chung.
- **Kiến trúc tinh gọn**: 1 FastAPI container duy nhất, SQLite WAL, không phụ thuộc Redis / Celery / PostgreSQL.
- **Tính năng chính**:
  - Đăng nhập xác thực bằng email Odoo + API Key cá nhân (hoặc mật khẩu).
  - Cổng Portal cá nhân tự kết nối / ngắt kết nối Google Workspace và Telegram Bot.
  - Tra cứu CRM lead cá nhân qua lệnh Telegram `/crm`.
  - Đồng bộ hai chiều lịch đơn giữa Google Calendar và Odoo Calendar (cửa sổ [-7 ngày, +90 ngày]).
  - Tự động nhắc hẹn họp qua Telegram trước 15 phút.
  - Cơ chế phát hiện và giải quyết xung đột lịch trực tiếp trên Portal.

---

## 2. Yêu cầu hệ thống

- Python 3.11 hoặc 3.12 (khuyên dùng 3.11/3.12).
- Docker và Docker Compose (nếu chạy container).
- Cloudflare Tunnel token (để đưa domain ra ngoài an toàn).

---

## 3. Chạy Gateway trên máy, kết nối Odoo Online

Chỉ gateway chạy trên máy của bạn; Odoo chạy trên tenant Online. Đặt `ODOO_URL` và `ODOO_DB` trong `.env` theo tenant thật. Không cần cài Odoo hay addon trên máy.

```bash
# 1. Đi vào thư mục dự án
cd auth-gateway

# 2. Tạo virtual environment và kích hoạt
python -m venv .venv
# Trên Windows PowerShell:
.venv\Scripts\Activate.ps1

# 3. Cài đặt dependencies
pip install -e ".[dev]"

# 4. Tạo file cấu hình môi trường
copy .env.example .env
# Chỉnh sửa .env với ENCRYPTION_KEY và Odoo tenant URL thật

# 5. Khởi chạy ứng dụng
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Truy cập: `http://localhost:8000` (hoặc kiểm tra healthcheck tại `http://localhost:8000/healthz`).

---

## 4. Chạy qua Docker Compose

```bash
# 1. Tạo file .env hoàn chỉnh
cp .env.example .env

# 2. Kiểm tra cấu hình compose
docker compose config

# 3. Build và khởi động dịch vụ
docker compose up -d --build

# 4. Kiểm tra logs
docker compose logs -f gateway
```

---

## 5. Kiểm thử & Định dạng mã nguồn

```bash
# Chạy toàn bộ test suite
pytest -v

# Kiểm tra linting và formatting
ruff check .
ruff format --check .

# Kiểm tra kết nối Odoo thật an toàn (không lộ secret trong terminal/args)
python scripts/check_odoo.py
```

---

## 6. Tài liệu chi tiết

- [docs/SETUP.md](docs/SETUP.md): Hướng dẫn cấu hình Odoo Online, Google OAuth, Telegram Bot và Cloudflare Tunnel.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): Vận hành, quản lý khóa bảo mật, backup/restore, xử lý sự cố.
- [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md): Bảng kiểm chứng tiêu chuẩn nghiệm thu từng hạng mục.
