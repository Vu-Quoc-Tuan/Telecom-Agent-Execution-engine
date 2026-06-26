# backend/app/sandbox/domain_validator.py
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.llm.gateway import LLMGateway
from app.llm.schemas import LLMMessage, MessageRole

SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT = (
    "Bạn chỉ được phép phản hồi DUY NHẤT một object JSON hợp lệ khớp chính xác "
    "định dạng yêu cầu (các khóa: domain_score, reason, suspicious_points). "
    "Không kèm văn bản giải thích, không bọc trong khối markdown."
)


class LLMDomainJudgeOutput(BaseModel):
    domain_score: float = Field(
        description="Điểm số đánh giá độ tương quan với ngành viễn thông từ 0.0 đến 1.0"
    )
    reason: str = Field(description="Giải thích chi tiết lý do chấm điểm")
    suspicious_points: str = Field(description="Các điểm nghi vấn hoặc hành vi lạc loài nếu có")


class TelecomDomainValidator:
    # 🟢 LỚP 1: TAXONOMY KEYWORDS CỦA NGÀNH VIỄN THÔNG
    TELECOM_TAXONOMY = {
        "alarm",
        "alert",
        "node",
        "cluster",
        "site",
        "cell",
        "service",
        "interface",
        "kpi",
        "latency",
        "packet_loss",
        "throughput",
        "snmp",
        "prometheus",
        "clickhouse",
        "ran",
        "core_network",
        "gnodeb",
        "enodeb",
        "router",
        "switch",
        "noc",
        "soc",
        "vdt",
    }

    @classmethod
    def calculate_taxonomy_score(cls, name: str, description: str, code_text: str) -> float:
        """Tính toán tần suất xuất hiện của từ khóa chuyên ngành để làm rào chắn vòng 1"""
        combined_text = f"{name} {description} {code_text}".lower()
        # Tìm tất cả các từ đơn thuần chữ
        words = set(re.findall(r"\b[a-z_]+\b", combined_text))

        matched_keywords = words.intersection(cls.TELECOM_TAXONOMY)
        if not matched_keywords:
            return 0.0

        # Trả về tỷ lệ mật độ từ khóa khớp trên tổng tập từ khóa chuẩn
        return min(len(matched_keywords) / 4, 1.0)

    @classmethod
    async def invoke_llm_domain_judge(
        cls, llm_gateway: LLMGateway, name: str, description: str, code_text: str
    ) -> LLMDomainJudgeOutput:
        """🟢 LỚP 2: DÙNG LLM GATEWAY LÀM TRỌNG TÀI THẨM ĐỊNH LOGIC SÂU"""
        prompt = f"""
        Bạn là một chuyên gia kiểm toán quy trình và kiến trúc mạng Viễn thông cấp cao.
        Hãy phân tích kịch bản tự động hóa (Skill) sau đây để xác định xem nó có thực sự phục vụ mục đích vận hành, xử lý sự cố mạng viễn thông không.

        Thông tin kịch bản:
        - Tên hàm: {name}
        - Mô tả: {description}
        - Mã nguồn Python:
        {code_text}

        Nhiệm vụ: Chấm điểm từ 0.0 (Không liên quan, ví dụ: hack game, download video, spam mail, đào coin) đến 1.0 (Tuyệt đối phục vụ Telco).
        """

        # Không ép response_format ở đây: param này chỉ hợp lệ với OpenAI-compatible API,
        # còn Anthropic Messages API sẽ báo lỗi tham số. Thay vào đó ràng buộc JSON qua
        # system prompt + parser tolerant bên dưới -> chạy đồng nhất trên mọi provider.
        response = await llm_gateway.invoke(
            messages=[LLMMessage(role=MessageRole.USER, content=prompt)],
            system_prompt=SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT,
        )

        # Parse chuỗi kết quả sang Pydantic Object để ép kiểu nghiêm ngặt
        try:
            data = json.loads(cls._extract_json_object(response.content or ""))
            return LLMDomainJudgeOutput(**data)
        except Exception:
            return LLMDomainJudgeOutput(
                domain_score=0.0,
                reason="Lỗi định dạng phản hồi từ LLM Judge.",
                suspicious_points="LLM Glitch",
            )

    @staticmethod
    def _extract_json_object(content: str) -> str:
        """Trích object JSON đầu tiên trong phản hồi, chịu được markdown fence / text thừa."""
        text = content.strip()
        if text.startswith("```"):
            # Gỡ ```json ... ``` nếu provider bọc trong code fence
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text
