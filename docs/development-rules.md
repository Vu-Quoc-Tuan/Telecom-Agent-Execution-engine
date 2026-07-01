Cấp độ áp dụng: Nghiêm ngặt (Mandatory)

Tài liệu này quy định cấu trúc tổ chức mã nguồn độc lập, chống lại hành vi viết code gộp (Monolithic bundling) hoặc trộn lẫn logic nghiệp vụ cốt lõi với giao thức truyền thông mạng. Mọi thành viên phát triển hệ thống bắt buộc phải tuân thủ việc phân tách file rạch ròi.
1. Bản đồ Phân lớp Nhiệm vụ (Separation of Concerns)

Mã nguồn Backend bắt buộc phải phân rã thành các module cô lập với ranh giới trách nhiệm như sau:
Plaintext

[api/ Tầng mạng giao tiếp REST/SSE]
       │ (Hứng dữ liệu mạng, gọi Service, bọc bộ Format Transport)
       ▼
[services/ Tầng nghiệp vụ lõi & LangGraph]
       │ (Điều phối thuật toán, ghi nhận DB, nhả Object dữ liệu sạch)
       ▼
[streaming/ Tầng định dạng gói tin đường truyền]
       │ (Định nghĩa Event Pydantic, hóa thô chuỗi mạng SSE)
       ▼
[connectors/ & sandbox/ Tầng hạ tầng vật lý và kiểm tra tĩnh]
       (Kết nối trạm, chặn mã/lệnh nguy hiểm)

2. Các Quy tắc Chia file Cụ thể
🛑 Quy tắc 1: Tầng Dịch vụ (services/) tuyệt đối không được biết đến giao thức mạng

    Tệp tin áp dụng: backend/app/services/agent_execution.py

    Hành vi nghiêm cấm: Cấm tuyệt đối việc sử dụng chuỗi text thô dạng event: ...\ndata: ...\n\n hoặc import bất kỳ hàm nào liên quan đến kỹ thuật đóng gói SSE bên trong tầng này.

    Tiêu chuẩn bắt buộc: Tầng Service thực thi đồ thị LangGraph, thực hiện các thao tác CRUD qua Repository và chỉ được phép yield về các cấu trúc dữ liệu sạch (Tuple, Dict hoặc Pydantic Object).

📡 Quy tắc 2: Tầng Truyền phát (streaming/) độc lập định dạng Transport

    Tệp tin áp dụng: backend/app/streaming/sse.py, events.py

    Tiêu chuẩn bắt buộc: * events.py quản lý cấu trúc gói tin bằng Pydantic Model để validate đầu ra sạch.

        sse.py giữ duy nhất nhiệm vụ biên dịch Object thành chuỗi mạng thô theo đặc tả REST SSE. Tầng này không được chứa bất kỳ logic nào liên quan đến Database hay LangGraph State.

🔌 Quy tắc 3: Tầng API (api/) làm nhiệm vụ Kết nối Trung gian (Adapter)

    Tệp tin áp dụng: backend/app/api/chat.py

    Tiêu chuẩn bắt buộc: Hàm Endpoint của FastAPI đóng vai trò là trạm trung chuyển. Nó hứng luồng dữ liệu sạch từ services/, đi qua bộ lọc bọc của streaming/ để hóa thô văn bản mạng, rồi ném vào StreamingResponse. Không viết logic xử lý đồ thị hay logic validate an ninh trạm vật lý tại đây.

🛡️ Quy tắc 4: Cô lập Tuyệt đối giữa Agent Skills và Connectors

    Hành vi nghiêm cấm: Skill upload không được chứa credential, tự cấu hình kết nối trạm hoặc trực tiếp thực thi mã để truy cập hạ tầng.

    Tiêu chuẩn bắt buộc: Credential chỉ được lấy từ cấu hình backend. Driver vật lý nằm tại `connectors/ssh.py`, `connectors/clickhouse.py` và `connectors/postgres.py`. Agent Skills chỉ cung cấp hướng dẫn và tài nguyên; mọi thao tác hạ tầng phải đi qua backend-owned capability có runner/template/schema và safety policy cố định.

🧩 Quy tắc 5: Không auto-run payload do LLM tự nghĩ ra

    Hành vi nghiêm cấm: Không expose tool free-form ở chế độ `auto_execute` cho mã Python, shell, SQL, SSH command, wrapper hoặc script body do LLM tự sinh trong lúc chat. Một payload sạch theo AST/regex vẫn chưa phải là artifact đã được duyệt.

    Tiêu chuẩn bắt buộc: Chỉ hai nhóm được auto-run: script nằm trong gói skill đã qua static scan, secret scan, domain validation, LLM-assisted run-spec proposal, backend validation, Cube smoke test và human approval; hoặc backend-owned built-in capability do dev hardcode runner/template/schema. Payload phát sinh ngoài hai nhóm này phải bị từ chối; nếu cần vận hành lặp lại thì phải thêm reviewed skill script hoặc backend-owned HITL capability với schema cố định.

3. Lý do Kiến trúc (Architectural Justification)

    Khả năng Bảo trì (Maintainability): Khi hệ thống quyết định chuyển từ giao thức truyền phát Server-Sent Events (SSE) sang WebSockets hoặc gRPC, hệ thống chỉ cần thay đổi/đập đi xây lại duy nhất thư mục streaming/ và lớp vỏ ngoài của api/. Toàn bộ lõi thuật toán đồ thị LangGraph và Dynamic Skill Registry ở tầng Service được giữ nguyên vẹn 100%.

    Khả năng Kiểm thử (Testability): Việc tách biệt dữ liệu sạch ra khỏi chuỗi text mạng giúp các kỹ sư QA có thể viết Unit Test độc lập bằng pytest cho tầng Service cực kỳ nhàn, bốc trực tiếp Dict/Object ra so sánh thay vì phải đi parse chuỗi string mạng \n\n rườm rà.

    An ninh Hạ tầng (Security Boundaries): Tách Connectors ra khỏi Skill giúp Backend làm chủ hoàn toàn chốt chặn Allowlist/Blocklist Regex (Safety Guard), ngăn chặn triệt để hành vi bypass bộ lọc an ninh từ mã nguồn động bên ngoài truyền vào. Tách script đã duyệt khỏi code do LLM tự sinh giúp sandbox là lớp cô lập cuối cùng, không phải lý do để bỏ qua review.
