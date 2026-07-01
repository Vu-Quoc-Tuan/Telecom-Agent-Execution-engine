"Giới thiệu:
 Đây là module trung tâm của toàn bộ nền tảng — nơi AI Agent thực sự 'hoạt động'. Kỹ sư vận hành đặt câu hỏi hoặc giao việc cho Agent (VD: 'Kiểm tra trạng thái các node trong cluster HN-01'); Agent suy luận, gọi Skills phù hợp, tổng hợp kết quả và trả lời. Toàn bộ quá trình được hiển thị theo thời gian thực để người dùng theo dõi và can thiệp khi cần.

 Mục tiêu:
 • Xây dựng Agent Execution Engine: vòng lặp reasoning-acting tích hợp LLM API thực tế (hỗ trợ ≥ 2 provider).
 • Xử lý tool-calling loop: LLM quyết định Skill cần dùng → gọi Skill → trả kết quả về LLM → lặp lại đến khi hoàn thành.
 • Giao diện chat streaming: hiển thị phản hồi theo thời gian thực, từng bước suy luận và lệnh gọi Skill.
 • Tích hợp Langfuse để ghi nhận trace/session phục vụ observability."	"1. Agent Execution Engine hỗ trợ ≥ 2 LLM provider (OpenAI và Anthropic/Claude).
 2. Giao diện chat với streaming response và hiển thị reasoning steps + tool calls theo thời gian thực.
 3. Tích hợp Langfuse để ghi nhận trace và session.
 4. Hỗ trợ ≥ 3 Skills mẫu vận hành viễn thông trong demo.
 5. End-to-end test với ≥ 5 kịch bản thực tế.
 6. Tài liệu kiến trúc hệ thống, hướng dẫn cấu hình LLM provider.
 7. Video demo, báo cáo thực tập và slide demo cuối kỳ."