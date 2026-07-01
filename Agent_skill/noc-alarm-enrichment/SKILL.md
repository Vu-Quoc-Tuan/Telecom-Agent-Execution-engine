---
name: noc-alarm-enrichment
description: >-
  Điều tra và làm giàu (enrich) cảnh báo NOC theo quy trình 3 bước: (1) lấy cảnh báo theo
  MỘT loại alarm cụ thể từ ClickHouse trong một cửa sổ thời gian, (2) chạy hàm trích xuất
  entity (IP, tên NE, interface, cell/eNB/gNB id, AS, VLAN, key=value) từ trường content,
  (3) dùng các entity đó tra cứu một bảng khác trong DB (vd ne_inventory/topology) để lấy
  site_id, segment, vendor, đội trực... Dùng skill này BẤT CỨ KHI NÀO người dùng muốn
  soi/điều tra/đào sâu một loại cảnh báo, "lọc cảnh báo theo type", bóc thông tin trong
  content cảnh báo, map cảnh báo sang site/thiết bị/topology, hoặc enrich alarm từ
  core_alarm_history / chaining_summary — kể cả khi họ không nói thẳng chữ "enrichment".
  Trigger cho cả tiếng Việt lẫn tiếng Anh (alarm, alert, NOC, cảnh báo, enrich, correlation).
---

# NOC Alarm Enrichment

Skill này thực hiện một pipeline điều tra cảnh báo NOC quen thuộc: **lọc theo loại → bóc
thông tin trong content → tra bảng tham chiếu để biết cảnh báo này thuộc về đâu**. Mục tiêu
là biến một đống cảnh báo free-text thành bảng có cấu trúc, gắn được vào site/topology để
phục vụ điều tra và correlation.

Pipeline gồm 3 bước, chạy tuần tự, mỗi bước feed bước sau:

```
[Bước 1] ClickHouse: lấy cảnh báo theo alarm_type + cửa sổ thời gian
            │  (alarm_id, content, ne_name, ...)
            ▼
[Bước 2] extract_content.py: bóc entity từ content
            │  (ips, ne_names, interfaces, cell_ids, as_numbers, vlans, kv) + lookup_keys
            ▼
[Bước 3] PostgreSQL: tra ne_inventory bằng lookup_keys
            │  (site_id, segment, vendor, oncall_team, ...)
            ▼
        Cảnh báo đã enrich (map ngược về từng alarm_id)
```

## Tham số cần làm rõ với người dùng

Trước khi chạy, xác định (hỏi nếu thiếu, nhưng đừng hỏi cái đã suy ra được):

1. **alarm_type** — loại cảnh báo cần điều tra (vd `LINK_DOWN`, `BGP_SESSION_DOWN`). Bắt buộc.
2. **window_min** — cửa sổ thời gian tính bằng phút. Mặc định `10` (khớp ngưỡng tương quan 10 phút).
3. **Bảng/cột thật** — tên bảng cảnh báo (mặc định `core_alarm_history`) và bảng tham chiếu
   (mặc định `ne_inventory`), cùng tên cột. Đây là thứ HAY khác nhau giữa các môi trường.
4. **Khoá lookup** — field nào trong kết quả bước 2 dùng để tra bước 3. Mặc định `ips,ne_names`.
5. **"Lấy ra cái gì"** ở bước 3 — các cột muốn enrich (site_id, segment, vendor, oncall_team...).

## Cách chạy

Hai script nằm trong `scripts/`. Có thể chạy thủ công từng bước hoặc dùng orchestrator.

### Bước 2 — hàm trích xuất (chạy độc lập được)

`scripts/extract_content.py` chỉ dùng thư viện chuẩn, luôn chạy được.

```bash
# Một chuỗi content
python3 scripts/extract_content.py --text "<nội dung cảnh báo>"

# Batch: file JSONL, mỗi dòng 1 cảnh báo có trường content
python3 scripts/extract_content.py --input alarms.jsonl --content-field content
```

Output (chế độ batch) là JSONL: mỗi bản ghi gốc được thêm `extracted` và `lookup_keys`.
Danh sách khoá gộp của cả batch in ra **stderr** dạng `LOOKUP_KEYS=[...]` để copy nhanh sang bước 3.

Mở rộng/ghi đè pattern mà KHÔNG sửa code: tạo file JSON rồi truyền `--patterns`:
```json
{ "circuit_id": { "regex": "(?i)\\bcircuit[ :=#-]*([A-Z0-9-]+)", "group": 1 } }
```
`group: 0` = lấy toàn bộ khớp; `group: 1` = lấy nhóm bắt. Field trùng tên sẽ ghi đè mặc định.

### Cả pipeline — orchestrator

`scripts/run_enrichment.py` nối cả 3 bước. **Xem SQL trước khi bắn thật** bằng `--dry-run`
(không cần kết nối DB, không cần cài lib):

```bash
python3 scripts/run_enrichment.py --alarm-type LINK_DOWN --window-min 10 --dry-run
```

Chạy thật cần đặt biến môi trường và cài driver (chi tiết trong đầu file script):
```bash
export CH_HOST=... CH_PORT=8123 CH_USER=... CH_PASSWORD=... CH_DATABASE=...
export PG_DSN="postgresql://user:pass@host:5432/dbname"
pip install clickhouse-connect psycopg2-binary
python3 scripts/run_enrichment.py --alarm-type LINK_DOWN --window-min 10 --limit 500
```

Câu lệnh SQL của bước 1 và bước 3 nằm trong chính script (`STEP1_SQL`, `STEP3_SQL`) và được
giải thích kỹ trong `references/sql_templates.md` — **đọc file đó khi cần chỉnh tên bảng/cột,
đổi sang `LIMIT 1 BY`, lookup theo CIDR, hoặc xử lý bind mảng `text[]`/`jsonb` trong Java/Spring.**

## Định dạng output

Trả kết quả cuối cho người dùng dưới dạng JSON (một object cho mỗi cảnh báo), gồm:

- Các trường gốc từ bước 1: `alarm_id`, `content`, `ne_name`, `severity`, `last_seen`.
- `extracted`: dict các entity bóc được ở bước 2.
- `lookup_keys`: danh sách khoá đã dùng để tra bước 3.
- `enrichment`: danh sách bản ghi tham chiếu khớp được (site_id, segment, vendor, oncall_team...).

Khi tóm tắt cho người dùng, ngoài JSON hãy nêu ngắn gọn các phát hiện đáng chú ý: ví dụ
nhiều cảnh báo cùng trỏ về một `site_id`, hay một `oncall_team` đang ôm phần lớn cảnh báo —
đó thường là tín hiệu cho bước correlation tiếp theo.

## Ví dụ

**Input (yêu cầu người dùng):**
> "Soi giúp tôi cảnh báo LINK_DOWN trong 10 phút qua, xem chúng rơi vào site/đội nào."

**Cách skill xử lý:**
1. Bước 1: query `core_alarm_history` với `alarm_type='LINK_DOWN'`, `event_time >= now()-10m`.
2. Bước 2: với mỗi `content`, chạy `extract_content` → thu `ips` + `ne_names` làm `lookup_keys`.
3. Bước 3: query `ne_inventory WHERE ip = ANY(keys) OR ne_name = ANY(keys)` → lấy `site_id`,
   `segment`, `oncall_team`.
4. Map ngược `enrichment` về từng cảnh báo, in JSON + tóm tắt site/đội nổi bật.

**Một dòng content và entity bóc được:**
Input: `Interface GigabitEthernet0/0/1 on NE HNI-CORE-01 (10.211.140.16) is DOWN; peer AS65001; cell HNI_0231`
Output: `ips=["10.211.140.16"]`, `ne_names=["HNI-CORE-01"]`, `interfaces=["GigabitEthernet0/0/1"]`,
`cell_ids=["HNI_0231"]`, `as_numbers=["65001"]`, `lookup_keys=["10.211.140.16","HNI-CORE-01"]`.

## Lưu ý & xử lý sự cố

- **Bước 3 không khớp gì** thường do (a) định dạng tên NE trong content khác trong inventory
  (vd có hậu tố domain, viết hoa/thường khác), hoặc (b) pattern `ne_names` bỏ sót dạng tên lạ.
  Cách xử lý: thêm pattern qua `--patterns`, hoặc đổi `--key-fields` sang chỉ `ips` nếu IP đáng tin hơn.
- **Pattern `ne_names` mặc định** yêu cầu ≥3 đoạn (REGION-ROLE-INDEX) để giảm nhiễu và đã loại
  các token kiểu `AS-xxxx`/`VLAN-xxxx`. Nếu hệ thống dùng tên 2 đoạn (vd `CORE-01`), hãy nới
  pattern qua `--patterns`.
- **Luôn `--dry-run` trước** khi chạy thật để soát lại SQL và tên bảng/cột — đây là chỗ dễ sai nhất.
- **Đừng nối chuỗi vào SQL.** Bước 1 dùng parameterized query của ClickHouse; bước 3 bind mảng
  qua `ANY(...)`. Khi port sang Java/Spring, dùng `createArrayOf("text", ...)` cho `text[]` và
  `PGobject` cho `jsonb` (xem `references/sql_templates.md`).
- **Mở rộng sang cross-segment correlation:** sau khi có `site_id`, nhóm cảnh báo theo
  `(site_id, time_bucket)` để bắt nhiều segment cùng kêu tại một site — phần này có sẵn truy vấn
  mẫu ở cuối `references/sql_templates.md`.
