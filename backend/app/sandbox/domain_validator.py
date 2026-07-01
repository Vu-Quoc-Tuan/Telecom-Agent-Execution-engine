# backend/app/sandbox/domain_validator.py
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.common.utils import extract_json_object
from app.llm.gateway import LLMGateway
from app.llm.schemas import LLMMessage, MessageRole
from app.observability.langfuse import SKILL_DOMAIN_JUDGE_PROMPT_NAME, telemetry_tracker
from app.observability.redaction import DataRedactor

SKILL_DOMAIN_JUDGE_FALLBACK_PROMPT = (
    "Đánh giá mức liên quan viễn thông của Skill từ 0.0 đến 1.0. "
    "Tên: {{name}}. Mô tả: {{description}}. Mã Python:\n{{code_text}}"
)
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

    @staticmethod
    def _local_domain_judge_prompt(name: str, description: str, code_text: str) -> str:
        variables = {
            "name": name,
            "description": description,
            "code_text": code_text,
        }
        return re.sub(
            r"\{\{(name|description|code_text)\}\}",
            lambda match: variables[match.group(1)],
            SKILL_DOMAIN_JUDGE_FALLBACK_PROMPT,
        )

    @classmethod
    async def invoke_llm_domain_judge(
        cls, llm_gateway: LLMGateway, name: str, description: str, code_text: str
    ) -> LLMDomainJudgeOutput:
        """DÙNG LLM GATEWAY LÀM TRỌNG TÀI THẨM ĐỊNH LOGIC SÂU"""
        prompt_obj = telemetry_tracker.get_prompt(
            SKILL_DOMAIN_JUDGE_PROMPT_NAME,
            fallback_text=SKILL_DOMAIN_JUDGE_FALLBACK_PROMPT,
        )
        if prompt_obj is not None:
            try:
                prompt = prompt_obj.compile(
                    name=name,
                    description=description,
                    code_text=code_text,
                )
            except Exception:
                prompt = cls._local_domain_judge_prompt(name, description, code_text)
        else:
            prompt = cls._local_domain_judge_prompt(name, description, code_text)
        response = await llm_gateway.invoke(
            messages=[LLMMessage(role=MessageRole.USER, content=prompt)],
            system_prompt=SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT,
        )
        raw_content = response.content or ""
        try:
            data = cls._parse_judge_payload(raw_content)
            return LLMDomainJudgeOutput(**data)
        except Exception as exc:
            preview = DataRedactor.redact_text(raw_content).replace("\n", " ").strip()[:240]
            return LLMDomainJudgeOutput(
                domain_score=0.0,
                reason=(
                    "Lỗi định dạng phản hồi từ LLM Judge "
                    f"({type(exc).__name__}). Raw preview: {preview or '<empty>'}"
                ),
                suspicious_points="LLM Glitch",
            )

    @classmethod
    def _parse_judge_payload(cls, content: str) -> dict[str, Any]:
        data = json.loads(extract_json_object(content))
        data = cls._unwrap_judge_mapping(data)

        score = cls._first_present(
            data,
            "domain_score",
            "domainScore",
            "score",
            "relevance_score",
            "telecom_score",
            "telco_score",
        )
        reason = cls._first_present(data, "reason", "explanation", "rationale", "justification")
        suspicious_points = cls._first_present(
            data,
            "suspicious_points",
            "suspiciousPoints",
            "suspicious",
            "risks",
            "concerns",
        )

        if score is None:
            raise ValueError("Missing domain score.")

        return {
            "domain_score": cls._coerce_score(score),
            "reason": str(reason or "No reason returned by LLM judge."),
            "suspicious_points": cls._stringify_points(suspicious_points),
        }

    @classmethod
    def _unwrap_judge_mapping(cls, data: Any) -> dict[str, Any]:
        if isinstance(data, list) and data:
            return cls._unwrap_judge_mapping(data[0])
        if not isinstance(data, dict):
            raise ValueError("LLM judge response is not an object.")

        score_keys = {
            "domain_score",
            "domainScore",
            "score",
            "relevance_score",
            "telecom_score",
            "telco_score",
        }
        if score_keys.intersection(data):
            return data

        for key in ("result", "judge", "judgement", "judgment", "analysis", "output"):
            nested = data.get(key)
            if isinstance(nested, dict | list):
                return cls._unwrap_judge_mapping(nested)

        return data

    @staticmethod
    def _first_present(data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data:
                return data[key]
        return None

    @staticmethod
    def _rescale_bare_number(score: float) -> float:
        """Diễn giải số trần theo thang đo: (1,10] là thang /10, (10,100] là thang /100."""
        if score > 1 and score <= 10:
            return score / 10
        if score > 10:
            return score / 100
        return score

    @classmethod
    def _coerce_score(cls, value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("Unsupported score type.")
        if isinstance(value, int | float):
            score = cls._rescale_bare_number(float(value))
        elif isinstance(value, str):
            text = value.strip()
            percent_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
            ratio_match = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*(10|100)\b", text)
            number_match = re.search(r"-?\d+(?:\.\d+)?", text)
            if percent_match:
                score = float(percent_match.group(1)) / 100
            elif ratio_match:
                numerator = float(ratio_match.group(1))
                denominator = float(ratio_match.group(2))
                score = numerator / denominator
            elif number_match:
                score = cls._rescale_bare_number(float(number_match.group(0)))
            else:
                raise ValueError("Score is not numeric.")
        else:
            raise ValueError("Unsupported score type.")

        return max(0.0, min(score, 1.0))

    @staticmethod
    def _stringify_points(value: Any) -> str:
        if value is None or value == "":
            return "None"
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "; ".join(str(item) for item in value) or "None"
        return str(value)
