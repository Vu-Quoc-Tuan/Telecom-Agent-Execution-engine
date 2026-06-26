# backend/app/api/router.py
from fastapi import APIRouter

from app.api import approvals, chat, resources, runs, sessions, skills

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(chat.router, prefix="/chat", tags=["Agent Chat Stream"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["Session History"])
api_router.include_router(
    approvals.router, prefix="/approvals", tags=["Human-in-the-loop Approvals"]
)
api_router.include_router(skills.router, prefix="/skills", tags=["Dynamic Skill Registry"])
api_router.include_router(runs.router, prefix="/runs", tags=["Agent Run Timeline"])
api_router.include_router(resources.router, prefix="/resources", tags=["Runtime Resource Inventory"])
