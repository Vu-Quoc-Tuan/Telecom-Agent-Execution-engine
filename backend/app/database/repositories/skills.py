from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.skills import Skill


class SkillRepository:
    @staticmethod
    def create_uploaded_skill(
        db: Session,
        *,
        name: str,
        description: str,
        skill_md: str,
        frontmatter: dict | None = None,
        bundled_files: dict | None = None,
        script_manifest: dict | None = None,
        version: str = "1.0.0",
    ) -> Skill:
        """Persist an uploaded Agent Skill package once, awaiting validation result."""
        new_skill = Skill(
            id=uuid.uuid4(),
            name=name,
            description=description,
            version=version,
            skill_md=skill_md,
            frontmatter=frontmatter or {},
            bundled_files=bundled_files or {},
            script_manifest=script_manifest or {},
            status="uploaded",
            is_malicious=False,
        )
        db.add(new_skill)
        db.commit()
        db.refresh(new_skill)
        return new_skill

    @staticmethod
    def get_skill_by_id(db: Session, skill_id: uuid.UUID) -> Skill | None:
        return db.get(Skill, skill_id)

    @staticmethod
    def get_skill_by_name(db: Session, name: str) -> Skill | None:
        stmt = select(Skill).where(Skill.name == name)
        return db.scalar(stmt)

    @staticmethod
    def update_sandbox_result(
        db: Session,
        *,
        skill_id: uuid.UUID,
        status: str,
        review_log: str,
        is_malicious: bool,
    ) -> Skill | None:
        """Update automated validation result and human-review state."""
        skill = db.get(Skill, skill_id)
        if skill:
            skill.status = status
            skill.security_review_log = review_log
            skill.is_malicious = is_malicious
            db.commit()
            db.refresh(skill)
        return skill

    @staticmethod
    def approve_skill(db: Session, skill_id: uuid.UUID) -> Skill | None:
        skill = db.get(Skill, skill_id)
        if skill:
            skill.status = "ready"
            db.commit()
            db.refresh(skill)
        return skill

    @staticmethod
    def reject_skill(
        db: Session, skill_id: uuid.UUID, review_log: str | None = None
    ) -> Skill | None:
        skill = db.get(Skill, skill_id)
        if skill:
            skill.status = "rejected"
            if review_log:
                existing_log = skill.security_review_log or ""
                skill.security_review_log = (
                    f"{existing_log}\n\n[HUMAN_REVIEW]\n{review_log}"
                    if existing_log
                    else f"[HUMAN_REVIEW]\n{review_log}"
                )
            db.commit()
            db.refresh(skill)
        return skill

    @staticmethod
    def delete_skill(db: Session, skill_id: uuid.UUID, commit: bool = True) -> bool:
        skill = db.get(Skill, skill_id)
        if skill is None:
            return False
        db.delete(skill)
        if commit:
            db.commit()
        return True

    @staticmethod
    def list_ready_skills(db: Session) -> list[Skill]:
        """Return all approved dynamic skills available to the LLM/tool runtime."""
        stmt = select(Skill).where(Skill.status == "ready").order_by(Skill.name.asc())
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_skills(db: Session, *, status: str | None = None, limit: int = 200) -> list[Skill]:
        stmt = select(Skill).order_by(Skill.created_at.desc()).limit(limit)
        if status:
            stmt = (
                select(Skill)
                .where(Skill.status == status)
                .order_by(Skill.created_at.desc())
                .limit(limit)
            )
        return list(db.scalars(stmt).all())
