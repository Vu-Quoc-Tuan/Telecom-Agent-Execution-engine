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
from app.services.skills import SkillUploadCommand, SkillValidationError, SkillValidationService

router = APIRouter()


class ReviewSkillPayload(BaseModel):
    note: str | None = None


@router.get("")
def list_skills(status: str | None = None, db: Session = Depends(get_db)):
    skills = SkillRepository.list_skills(db, status=status)
    return [
        {
            "id": str(skill.id),
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "status": skill.status,
            "is_malicious": skill.is_malicious,
            "security_review_log": skill.security_review_log,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
            "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
            "frontmatter": skill.frontmatter,
            "bundled_files": skill.bundled_files,
        }
        for skill in skills
    ]


@router.post("/upload")
async def upload_and_verify_skill_pipeline(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    service = SkillValidationService()
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=415, detail="Skill phải được upload dưới dạng file .zip.")
    try:
        zip_bytes = await file.read(service.MAX_ARCHIVE_BYTES + 1)
        result = await service.upload_skill(
            db=db,
            llm_gateway=get_llm_gateway(),
            command=SkillUploadCommand(zip_bytes=zip_bytes),
        )
    except SkillValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "status": exc.status,
                "message": exc.message,
                "skill_id": exc.skill_id,
                "logs": exc.logs,
            },
        ) from exc
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

    approved = SkillRepository.approve_skill(db, skill_id)
    return {
        "status": "READY",
        "skill_id": str(approved.id),
        "name": approved.name,
        "version": approved.version,
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
