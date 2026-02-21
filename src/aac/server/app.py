"""FastAPI 서버 — AAC HTTP API (FR-9.1~9.3).

Spring Boot의 embedded Tomcat에 해당하며,
AgentApplicationContext를 기반으로 REST API와 WebSocket을 제공한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from aac.context import AgentApplicationContext
from aac.logging.formatter import boot_log


# ─── Request/Response 모델 ───────────────────────────

class ExecuteRequest(BaseModel):
    prompt: str
    context: dict[str, Any] | None = None


class ExecuteResponse(BaseModel):
    execution_id: str
    session_id: str
    tx_id: str
    agent: str
    result: str
    success: bool
    error: str | None = None
    cost_usd: float
    duration_ms: int
    model: str


# ─── 글로벌 Context ──────────────────────────────────

_ctx: AgentApplicationContext | None = None


def get_context() -> AgentApplicationContext:
    if _ctx is None:
        raise RuntimeError("AgentApplicationContext가 시작되지 않았습니다")
    return _ctx


# ─── FastAPI 앱 ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 Context lifecycle 관리."""
    # startup은 create_app에서 이미 처리
    yield
    if _ctx:
        await _ctx.shutdown()


def create_app(ctx: AgentApplicationContext) -> FastAPI:
    """FastAPI 앱 생성 — Context 주입."""
    global _ctx
    _ctx = ctx

    app = FastAPI(
        title="Agent Application Context",
        version="0.1.0",
        description="Spring-inspired IoC/DI/AOP for AI Agents",
        lifespan=lifespan,
    )

    # ─── Routes ───────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status():
        """Context 전체 상태 (FR-9.1)."""
        return get_context().get_status()

    @app.get("/api/agents")
    async def list_agents():
        """Agent 목록 — tools_loaded_count, skills 포함 (AC-2)."""
        return get_context().list_agents()

    @app.get("/api/agents/{name}")
    async def get_agent(name: str):
        """Agent 상세 정보."""
        try:
            agent = get_context().get_agent(name)
            return agent.to_detail()
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/agents/{name}/execute", response_model=ExecuteResponse)
    async def execute_agent(name: str, request: ExecuteRequest):
        """Agent 실행 (FR-9.2)."""
        try:
            result = await get_context().execute(
                name,
                request.prompt,
                context=request.context,
            )
            return result
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/tools")
    async def list_tools():
        """Tool 목록."""
        return get_context().tool_registry.list_all()

    @app.get("/api/skills")
    async def list_skills():
        """Skill 목록."""
        return get_context().skill_registry.list_all()

    return app


async def start_server(
    resources_dir: str = "./resources",
    host: str = "127.0.0.1",
    port: int = 8800,
    *,
    strict_tools: bool = False,
) -> None:
    """AAC 서버 시작 — Context 기동 + FastAPI 서버 실행."""
    ctx = AgentApplicationContext(
        resources_dir=resources_dir,
        strict_tools=strict_tools,
    )
    await ctx.start()

    app = create_app(ctx)

    boot_log(f"🌐 HTTP: http://{host}:{port}")
    boot_log(f"📡 WS: ws://{host}:{port}/ws/events")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()
