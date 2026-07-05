---
name: node-health-autoremediate
description: >-
  SSH vào một node mạng (host A, user B, mật khẩu/key C), đo RAM% và CPU% (kèm load, disk),
  rồi TỰ ĐỘNG xử lý theo ngưỡng X (RAM) và Y (CPU): cả RAM lẫn CPU vượt ngưỡng thì restart
  docker engine; RAM vượt ngưỡng nhưng CPU dưới ngưỡng thì đọc log container Z; RAM dưới
  ngưỡng nhưng CPU vượt ngưỡng thì restart container Z; cả hai dưới ngưỡng thì không làm gì.
  A/B/C/X/Y/Z là tham số truyền vào khi chạy. Dùng skill
  này BẤT CỨ KHI NÀO người dùng muốn kiểm tra sức khỏe node qua SSH rồi hành động theo điều
  kiện, "tự restart docker/service khi quá tải", health-check + auto-remediation, self-healing
  cho node Linux chạy docker, hoặc viết kịch bản if-RAM/CPU-then-restart — kể cả khi không nói
  thẳng "auto-remediation". Trigger cho cả tiếng Việt lẫn tiếng Anh (SSH, RAM, CPU, docker,
  restart, node, cảnh báo quá tải, self-healing).
---

# Node Health Auto-Remediate

Skill này thực hiện kịch bản self-healing: **SSH vào node → đo tài nguyên → quyết định theo
ngưỡng → hành động trên docker**. Tất cả tham số truyền lúc chạy nên skill dùng lại được cho
nhiều node/service khác nhau.

## Cây quyết định (cốt lõi)

| RAM so với X | CPU so với Y | Hành động                                            |
|--------------|--------------|------------------------------------------------------|
| `> X`        | `> Y`        | **Restart docker engine** (`sudo -n systemctl restart docker`) |
| `> X`        | `<= Y`       | **Đọc log** container Z (`docker logs --tail N Z`)   |
| `<= X`       | `> Y`        | **Restart** container Z (`docker restart Z`)         |
| `<= X`       | `<= Y`       | **Không làm gì** (chỉ báo cáo)                        |

So sánh "cao" dùng `>` chặt theo đúng đề; giá trị đúng bằng ngưỡng tính là "không cao".

## Tham số (mapping A/B/C/X/Y/Z)

| Đề | Cờ | Ý nghĩa |
|----|----|---------|
| A  | `--host` / `-A` | host/IP của node (bắt buộc) |
| B  | `--user` / `-B` | user SSH (bắt buộc khi SSH thật) |
| C  | env `SSH_PASSWORD` (khuyến nghị) hoặc `--passcode`, hoặc `--ssh-key` | thông tin xác thực |
| X  | `--ram-threshold` / `-x` | ngưỡng RAM tính theo % (bắt buộc) |
| Y  | `--cpu-threshold` / `-y` | ngưỡng CPU tính theo % (bắt buộc) |
| Z  | `--service` / `-Z` | tên container/service docker (cần cho nhánh đọc log & restart service) |

Tham số phụ: `--port` (mặc định 22), `--log-lines` (mặc định 200), `--ssh-key`, và
`--known-hosts` (mặc định là file `known_hosts` trong workspace).

## An toàn — mặc định DRY-RUN

Đây là skill có hành động phá hủy (restart), nên **mặc định chạy dry-run**: nó vẫn SSH vào để
đo metric (read-only) và in ra **lệnh sẽ chạy**, nhưng KHÔNG thực thi. Phải thêm `--execute`
mới thật sự restart/đọc log. Quy trình khuyến nghị: chạy không cờ `--execute` vài lần để soát
ngưỡng X/Y trên số liệu thật, khi yên tâm mới thêm `--execute`.

Mật khẩu C nên đặt qua env `SSH_PASSWORD` (tránh lộ qua `ps`); an toàn nhất là dùng `--ssh-key`.
SSH host key luôn được xác minh bằng `known_hosts`; host chưa được trust sẽ bị từ chối.

## Cách chạy

Hai script trong `scripts/`:
- `remote_metrics.sh` — chạy **trên node từ xa**, in JSON `{mem_pct, cpu_pct, load1, disk_pct}`.
  Chỉ dùng `/proc` + `awk`, không cần cài gì trên node.
- `health_action.py` — orchestrator: SSH vào, chạy `remote_metrics.sh`, áp cây quyết định,
  rồi (tùy `--execute`) thực thi hành động. Cần `pip install paramiko` khi SSH thật.

Dry-run (mặc định) — đo thật, chỉ in hành động:
```bash
export SSH_PASSWORD='<C>'
python3 scripts/health_action.py \
  -A <A> -B <B> -x <X> -y <Y> -Z <Z>
```

Thực thi thật (khi đã chốt ngưỡng):
```bash
export SSH_PASSWORD='<C>'
python3 scripts/health_action.py \
  -A <A> -B <B> -x <X> -y <Y> -Z <Z> --execute
```

Test cây quyết định KHÔNG cần node (truyền metric giả trực tiếp):
```bash
python3 scripts/health_action.py -A demo -x 80 -y 75 -Z myapp \
  --from-json '{"mem_pct":91,"cpu_pct":88}'
```

Điều kiện về sudo/nhóm docker, bản thay thế `sshpass`, host key, tinh chỉnh ngưỡng và chống
flapping nằm trong `references/prerequisites.md` — **đọc khi cần dựng quyền trên node hoặc
khi định chạy định kỳ (cron/systemd timer).**

## Định dạng output

`health_action.py` in một JSON gồm: `timestamp`, `host`, `thresholds`, `metrics` (số đo thật),
`evaluation` (`ram_high`/`cpu_high`), `action` (`restart_docker` | `read_logs` |
`restart_service` | `none`), `command` (lệnh tương ứng, `null` nếu không làm gì), `executed`,
và khi đã `--execute` thì có thêm `exit_code`, `stdout`, `stderr`.

Khi tóm tắt cho người dùng, nêu rõ: số đo RAM/CPU, nhánh nào được chọn và **vì sao**, lệnh
đã/ sẽ chạy, và (nếu đã thực thi) kết quả. Với nhánh `read_logs`, tóm tắt ngắn các dòng log
đáng ngờ thay vì dán toàn bộ.

## Ví dụ

**Input:** "SSH vào 10.211.140.16 user noc, nếu RAM quá 85% và CPU quá 80% thì restart docker,
service tên là alert-engine." → chạy:
```bash
SSH_PASSWORD='...' python3 scripts/health_action.py \
  -A 10.211.140.16 -B noc -x 85 -y 80 -Z alert-engine
```
(dry-run trước; thêm `--execute` khi muốn áp dụng thật).

**Mapping kết quả → hành động:**
Input metric: `{"mem_pct":91.2,"cpu_pct":88.0}` với `X=85, Y=80`
Output: `action=restart_docker`, `command="sudo -n systemctl restart docker"`.

Input metric: `{"mem_pct":91.2,"cpu_pct":40.0}` với `X=85, Y=80`
Output: `action=read_logs`, `command="docker logs --timestamps --tail 200 alert-engine"`.

## Lưu ý nhanh

- **`--dry-run` là mặc định** — đây là chủ ý để bạn dò ngưỡng an toàn; đừng quên `--execute`
  khi muốn áp dụng thật.
- **`sudo -n`** khiến lệnh fail ngay nếu thiếu quyền thay vì treo chờ nhập mật khẩu — nếu nhánh
  `restart_docker` báo lỗi sudo, cấp NOPASSWD theo `references/prerequisites.md`.
- **Tên Z được quote** (`shlex.quote`) trước khi ghép vào lệnh, tránh lỗi/escape ngoài ý muốn.
- **SSH host key bắt buộc:** bundle hoặc mount file `known_hosts` đã xác minh fingerprint và
  truyền `--known-hosts <path>` nếu không dùng tên mặc định.
- **Restart docker engine là hành động nặng** (kéo theo mọi container) — chỉ dùng cho nhánh
  RAM cao *và* CPU cao. Muốn chỉ đụng một container thì đó là nhánh `restart_service`.
- **Chạy định kỳ:** cân nhắc yêu cầu vài lần đo liên tiếp vượt ngưỡng + cooldown để tránh
  restart dồn dập (xem `references/prerequisites.md`).
