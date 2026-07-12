# Hướng dẫn cài đặt, chạy và vận hành

Tài liệu này là runbook dành cho môi trường development/demo của Telecom Agent Execution Engine.
Các lệnh được chạy từ thư mục gốc repository, trừ khi phần hướng dẫn ghi rõ thư mục khác.

> API hiện chưa có authentication/authorization. Không mở edge/ngrok cho mạng không tin cậy hoặc
> dùng như production trước khi bổ sung lớp xác thực và phân quyền.

## 1. Thành phần và cổng mặc định

| Thành phần | Cách chạy | Cổng mặc định |
|---|---|---:|
| PostgreSQL ứng dụng/checkpointer | Docker Compose | `5432` |
| FastAPI backend | Host | `8000` |
| Next.js frontend | Host hoặc Docker | `3000` |
| Nginx one-origin edge | Docker Compose | `8080` |
| Docker skill sandbox | Container ngắn hạn do backend tạo | Không publish cổng |

`make up` cố ý chạy backend trên host. Backend cần truy cập Docker CLI/daemon của host để tạo
container sandbox cho `run_skill_script`; frontend, PostgreSQL và edge vẫn chạy bằng Compose.

## 2. Yêu cầu hệ thống

- Linux hoặc môi trường tương thích Docker.
- Docker Engine và Docker Compose plugin.
- Python 3.12.
- `uv` để đồng bộ backend; repository cũng có thể dùng `backend/.venv` đã được tạo bởi `uv`.
- Node.js 22 trở lên và npm.
- Quyền truy cập các LLM/connectors mà bạn cấu hình.

Kiểm tra nhanh:

```bash
docker --version
docker compose version
python3 --version
uv --version
node --version
npm --version
```

## 3. Setup lần đầu

```bash
cp .env.example .env
# Tuỳ chọn: cấu hình connector hạ tầng (ClickHouse / external Postgres / SSH)
cp .env.external.example .env.external
make setup
docker compose up -d postgres
make migrate
```

`.env` là cấu hình chính (LLM, app DB, sandbox, Langfuse). `.env.external` là lớp ghi đè tuỳ chọn
cho connector; có thể bỏ qua nếu chưa cần (chi tiết load order ở mục 4).

`make setup` chạy `uv sync` cho backend và `npm ci` cho frontend. `make migrate` chạy toàn bộ
Alembic migration lên database được cấu hình.

Build image sandbox trước khi chạy skill script thật:

```bash
docker build -t telecom-agent-sandbox:latest -f sandbox.Dockerfile .
```

Schema database chỉ tạo/cập nhật qua Alembic: `make migrate`.

## 4. Cấu hình `.env`

Settings được đọc từ `.env`, sau đó `.env.external`. Environment variable của process có độ ưu
tiên cao hơn file. Giá trị rỗng bị bỏ qua và dùng default trong `backend/app/config.py`.

### 4.1 OpenAI hoặc OpenAI-compatible router

```dotenv
PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_API_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4o
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_MAX_TOKENS=4096
```

Với router nội bộ, thay `OPENAI_API_URL` và `OPENAI_MODEL_NAME` bằng endpoint/model router hỗ trợ.
Tên adapter trong log vẫn là `openai` vì ứng dụng dùng OpenAI-compatible protocol; provider thật
phía sau router có thể khác.

Nếu router không hỗ trợ strict tool schema:

```dotenv
OPENAI_SUPPORTS_TOOL_STRICT=false
```

### 4.2 Anthropic

```dotenv
PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key
ANTHROPIC_API_URL=https://api.anthropic.com
ANTHROPIC_MODEL_NAME=claude-3-5-sonnet-20241022
```

Chỉ adapter có API key mới được đăng ký. Sau khi đổi provider, model, URL, key, timeout hoặc retry,
phải restart backend vì settings và LLM gateway được cache khi process khởi động.

Kiểm tra model mà UI thực sự nhận:

```bash
curl -sS http://127.0.0.1:8000/api/v1/chat/options
```

### 4.3 PostgreSQL ứng dụng

Cách cấu hình rời:

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=telecom_agent
CHECKPOINTER_BACKEND=postgres
```

Hoặc dùng URL đầy đủ:

```dotenv
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/telecom_agent
CHECKPOINTER_DATABASE_URL=
```

`CHECKPOINTER_DATABASE_URL` rỗng sẽ dùng chung database URL của ứng dụng.

### 4.4 Connectors hạ tầng

ClickHouse:

```dotenv
CLICKHOUSE_HOST=
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=alarm_data
CLICKHOUSE_USER=
CLICKHOUSE_PASSWORD=
```

PostgreSQL inventory bên ngoài:

```dotenv
EXTERNAL_POSTGRES_HOST=
EXTERNAL_POSTGRES_PORT=5432
EXTERNAL_POSTGRES_DATABASE=postgres
EXTERNAL_POSTGRES_USER=
EXTERNAL_POSTGRES_PASSWORD=
```

SSH:

```dotenv
SSH_HOST=
SSH_PORT=22
SSH_USER=
SSH_PASSWORD=
SSH_ALLOWED_NODES=site-a,site-b
SSH_NODE_HOST_MAP=site-a=10.0.0.11,site-b=node-b.internal
SSH_AUTO_ADD_HOST_KEYS=false
SSH_KNOWN_HOSTS=
SSH_RESTART_ALLOWED_SERVICES=nginx,node-exporter.service
```

Production/demo nghiêm túc nên dùng known-host verification và credential có quyền tối thiểu.

Smoke-check connector thật (không nằm trong `make test`; cần `.env` / `.env.external` đầy đủ):

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/check_connections.py
```

### 4.5 Docker sandbox

```dotenv
SANDBOX_ENABLED=true
SANDBOX_IMAGE=telecom-agent-sandbox:latest
SANDBOX_TIMEOUT_SECONDS=30
SANDBOX_MEMORY=256m
SANDBOX_CPUS=1.0
SANDBOX_NETWORK=none
```

Giữ `SANDBOX_NETWORK=none` theo mặc định. Chỉ chuyển sang network khác khi skill đã được review và
thực sự cần truy cập hạ tầng tin cậy.

### 4.6 Langfuse

```dotenv
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PROMPT_LABEL=production
LANGFUSE_PROMPT_CACHE_TTL_SECONDS=300
```

Không có credential thì hệ thống dùng prompt fallback trong code. Prompt managed (nếu dùng) cấu hình
trực tiếp trên Langfuse Prompt Management với label `LANGFUSE_PROMPT_LABEL` (mặc định `production`).
Tên prompt backend fetch:

- `telecom-agent-system`
- `telecom-context-compactor`
- `SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT`

## 5. Chạy development

### 5.1 Chạy từng thành phần

Terminal 1:

```bash
docker compose up -d postgres
make migrate
make dev-backend
```

Terminal 2:

```bash
make dev-frontend
```

Truy cập:

```text
Frontend: http://127.0.0.1:3000/chat
Backend:  http://127.0.0.1:8000
Health:   http://127.0.0.1:8000/health
OpenAPI:  http://127.0.0.1:8000/docs
```

Nếu frontend chạy riêng ở cổng khác, đặt `NEXT_PUBLIC_API_BASE_URL` và bổ sung origin tương ứng vào
`CORS_ORIGINS`.

### 5.2 Chạy stack bằng Makefile

```bash
make up
```

Lệnh này:

1. Chọn cổng backend/frontend chưa bị chiếm.
2. Chạy `postgres`, `frontend`, `edge` bằng Compose.
3. Chạy Uvicorn reloadable trên host.
4. Ghi PID, port và log backend vào `.run/`.
5. Chờ `/health` thành công; nếu backend lỗi, in 40 dòng log cuối và dọn stack.

Xem trạng thái và log:

```bash
make ps
make logs
tail -f .run/backend.log
```

Dừng hoặc restart:

```bash
make down
make restart
```

`make down` không xóa PostgreSQL volume. `make clean` có gọi `docker compose down -v` và sẽ xóa
volume database; chỉ dùng khi bạn chủ động muốn reset toàn bộ dữ liệu local.

## 6. Chạy qua one-origin edge và ngrok

Edge route `/api/v1/*` tới backend host, route phần còn lại tới frontend và tắt proxy buffering cho
SSE.

Local one-origin:

```bash
NEXT_PUBLIC_API_BASE_URL=/api/v1 make up
```

Truy cập `http://127.0.0.1:8080/chat`.

Với public URL tin cậy cho development/demo:

```bash
NEXT_PUBLIC_API_BASE_URL=/api/v1 \
PUBLIC_URL=https://your-domain.example \
make up
```

Sau đó cấu hình ngrok forward vào edge `127.0.0.1:8080`. Không công khai hệ thống cho người dùng
không tin cậy khi auth chưa được triển khai.

## 7. Database và migration

```bash
make migrate
make db-shell
```

Kiểm tra revision:

```bash
cd backend
.venv/bin/alembic current
.venv/bin/alembic heads
```

Tạo migration mới:

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "describe change"
.venv/bin/alembic upgrade head
```

Luôn review migration sinh tự động trước khi chạy lên database có dữ liệu.

## 8. Test, lint, build và eval

Chạy toàn bộ quality gates chính:

```bash
make test
make lint
```

Chạy riêng:

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check app tests scripts
.venv/bin/ruff format --check app tests scripts

cd ../evals
PYTHONPATH=.. ../backend/.venv/bin/pytest -q

cd ../frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
```

Offline Promptfoo evaluation:

```bash
make eval
```

Online eval cần `EVAL_DATASET_URL`; red-team cần `REDTEAM_CONFIG` và provider credential phù hợp:

```bash
EVAL_DATASET_URL=https://example/dataset.yaml make eval-online
REDTEAM_CONFIG=evals/redteam.yaml make redteam
```

## 9. Skill upload và review

Skill ZIP phải chứa đúng một thư mục skill với `SKILL.md`; có thể kèm `scripts/`, `references/` và
`assets/`.

Inspect trước khi upload:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/skills/inspect \
  -F 'file=@Agent_skill/noc-alarm-enrichment.zip;type=application/zip'
```

Upload vào validation pipeline:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/skills/upload \
  -F 'file=@Agent_skill/noc-alarm-enrichment.zip;type=application/zip'
```

Sau automated validation, mở `http://127.0.0.1:3000/admin/skills` để approve/reject. Mỗi lần agent
chạy một approved skill script, operator vẫn phải duyệt riêng cho run đó.

Không chỉnh source tree và ZIP độc lập. Hãy chọn source tree làm nguồn chính rồi build lại ZIP để
tránh upload nhầm artifact cũ.

## 10. Run operations

Hủy run đang active:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/runs/RUN_ID/cancel \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Operator stopped this run."}'
```

Đánh dấu run stale là timed out:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/runs/mark-timeouts \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds":3600,"limit":100}'
```

Backend cũng chạy timeout sweeper khi `RUN_TIMEOUT_SWEEPER_ENABLED=true`.

## 11. Troubleshooting

### Backend không healthy

```bash
tail -120 .run/backend.log
docker compose ps
curl -v http://127.0.0.1:8000/health
```

Kiểm tra PostgreSQL đã chạy và migration đã lên `head`.

### Đổi model nhưng backend vẫn gọi model cũ

Settings và gateway được cache khi backend khởi động. Kiểm tra `.env` không khai báo trùng key, sau
đó restart backend:

```bash
make restart
curl -sS http://127.0.0.1:8000/api/v1/chat/options
```

### Model timeout hoặc không trả lời

- Gọi `/api/v1/chat/options` để xác nhận model/provider UI đang dùng.
- Kiểm tra `OPENAI_API_URL`/`ANTHROPIC_API_URL` và virtual-key permission ở router.
- Nhớ rằng `LLM_MAX_RETRIES=2` nghĩa là sau lần đầu còn tối đa hai lần thử; timeout tổng có thể gần
  ba lần `LLM_TIMEOUT_SECONDS`.
- Chuyển về model đã xác nhận hoạt động rồi restart backend.

### SSE chỉ hiện kết quả cuối

Kiểm tra edge/nginx không buffering. Với custom OpenAI-compatible router, provider có thể không phát
text delta dù request bật streaming; timeline step vẫn được SSE cập nhật độc lập.

### Docker sandbox không chạy

```bash
docker image inspect telecom-agent-sandbox:latest
docker ps
```

Build lại image và xác nhận backend host có quyền gọi Docker daemon. Nếu `SANDBOX_ENABLED=false`,
`run_skill_script` sẽ không khả dụng.

### Langfuse cảnh báo DNS/credential

Langfuse là optional. Khi không truy cập được, agent dùng prompt fallback nhưng log có thể xuất hiện
cảnh báo telemetry. Kiểm tra host/key hoặc để credential rỗng nếu không dùng.