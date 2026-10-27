# Tích hợp Odoo Online

Workspace này phát triển External Auth Gateway kết nối Odoo Online với Google Workspace và Telegram. Không cần mã nguồn, addon, PostgreSQL hay môi trường Odoo local.

## Bắt đầu

- [Dự án gateway](auth-gateway/README.md): cài đặt và chạy gateway trên máy hoặc bằng Docker.
- [Cấu hình tích hợp](auth-gateway/docs/SETUP.md): tenant Odoo Online, API Key cá nhân, Google OAuth và Telegram.
- [Vận hành và sao lưu](auth-gateway/docs/OPERATIONS.md).
- [Kết quả nghiệm thu hiện có](auth-gateway/docs/ACCEPTANCE.md): cần đối chiếu lại trước khi triển khai chính thức.

Trong `auth-gateway/.env`, đặt `ODOO_URL` bằng URL HTTPS của tenant Online và `ODOO_DB` bằng tên database tương ứng. Giữ nguyên `ENCRYPTION_KEY` khi dùng dữ liệu đã có.

Gateway có thể chạy trên máy của bạn; Odoo đích chạy trên Online. Docker Compose của gateway chỉ gồm gateway và Cloudflare Tunnel.

## Dữ liệu cần giữ

`auth-gateway/.env`, các tệp SQLite và volume `auth_gateway_data` chứa cấu hình hoặc dữ liệu vận hành. Không xóa hay đưa chúng vào Git. Hiện có database ở cả gốc gateway và `data/`; đường dẫn trong `DATABASE_URL` quyết định database được sử dụng.

## Trạng thái kiểm thử và tích hợp

- Toàn bộ **134 test** tự động của Gateway (unit test, contract v2, worker idempotency, sweep sending, action execution và integration cross-app) đều đã chạy thành công (`pytest -v`).
- API chi tiết Calendar và CRM đã được khắc phục đúng chữ ký xác thực Odoo, kiểm tra quyền và hỗ trợ ETag/write_date.
- Hàng đợi Telegram và AI Agent forwarding hỗ trợ xử lý luồng hai chiều an toàn, đối chiếu `telegram_binding_id` hiện hành.

## Giới hạn đã biết và lưu ý vận hành

Trước khi đưa vào môi trường sản xuất với dữ liệu quy mô lớn, cần lưu ý:
- Phân trang Calendar/Gmail quy mô lớn và đồng bộ nền hai chiều cần kiểm chứng riêng với từng tenant Odoo Online.
- Bảo đảm hai secret giao tiếp giữa Gateway và AI Agent được cấu hình khác nhau, độ dài tối thiểu 32 ký tự, và volume lưu trữ SQLite được gắn bền vững.
