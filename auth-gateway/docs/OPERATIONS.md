# Hướng Dẫn Vận Hành & Xử Lý Sự Cố — External Auth Gateway

Tài liệu dành cho kỹ sư vận hành hệ thống External Auth Gateway.

---

## 1. Quản lý Bí mật & Dữ liệu

### 1.1. Khóa mã hóa `ENCRYPTION_KEY`
- Sử dụng thuật toán AES-128 CBC thông qua thư viện `cryptography.fernet.Fernet`.
- Dùng để mã hóa đối xứng:
  - Mật khẩu / API Key cá nhân của nhân viên Odoo.
  - Google OAuth Access Token và Refresh Token.
  - PKCE Code Verifier tạm thời.
- **Nguyên tắc bất di bất dịch**:
  - Không sinh khóa mới khi khởi động lại ứng dụng.
  - Khóa phải được lưu riêng biệt (biến môi trường hoặc secret manager), không lưu trong SQLite database hay trong image Docker.
  - Phải sao lưu khóa ở nơi an toàn. Nếu mất khóa, toàn bộ token và credential đã lưu sẽ không thể giải mã và tất cả nhân viên phải đăng nhập/kết nối lại từ đầu.

### 1.2. SQLite Database & Volume
- Database được lưu tại `/data/gateway.db` trong volume Docker `auth_gateway_data`.
- Chế độ WAL (Write-Ahead Logging) được bật tự động, `busy_timeout=10000ms`, `foreign_keys=ON`.

---

## 2. Quy trình Sao lưu & Phục hồi (Backup & Restore)

### 2.1. Sao lưu tự động / thủ công (Hot Online Backup)
Dự án cung cấp sẵn script `scripts/backup.py` sử dụng SQLite Online Backup API. Script này tạo bản snapshot nhất quán ngay cả khi hệ thống đang xử lý request và ghi log WAL:

```bash
# Chạy backup từ host hoặc container:
docker exec auth-gateway python scripts/backup.py --source /data/gateway.db --dest /data/backups

# Hoặc chạy script từ máy chủ có mount volume:
python scripts/backup.py --source ./data/gateway.db --dest ./backups
```

Tạo cronjob hàng ngày trên máy chủ để định kỳ sao lưu:
```cron
0 2 * * * docker exec auth-gateway python scripts/backup.py --dest /data/backups
```

### 2.2. Phục hồi dữ liệu (Restore)
1. Dừng container ứng dụng:
   ```bash
   docker compose stop gateway
   ```
2. Sao chép file backup đè vào file database chính:
   ```bash
   cp /data/backups/gateway_backup_YYYYMMDD_HHMMSS.db /data/gateway.db
   # Xóa các file WAL cũ nếu còn sót lại:
   rm -f /data/gateway.db-wal /data/gateway.db-shm
   ```
3. Đảm bảo file `.env` chứa đúng `ENCRYPTION_KEY` tại thời điểm tạo bản backup đó.
4. Khởi động lại container:
   ```bash
   docker compose start gateway
   ```
5. Kiểm tra endpoint `/healthz` và xem log:
   ```bash
   curl -i http://localhost:8000/healthz
   docker compose logs -n 50 gateway
   ```

---

## 3. Xử lý các tình huống sự cố thường gặp

### 3.1. API Key Odoo của nhân viên hết hạn hoặc bị thu hồi
- **Hiện tượng**: Log ghi nhận `OdooAuthError` khi đồng bộ lịch hoặc tra cứu CRM.
- **Hành vi hệ thống**: Gateway tự động dừng tác vụ đồng bộ lịch và nhắc họp của nhân viên đó.
- **Cách xử lý**: Nhân viên chỉ cần đăng nhập lại vào Cổng Portal bằng API Key mới. Credential mới sẽ tự động được mã hóa và cập nhật, các tác vụ nền sẽ khôi phục bình thường.

### 3.2. Google OAuth Token bị thu hồi (`invalid_grant`)
- **Hiện tượng**: Job refresh token trả về lỗi `invalid_grant` (do user đổi mật khẩu Google hoặc thu hồi quyền truy cập).
- **Hành vi hệ thống**: Chuyển trạng thái liên kết sang `reconnect_required`, ngừng spam retry. Trên Dashboard hiển thị badge cảnh báo "Cần kết nối lại".
- **Cách xử lý**: Nhân viên truy cập Dashboard &rarr; nhấn "Kết nối lại Google Workspace". Lưu ý: Hệ thống chỉ cho phép kết nối lại cùng một tài khoản Google (`sub` khớp) để bảo toàn mapping lịch.

### 3.3. Người dùng chặn Telegram Bot (Lỗi 403 `bot was blocked by the user`)
- **Hành vi hệ thống**: Bắt exception `BotBlockedError`, tự động đánh dấu trạng thái Telegram của user thành `blocked`, ngừng gửi các tin nhắn trong outbox để tránh lãng phí tài nguyên và không bị Telegram rate limit bot.
- **Cách xử lý**: Trên Dashboard hiển thị cảnh báo "Đã chặn Bot". Nhân viên cần mở Telegram &rarr; Unblock bot &rarr; bấm gửi lại `/start`.

### 3.4. Xung đột lịch (Conflict)
- **Hiện tượng**: Một sự kiện đã ghép đôi bị chỉnh sửa đồng thời ở cả Google Calendar và Odoo với dữ liệu nội dung khác nhau.
- **Hành vi hệ thống**: Đánh dấu cặp lịch là `conflict`, không tự ý ghi đè mù quáng. Tạm dừng đồng bộ cặp lịch đó và hiển thị danh sách trên Dashboard.
- **Cách xử lý**: Nhân viên vào Dashboard, xem mục "Xung đột lịch cần xử lý", chọn bấm "Giữ bản Odoo" hoặc "Giữ bản Google". Hệ thống sẽ đồng bộ bản được chọn sang phía còn lại và đưa trạng thái về `synced`.

### 3.5. Sự kiện Odoo chờ đối soát (Ambiguous Intent)
- **Hiện tượng**: Khi tạo lịch từ Google sang Odoo, request gặp timeout mạng hoặc container bị restart trước khi kịp nhận mã ID từ Odoo.
- **Hành vi hệ thống**: Để ngăn chặn việc gọi tạo lại làm nhân đôi lịch trên Odoo, hệ thống chuyển `SyncIntent` sang `ambiguous`.
- **Cách xử lý**: Nhân viên kiểm tra xem lịch đã xuất hiện trên Odoo chưa:
  - Nếu đã tạo: Nhập ID sự kiện Odoo đó và nhấn "Gắn kết nối".
  - Nếu chưa tạo: Bấm "Thử tạo lại".

---

## 4. Kế hoạch Rollback (Thu hồi triển khai)

Trong trường hợp cần ngừng thử nghiệm hoặc tháo gỡ Gateway:
1. Dừng và gỡ container:
   ```bash
   docker compose down
   ```
2. **Không tự ý xóa hàng loạt dữ liệu lịch**:
   - Tất cả các sự kiện đã tạo trên Google Calendar và Odoo Calendar đều là các bản ghi độc lập hợp lệ.
   - Khi Gateway dừng lại, nhân viên vẫn xem và sử dụng lịch bình thường trên cả hai hệ thống mà không bị mất mát dữ liệu.
3. Giữ nguyên volume `/data` và file `.env` nếu có kế hoạch kích hoạt lại sau đó.
