#!/usr/bin/env python3
"""
scripts/check_odoo.py - Kiểm tra kết nối trực tiếp với tenant Odoo Cloud.
Bảo mật: Nhập secret qua prompt ẩn (getpass) hoặc biến môi trường ODOO_SECRET.
Tuyệt đối KHÔNG truyền secret qua command-line arguments để tránh lộ trong bash history.
"""

import getpass
import os
import sys
from datetime import UTC, datetime, timedelta

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.odoo.client import OdooClient, OdooError


def run_check():
    url = (
        os.getenv("ODOO_URL")
        or input("Nhập URL Odoo (ví dụ: https://mycompany.odoo.com): ").strip()
    )
    db = os.getenv("ODOO_DB") or input("Nhập Tên Database Odoo: ").strip()
    login = os.getenv("ODOO_LOGIN") or input("Nhập Email / Login Odoo: ").strip()

    secret = os.getenv("ODOO_SECRET")
    if not secret:
        secret = getpass.getpass("Nhập Mật khẩu hoặc API Key Odoo (ẩn khi gõ): ")

    if not url or not db or not login or not secret:
        print("[LỖI] Thiếu thông tin kết nối.")
        sys.exit(1)

    print(f"\n--- Bắt đầu kiểm tra kết nối với: {url} (DB: {db}) ---")
    client = OdooClient(url=url, db=db, timeout=20)

    # 1. Xác thực tài khoản
    print("[1/4] Đang xác thực tài khoản...")
    try:
        uid = client.authenticate(login, secret)
        print(f"  -> [THÀNH CÔNG] Xác thực thành công. UID: {uid}")
    except OdooError as e:
        print(f"  -> [THẤT BẠI] Xác thực không thành công: {e}")
        sys.exit(1)

    # 2. Đọc thông tin chính nhân viên đó
    print("[2/4] Đang đọc thông tin hồ sơ nhân viên (res.users)...")
    try:
        fields = ["id", "name", "login", "active", "share", "tz", "partner_id"]
        records = client.execute(uid, secret, "res.users", "read", [[uid]], {"fields": fields})
        if records:
            profile = records[0]
            print(
                f"  -> [THÀNH CÔNG] Tên: {profile.get('name')}, Múi giờ: {profile.get('tz')}, Is Share (Portal): {profile.get('share')}"
            )
        else:
            print("  -> [CẢNH BÁO] Không đọc được bản ghi hồ sơ.")
    except OdooError as e:
        print(f"  -> [THẤT BẠI] Lỗi đọc hồ sơ: {e}")

    # 3. Đọc CRM theo quyền
    print("[3/4] Đang kiểm tra quyền đọc CRM (crm.lead)...")
    try:
        domain = [("user_id", "=", uid), ("active", "=", True)]
        leads = client.execute(
            uid,
            secret,
            "crm.lead",
            "search_read",
            [domain],
            {"fields": ["id", "name", "stage_id"], "limit": 5},
        )
        print(f"  -> [THÀNH CÔNG] Đọc được {len(leads)} lead phụ trách.")
        for lead in leads:
            stage_name = (
                lead.get("stage_id", [False, "N/A"])[1]
                if isinstance(lead.get("stage_id"), list)
                else "N/A"
            )
            print(f"     * Lead #{lead.get('id')}: {lead.get('name')} [{stage_name}]")
    except OdooError as e:
        print(f"  -> [THẤT BẠI] Không có quyền đọc CRM hoặc CRM chưa cài đặt: {e}")

    # 4. Tạo, đọc lại và sửa một lịch thử
    print("[4/4] Đang kiểm tra quyền tạo/sửa lịch (calendar.event)...")
    test_event_id = None
    try:
        now = datetime.now(UTC)
        start_time = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        stop_time = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

        event_vals = {
            "name": f"[TEST-GATEWAY] Lịch thử nghiệm {datetime.now().strftime('%H:%M:%S')}",
            "start": start_time,
            "stop": stop_time,
            "user_id": uid,
        }
        test_event_id = client.execute(uid, secret, "calendar.event", "create", [event_vals])
        print(f"  -> [THÀNH CÔNG] Tạo lịch thử nghiệm thành công. Event ID: {test_event_id}")

        # Sửa lịch
        client.execute(
            uid,
            secret,
            "calendar.event",
            "write",
            [[test_event_id], {"name": "[TEST-GATEWAY-UPDATED] Lịch đã cập nhật"}],
        )
        print("  -> [THÀNH CÔNG] Cập nhật lịch thử nghiệm thành công.")

        # Dọn dẹp lịch test
        client.execute(uid, secret, "calendar.event", "unlink", [[test_event_id]])
        print("  -> [THÀNH CÔNG] Đã xóa lịch thử nghiệm dọn dẹp môi trường.")
    except OdooError as e:
        print(f"  -> [THẤT BẠI] Lỗi thao tác Calendar: {e}")

    print("\n--- HOÀN TẤT KIỂM TRA TẤT CẢ CÁC BƯỚC ---")


if __name__ == "__main__":
    run_check()
