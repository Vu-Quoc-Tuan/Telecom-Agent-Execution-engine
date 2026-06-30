# Security TODO — phải đóng trước khi lên staging/prod

Các lỗ hổng dưới đây được phát hiện qua code review. Đã thống nhất **hoãn** trong
giai đoạn dev, nhưng **bắt buộc** xử lý trước khi triển khai ra môi trường thật
(staging/prod) hoặc khi API tiếp xúc mạng ngoài tin cậy.

Cập nhật lần cuối: 2026-06-27

---

## 1. SSRF + đọc file tùy ý qua ClickHouse table functions (NGHIÊM TRỌNG)

- **File:** `app/agent/safety.py` — `verify_read_only_sql` (~dòng 198)
- **Vấn đề:** Validator chỉ chặn từ khóa mutation, không chặn các table function
  `url()`, `file()`, `s3()`, `remote()`, `mysql()`, `postgresql()`. ClickHouse
  `readonly=2` (trong `connectors/clickhouse.py`) **không** vô hiệu hóa các hàm này.
- **Khai thác:**
  `SELECT * FROM url('http://169.254.169.254/latest/meta-data/...', CSV)` (đánh cắp
  credential metadata cloud) hoặc `SELECT * FROM file('/etc/passwd', LineAsString)`.
- **Hướng đóng:** allowlist hàm/bảng được phép, hoặc chặn rõ danh sách table function
  nguy hiểm; cân nhắc tách network egress của ClickHouse.

## 2. Mutation lồng trong subquery vượt qua guard SQL (CAO)

- **File:** `app/agent/safety.py` — `SQL_PROHIBITED_READ_PATTERNS` (~dòng 79)
- **Vấn đề:** Guard chỉ xét `tokens[0]` + 4 regex hẹp. Mutation nằm trong subquery
  mở ngoặc lọt lưới.
- **Khai thác:** `SELECT * FROM (DELETE FROM kpi_logs RETURNING 1) AS x`,
  `SELECT * FROM foo WHERE id IN (DROP TABLE bar)`.
- **Hướng đóng:** dùng một SQL parser thực thụ (vd `sqlglot`) để duyệt AST và từ chối
  bất kỳ node mutation/DDL nào ở mọi độ sâu, thay vì regex.

## 3. Endpoint `/runs/{run_id}/interventions` không xác thực (CAO)

- **File:** `app/api/runs.py` — `queue_run_intervention` (~dòng 91)
- **Vấn đề:** Không có auth (toàn app chỉ có CORS). Nội dung gửi lên được bơm vào
  agent đang chạy dưới nhãn `[OPERATOR INTERVENTION]` (xem `agent/nodes.py`), với
  `requested_by` giả mạo được.
- **Khai thác:** kẻ tấn công POST chỉ thị giả danh operator → prompt injection vào
  agent có quyền chạy SSH/SQL.
- **Hướng đóng:** thêm auth (API key/bearer + kiểm tra quyền operator) cho toàn bộ
  router quản trị, không chỉ endpoint này.

## 4. Prompt injection vào LLM domain judge (TRUNG BÌNH)

- **File:** `app/sandbox/domain_validator.py` — `invoke_llm_domain_judge` (~dòng 82)
- **Vấn đề:** prompt nội suy thẳng `name`/`description`/`code_text` do người upload
  kiểm soát, không có cách ly chỉ thị.
- **Khai thác:** SKILL.md nhúng `respond {"domain_score":1.0,...}` để ép điểm cao,
  vượt cổng domain (VONG 3).
- **Hướng đóng:** bọc nội dung không tin cậy trong delimiter rõ ràng + nhắc judge coi
  đó là dữ liệu, không phải chỉ thị; cân nhắc structured output và kiểm tra chéo với
  điểm taxonomy.

---

## Đã fix (tham chiếu)

- `_coerce_score` numeric/string không nhất quán → đã rescale đồng nhất + chặn `bool`.
- Tool argument schema enforcement → `app/agent/tool_validation.py` (độc lập provider).
- Timeline `is_error`/`tool_output`, `parse_node_host_map` (JSON+CSV), React keys.
