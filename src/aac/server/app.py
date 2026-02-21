"""FastAPI 서버 — AAC HTTP API (FR-9.1~9.3).

Spring Boot의 embedded Tomcat에 해당하며,
AgentApplicationContext를 기반으로 REST API와 WebSocket을 제공한다.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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


class AsyncExecuteResponse(BaseModel):
    execution_id: str
    status: str
    poll_url: str


class ExecutionStatus(BaseModel):
    execution_id: str
    agent: str
    status: str
    result: str | None = None
    error: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    model: str = ""
    created_at: str | None = None
    completed_at: str | None = None


# ─── WebSocket 연결 관리 (Issue #16) ──────────────────

class ConnectionManager:
    """WebSocket 연결 관리자."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)

    async def broadcast(self, data: dict[str, Any]) -> None:
        """모든 연결에 이벤트 전송."""
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# ─── 글로벌 Context ──────────────────────────────────

_ctx: AgentApplicationContext | None = None
_ws_manager = ConnectionManager()


def get_context() -> AgentApplicationContext:
    if _ctx is None:
        raise RuntimeError("AgentApplicationContext가 시작되지 않았습니다")
    return _ctx


def get_ws_manager() -> ConnectionManager:
    return _ws_manager


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

    @app.post("/api/agents/{name}/execute")
    async def execute_agent(
        name: str,
        request: ExecuteRequest,
        req: Request,
        async_mode: bool = Query(False, alias="async"),
    ):
        """Agent 실행 (FR-9.2, DR-5).

        - 기본: 동기 응답
        - Accept: text/event-stream → SSE 스트리밍
        - ?async=true → 202 + 폴링
        """
        context = get_context()
        accept = req.headers.get("accept", "")

        try:
            # DR-5: ?async=true → 비동기 실행
            if async_mode:
                execution_id = await context.execute_async(
                    name, request.prompt, context=request.context,
                )
                return AsyncExecuteResponse(
                    execution_id=execution_id,
                    status="running",
                    poll_url=f"/api/executions/{execution_id}",
                )

            # DR-5: Accept: text/event-stream → SSE 스트리밍
            if "text/event-stream" in accept:
                return EventSourceResponse(
                    _sse_generator(context, name, request.prompt, request.context)
                )

            # 기본: 동기 응답
            result = await context.execute(
                name, request.prompt, context=request.context,
            )
            return result

        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ─── 비동기 실행 폴링 (Issue #15) ─────────

    @app.get("/api/executions/{execution_id}", response_model=ExecutionStatus)
    async def get_execution(execution_id: str):
        """실행 상태 조회."""
        try:
            return get_context().get_execution(execution_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.delete("/api/executions/{execution_id}")
    async def cancel_execution(execution_id: str):
        """실행 취소."""
        context = get_context()
        # 존재 여부 먼저 확인
        try:
            context.get_execution(execution_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

        cancelled = await context.cancel_execution(execution_id)
        if cancelled:
            return {"status": "cancelled", "execution_id": execution_id}
        return {"status": "not_cancellable", "execution_id": execution_id}

    # ─── Tool / Skill ─────────────────────────

    @app.get("/api/tools")
    async def list_tools():
        """Tool 목록."""
        return get_context().tool_registry.list_all()

    @app.get("/api/skills")
    async def list_skills():
        """Skill 목록."""
        return get_context().skill_registry.list_all()

    # ─── WebSocket (Issue #16) ────────────────

    @app.websocket("/ws/events")
    async def websocket_events(ws: WebSocket):
        """실시간 이벤트 스트림."""
        manager = get_ws_manager()
        await manager.connect(ws)
        try:
            while True:
                # 클라이언트 메시지 수신 대기 (keepalive)
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            manager.disconnect(ws)

    return app


# ─── SSE 헬퍼 ────────────────────────────────────────

async def _sse_generator(
    ctx: AgentApplicationContext,
    agent_name: str,
    prompt: str,
    context: dict[str, Any] | None,
):
    """StreamChunk → SSE 이벤트 변환 제너레이터."""
    async for chunk in ctx.stream_execute(agent_name, prompt, context=context):
        yield {
            "event": chunk.type,
            "data": json.dumps({
                "type": chunk.type,
                "content": chunk.content,
                "tool_name": chunk.tool_name,
                "tool_input": chunk.tool_input,
                "metadata": chunk.metadata,
            }, ensure_ascii=False),
        }


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
