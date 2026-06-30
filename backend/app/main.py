from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agent.checkpointer import WorkflowCheckpointer
from app.agent.graph import build_telecom_agent
from app.api.router import api_router
from app.common.exceptions import TelecomAgentException
from app.config import get_llm_gateway, settings
from app.database.connection import SessionLocal
from app.observability.logging import app_logger
from app.services.agent_execution import AgentExecutionService
from app.services.runs import RunLifecycleService


def sweep_timed_out_runs_once() -> int:
    with SessionLocal() as db:
        timed_out_runs = RunLifecycleService.mark_timed_out_runs(
            db=db,
            timeout_seconds=settings.RUN_TIMEOUT_SECONDS,
            limit=settings.RUN_TIMEOUT_SWEEPER_LIMIT,
        )
    return len(timed_out_runs)


def verify_database_connectivity() -> None:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))


async def timeout_sweeper_loop() -> None:
    while True:
        await asyncio.sleep(settings.RUN_TIMEOUT_SWEEPER_INTERVAL_SECONDS)
        try:
            timed_out_count = await asyncio.to_thread(sweep_timed_out_runs_once)
            if timed_out_count:
                app_logger.info("Timeout sweeper marked %s run(s) as timed_out.", timed_out_count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            app_logger.warning("Timeout sweeper failed: %s", exc)


@asynccontextmanager
async def telecom_agent_lifespan(app: FastAPI):
    verify_database_connectivity()
    checkpointer = WorkflowCheckpointer(settings=settings)
    try:
        saver = await checkpointer.initialize()
    except Exception as exc:
        if settings.ENVIRONMENT == "production" and settings.CHECKPOINTER_BACKEND == "postgres":
            raise
        app_logger.warning(
            "Postgres checkpointer unavailable; falling back to in-memory saver: %s", exc
        )
        checkpointer = WorkflowCheckpointer(settings=settings, backend="memory")
        saver = await checkpointer.initialize()

    agent_graph = build_telecom_agent(checkpointer=saver)
    AgentExecutionService.configure(agent_app=agent_graph)
    app.state.checkpointer = checkpointer
    app.state.agent_graph = agent_graph
    timeout_sweeper_task = None
    if settings.RUN_TIMEOUT_SWEEPER_ENABLED:
        timeout_sweeper_task = asyncio.create_task(timeout_sweeper_loop())
        app.state.timeout_sweeper_task = timeout_sweeper_task

    try:
        yield
    finally:
        if timeout_sweeper_task is not None:
            timeout_sweeper_task.cancel()
            with suppress(asyncio.CancelledError):
                await timeout_sweeper_task
        await checkpointer.close()
        try:
            await get_llm_gateway().close()
        except Exception as exc:
            app_logger.warning("LLM gateway shutdown finished with warning: %s", exc)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Telecom AI Agent Backend",
        description="Backend execution engine for telecom ReAct agent, dynamic skills, HITL approvals, and SSE timeline streaming.",
        version="1.0.0",
        lifespan=telecom_agent_lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router)

    @application.exception_handler(TelecomAgentException)
    async def telecom_custom_exception_handler(request: Request, exc: TelecomAgentException):
        status_map = {
            "SAFETY_VIOLATION_LIMIT": 403,
            "DYNAMIC_COMPILE_FAILED": 400,
            "SKILL_RUNTIME_CRASH": 500,
            "CONNECTOR_IO_ERROR": 503,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={
                "status": "FAILED",
                "error_code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @application.get("/health", tags=["Infrastructure Health"])
    def health_check():
        return {
            "status": "GREEN",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "telecom-ai-agent-backend",
        }

    return application


app = create_app()
