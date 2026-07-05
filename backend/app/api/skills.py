from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_llm_gateway
from app.database.connection import get_db
from app.database.repositories.skills import SkillRepository
from app.database.repositories.tool_calls import ToolCallRepository
from app.services.skills import SkillUploadCommand, SkillValidationError, SkillValidationService

router = APIRouter()


class ReviewSkillPayload(BaseModel):
    note: str | None = None


def _reject_non_zip(filename: str | None) -> None:
    if Path(filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=415, detail="Skill phải được upload dưới dạng file .zip.")


def _skill_validation_http_error(exc: SkillValidationError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status_code,
        detail={
            "status": exc.status,
            "message": exc.message,
            "skill_id": exc.skill_id,
            "logs": exc.logs,
        },
    )


def _unapproved_script_paths(skill) -> list[str]:
    manifest = getattr(skill, "script_manifest", None) or {}
    if not isinstance(manifest, dict):
        return []
    return [
        str(path)
        for path, entry in sorted(manifest.items())
        if not isinstance(entry, dict) or entry.get("status") != "passed"
    ]


@router.get("")
def list_skills(status: str | None = None, db: Session = Depends(get_db)):
    skills = SkillRepository.list_skills(db, status=status)
    telemetry_by_skill = ToolCallRepository.get_skill_telemetry(
        db,
        [skill.name for skill in skills],
    )
    return [
        {
            "id": str(skill.id),
            "name": skill.name,
            "description": skill.description,
            "skill_md": skill.skill_md,
            "status": skill.status,
            "is_malicious": skill.is_malicious,
            "security_review_log": skill.security_review_log,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
            "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
            "frontmatter": skill.frontmatter,
            "bundled_files": skill.bundled_files,
            "script_manifest": getattr(skill, "script_manifest", {}) or {},
            "telemetry": telemetry_by_skill.get(
                skill.name,
                {
                    "call_count": 0,
                    "average_latency_ms": None,
                    "error_rate": 0.0,
                    "error_count": 0,
                    "last_called_at": None,
                },
            ),
        }
        for skill in skills
    ]


@router.post("/inspect")
async def inspect_skill_package(file: UploadFile = File(...)):
    service = SkillValidationService()
    _reject_non_zip(file.filename)
    try:
        zip_bytes = await file.read(service.MAX_ARCHIVE_BYTES + 1)
        package = service.parse_package(zip_bytes)
    except SkillValidationError as exc:
        raise _skill_validation_http_error(exc) from exc

    return {
        "name": package.name,
        "description": package.description,
        "frontmatter": package.frontmatter,
        "files": [
            {
                "path": path,
                "encoding": record["encoding"],
                "media_type": record["media_type"],
                "size": record["size"],
            }
            for path, record in sorted(package.bundled_files.items())
        ],
    }


@router.post("/upload")
async def upload_and_verify_skill_pipeline(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    service = SkillValidationService()
    _reject_non_zip(file.filename)
    try:
        zip_bytes = await file.read(service.MAX_ARCHIVE_BYTES + 1)
        result = await service.upload_skill(
            db=db,
            llm_gateway=get_llm_gateway(),
            command=SkillUploadCommand(zip_bytes=zip_bytes),
        )
    except SkillValidationError as exc:
        raise _skill_validation_http_error(exc) from exc
    return asdict(result)


@router.post("/{skill_id}/approve")
def approve_skill_for_agent(
    skill_id: uuid.UUID,
    body: ReviewSkillPayload | None = None,
    db: Session = Depends(get_db),
):
    skill = SkillRepository.get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill không tồn tại.")
    if skill.status != "testing":
        raise HTTPException(
            status_code=409, detail="Chỉ skill ở trạng thái testing mới được approve."
        )
    unapproved_scripts = _unapproved_script_paths(skill)
    if unapproved_scripts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Không thể approve skill khi còn script chưa pass validation/Cube smoke test."
                ),
                "scripts": unapproved_scripts,
            },
        )

    approved = SkillRepository.approve_skill(db, skill_id)
    return {
        "status": "READY",
        "skill_id": str(approved.id),
        "name": approved.name,
        "note": body.note if body else None,
    }


@router.post("/{skill_id}/reject")
def reject_skill_after_review(
    skill_id: uuid.UUID,
    body: ReviewSkillPayload,
    db: Session = Depends(get_db),
):
    skill = SkillRepository.get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill không tồn tại.")

    rejected = SkillRepository.reject_skill(db, skill_id, review_log=body.note)
    return {"status": "REJECTED", "skill_id": str(rejected.id), "name": rejected.name}
