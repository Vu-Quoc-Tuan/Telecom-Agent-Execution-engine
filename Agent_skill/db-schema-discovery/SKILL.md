---
name: db-schema-discovery
description: >-
  Thao tác kiểm tra cấu trúc cơ sở dữ liệu (describe schema, list tables) của PostgreSQL và ClickHouse.
  Hãy dùng skill này BẤT CỨ KHI NÀO truy vấn SQL gặp lỗi "table does not exist", "column does not exist",
  hoặc khi cần khảo sát cấu trúc các bảng và cột thực tế trước khi viết câu lệnh truy vấn.
---

# Database Schema Discovery

Skill này hỗ trợ tìm hiểu cấu trúc cơ sở dữ liệu thực tế tại môi trường vận hành (môi trường staging hoặc production). Mục tiêu là giúp AI Agent tránh đoán mò tên bảng, tên cột dẫn đến lỗi cú pháp SQL.

## Các trường hợp cần sử dụng
1. Khi một câu lệnh truy vấn ClickHouse hoặc PostgreSQL trả về lỗi: `relation does not exist` hoặc `column does not exist`.
2. Khi cần biết chính xác kiểu dữ liệu của một cột để viết câu lệnh so sánh/so khớp (vd so khớp chuỗi vs mảng, epoch time vs datetime).
3. Khi bắt đầu điều tra một sự cố mới liên quan đến dữ liệu chưa từng truy vấn trước đó.

## Tham số cấu hình
Các tham số có thể cung cấp qua file hoặc trực tiếp:
* **database_type** — Loại database cần thăm dò. Nhận một trong hai giá trị: `clickhouse` hoặc `external_postgres` (Mặc định: `external_postgres`).
* **table_name** — Tên bảng cụ thể muốn kiểm tra chi tiết các cột. Nếu để trống, script sẽ trả về danh sách tất cả các bảng khả dụng.
* **dry_run** — Nếu đặt `true`, script sẽ trả về mock dữ liệu cấu trúc mẫu để kiểm tra pipeline hoạt động mà không cần kết nối thật.

## Cách chạy

```bash
# Liệt kê tất cả các bảng trong PostgreSQL
python3 scripts/discover.py --database-type external_postgres

# Xem cấu trúc chi tiết của bảng core_alarm_history trong ClickHouse
python3 scripts/discover.py --database-type clickhouse --table-name core_alarm_history
```

## Định dạng kết quả trả về
Kết quả in ra stdout là chuỗi JSON chứa danh sách các bảng hoặc thông tin cột:

### Khi liệt kê bảng
```json
[
  {
    "table_schema": "public",
    "table_name": "ne_inventory"
  },
  {
    "table_schema": "public",
    "table_name": "skills"
  }
]
```

### Khi mô tả bảng (`table_name` được cung cấp)
```json
[
  {
    "column_name": "alarm_id",
    "data_type": "character varying",
    "is_nullable": "NO"
  },
  {
    "column_name": "severity",
    "data_type": "character varying",
    "is_nullable": "YES"
  }
]
```
