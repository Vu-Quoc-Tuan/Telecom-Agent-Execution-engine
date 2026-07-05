---
name: test-unstable-skill
description: >-
  Một skill viễn thông giả lập việc thu thập KPI từ node và gửi alarm lên hệ thống NOC.
  Được thiết kế không ổn định để kiểm thử pipeline xử lý sự cố.
metadata:
  version: 1.0.0
---

# Test Unstable Skill

Skill này thực hiện giám sát các chỉ số mạng viễn thông.
Nó được thiết kế có chủ ý với một script bị lỗi để kiểm thử quy trình xác thực (validation/smoke test) trong sandbox của hệ thống.

## Các tính năng
- Thu thập KPI của node mạng viễn thông
- Gửi alarm cảnh báo lên hệ thống NOC

## Cách chạy thử nghiệm
```bash
python3 scripts/unstable_script.py
```
