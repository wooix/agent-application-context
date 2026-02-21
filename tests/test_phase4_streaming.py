"""Phase 4 테스트 — SSE 스트리밍, 비동기 실행, WebSocket (DR-5, FR-9.3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from aac.aspects.engine import AspectContext, AspectEngine, AspectEventType
from aac.aspects.ws_publisher import WebSocketPublisherHandler
from aac.context import AgentApplicationContext
from aac.models.events import (
    AACEvent,
    AgentStatusChangeEvent,
    QueryCompleteEvent,
    QueryStartEvent,
    ToolUseEvent,
)
from aac.models.manifest import AspectManifest, AspectMetadata, AspectPointcut, AspectSpec
from aac.runtime.base import StreamChunk
from aac.server.app import ConnectionManager, create_app

from tests.helpers import MockRuntime


# ─── Fixtures ────────────────────────────────────────


@pytest.fixture
async def ctx_with_agent(tmp_path):
    """MockRuntime agent가 등록된 Context."""
    # resources 디렉토리 구성
    import yaml

    resources = tmp_path / "resources"
    agents_dir = resources / "agents" / "test-agent"
    agents_dir.mkdir(parents=True)
    tools_dir = resources / "tools" / "test-tools"
    tools_dir.mkdir(parents=True)
    (resources / "skills").mkdir()
    (resources / "aspects").mkdir()
    (resources / "runtimes").mkdir()

    # tool.yaml
    (tools_dir / "tool.yaml").write_text(yaml.dump({
        "apiVersion": "aac/v1",
        "kind": "Tool",
        "metadata": {"name": "test-tools"},
        "spec": {"items": [{"name": "Read", "description": "파일 읽기"}]},
    }))

    # agent.yaml
    (agents_dir / "agent.yaml").write_text(yaml.dump({
        "apiVersion": "aac/v1",
        "kind": "Agent",
        "metadata": {"name": "test-agent", "description": "테스트"},
        "spec": {
            "runtime": "mock",
            "system_prompt": "테스트",
            "tools": [{"ref": "test-tools"}],
        },
    }))

    ctx = AgentApplicationContext(resources_dir=str(resources))
    ctx._runtime_registry.register("mock", MockRuntime)
    await ctx.start()
    yield ctx
    await ctx.shutdown()


@pytest.fixture
async def client(ctx_with_agent):
    """FastAPI HTTPX 테스트 클라이언트."""
    app = create_app(ctx_with_agent)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ─── StreamChunk 모델 ────────────────────────────────


class TestStreamChunk:
    """StreamChunk 데이터 모델."""

    def test_text_chunk(self) -> None:
        chunk = StreamChunk(type="text", content="hello")
        assert chunk.type == "text"
        assert chunk.content == "hello"

    def test_tool_call_chunk(self) -> None:
        chunk = StreamChunk(
            type="tool_call",
            tool_name="Read",
            tool_input={"path": "/tmp/test"},
        )
        assert chunk.tool_name == "Read"
        assert chunk.tool_input == {"path": "/tmp/test"}

    def test_done_chunk_with_metadata(self) -> None:
        chunk = StreamChunk(
            type="done",
            metadata={"cost_usd": 0.01, "duration_ms": 500},
        )
        assert chunk.metadata["cost_usd"] == 0.01

    def test_error_chunk(self) -> None:
        chunk = StreamChunk(type="error", content="실행 실패")
        assert chunk.type == "error"


# ─── MockRuntime Streaming ───────────────────────────


class TestMockRuntimeStreaming:
    """MockRuntime의 stream() 메서드."""

    async def test_stream_yields_chunks(self) -> None:
        runtime = MockRuntime()
        await runtime.initialize({})

        chunks = []
        async for chunk in runtime.stream("테스트"):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].type == "text"
        assert "mock streaming" in chunks[0].content
        assert chunks[1].type == "done"


# ─── Context stream_execute ──────────────────────────


class TestContextStreamExecute:
    """AgentApplicationContext.stream_execute()."""

    async def test_stream_execute_yields_chunks(self, ctx_with_agent) -> None:
        chunks = []
        async for chunk in ctx_with_agent.stream_execute("test-agent", "스트리밍 테스트"):
            chunks.append(chunk)

        # meta + text + done = 최소 3개
        assert len(chunks) >= 3
        types = [c.type for c in chunks]
        assert "text" in types
        assert "done" in types

    async def test_stream_execute_meta_chunk(self, ctx_with_agent) -> None:
        """첫 번째 청크에 execution_id, session_id, tx_id가 포함되어야 한다."""
        first_chunk = None
        async for chunk in ctx_with_agent.stream_execute("test-agent", "메타 테스트"):
            first_chunk = chunk
            break

        assert first_chunk is not None
        assert "execution_id" in first_chunk.metadata
        assert "session_id" in first_chunk.metadata
        assert "tx_id" in first_chunk.metadata

    async def test_stream_execute_missing_agent(self, ctx_with_agent) -> None:
        """존재하지 않는 agent 스트리밍 시 에러."""
        with pytest.raises(KeyError):
            async for _ in ctx_with_agent.stream_execute("없는-agent", "테스트"):
                pass

    async def test_stream_execute_updates_stats(self, ctx_with_agent) -> None:
        """스트리밍 후 agent 통계가 업데이트되어야 한다."""
        async for _ in ctx_with_agent.stream_execute("test-agent", "통계 테스트"):
            pass

        agent = ctx_with_agent.get_agent("test-agent")
        assert agent.query_count >= 1


# ─── SSE Endpoint ────────────────────────────────────


class TestSSEEndpoint:
    """POST /api/agents/{name}/execute with Accept: text/event-stream."""

    async def test_sse_response(self, client) -> None:
        """SSE 헤더로 요청 시 스트리밍 응답이 와야 한다."""
        resp = await client.post(
            "/api/agents/test-agent/execute",
            json={"prompt": "SSE 테스트"},
            headers={"accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    async def test_sync_response(self, client) -> None:
        """일반 요청 시 동기 JSON 응답이 와야 한다."""
        resp = await client.post(
            "/api/agents/test-agent/execute",
            json={"prompt": "동기 테스트"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "execution_id" in data
        assert data["success"] is True

    async def test_execute_404(self, client) -> None:
        """존재하지 않는 agent 실행 시 404."""
        resp = await client.post(
            "/api/agents/없는-agent/execute",
            json={"prompt": "테스트"},
        )
        assert resp.status_code == 404


# ─── Async Execution + Polling (Issue #15) ───────────


class TestAsyncExecution:
    """?async=true 비동기 실행 + 폴링."""

    async def test_async_returns_202_equivalent(self, client) -> None:
        """async=true 시 즉시 실행 상태 반환."""
        resp = await client.post(
            "/api/agents/test-agent/execute?async=true",
            json={"prompt": "비동기 테스트"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "execution_id" in data
        assert "poll_url" in data

    async def test_poll_execution_status(self, ctx_with_agent) -> None:
        """실행 상태 폴링."""
        exec_id = await ctx_with_agent.execute_async("test-agent", "폴링 테스트")
        assert exec_id.startswith("exec_")

        # 실행 완료 대기
        for _ in range(20):
            status = ctx_with_agent.get_execution(exec_id)
            if status["status"] != "running":
                break
            await asyncio.sleep(0.1)

        status = ctx_with_agent.get_execution(exec_id)
        assert status["status"] in ("completed", "error")

    async def test_cancel_execution(self, ctx_with_agent) -> None:
        """실행 취소."""
        # 느린 runtime 시뮬레이션을 위해 직접 execution 상태 등록
        exec_id = "exec_cancel_test"
        ctx_with_agent._executions[exec_id] = {
            "execution_id": exec_id,
            "agent": "test-agent",
            "status": "running",
        }

        async def _slow_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(_slow_task())
        ctx_with_agent._execution_tasks[exec_id] = task

        cancelled = await ctx_with_agent.cancel_execution(exec_id)
        assert cancelled is True
        assert ctx_with_agent._executions[exec_id]["status"] == "cancelled"

    async def test_get_execution_not_found(self, ctx_with_agent) -> None:
        """존재하지 않는 execution 조회 시 KeyError."""
        with pytest.raises(KeyError):
            ctx_with_agent.get_execution("exec_없음")

    async def test_poll_endpoint(self, client) -> None:
        """GET /api/executions/{id} 엔드포인트."""
        # 먼저 비동기 실행
        resp = await client.post(
            "/api/agents/test-agent/execute?async=true",
            json={"prompt": "폴링 엔드포인트 테스트"},
        )
        exec_id = resp.json()["execution_id"]

        # 완료 대기
        for _ in range(20):
            poll_resp = await client.get(f"/api/executions/{exec_id}")
            if poll_resp.json()["status"] != "running":
                break
            await asyncio.sleep(0.1)

        poll_resp = await client.get(f"/api/executions/{exec_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] in ("completed", "error")

    async def test_cancel_endpoint(self, client) -> None:
        """DELETE /api/executions/{id} 엔드포인트."""
        resp = await client.delete("/api/executions/exec_없음")
        assert resp.status_code == 404


# ─── ConnectionManager (Issue #16) ───────────────────


class TestConnectionManager:
    """WebSocket ConnectionManager."""

    def test_initial_count(self) -> None:
        manager = ConnectionManager()
        assert manager.connection_count == 0


# ─── WebSocket Publisher Aspect ──────────────────────


class TestWebSocketPublisher:
    """WebSocketPublisherHandler — 이벤트 발행."""

    async def test_PreQuery_이벤트_발행(self) -> None:
        """PreQuery → QueryStartEvent가 broadcast되어야 한다."""
        published: list[dict] = []

        async def mock_broadcast(data: dict[str, Any]) -> None:
            published.append(data)

        manifest = AspectManifest(
            metadata=AspectMetadata(name="ws-pub"),
            spec=AspectSpec(
                type="WebSocketPublisher",
                order=999,
                pointcut=AspectPointcut(events=[]),
            ),
        )
        handler = WebSocketPublisherHandler(manifest)
        handler.set_broadcast(mock_broadcast)

        ctx = AspectContext(
            agent_name="test-agent",
            session_id="sess_test",
            tx_id="tx_001",
            execution_id="exec_test",
            prompt="테스트 쿼리",
        )
        await handler.handle("PreQuery", ctx)

        assert len(published) == 1
        assert published[0]["type"] == "query_start"
        assert published[0]["payload"]["agent"] == "test-agent"

    async def test_PostQuery_이벤트_발행(self) -> None:
        published: list[dict] = []

        async def mock_broadcast(data: dict[str, Any]) -> None:
            published.append(data)

        manifest = AspectManifest(
            metadata=AspectMetadata(name="ws-pub"),
            spec=AspectSpec(
                type="WebSocketPublisher",
                order=999,
                pointcut=AspectPointcut(events=[]),
            ),
        )
        handler = WebSocketPublisherHandler(manifest)
        handler.set_broadcast(mock_broadcast)

        ctx = AspectContext(
            agent_name="test-agent",
            session_id="sess_test",
            tx_id="tx_001",
            execution_id="exec_test",
            cost_usd=0.01,
            duration_ms=500,
            model="test-model",
        )
        await handler.handle("PostQuery", ctx)

        assert len(published) == 1
        assert published[0]["type"] == "query_complete"
        assert published[0]["payload"]["cost_usd"] == 0.01

    async def test_ToolUse_이벤트_발행(self) -> None:
        published: list[dict] = []

        async def mock_broadcast(data: dict[str, Any]) -> None:
            published.append(data)

        manifest = AspectManifest(
            metadata=AspectMetadata(name="ws-pub"),
            spec=AspectSpec(
                type="WebSocketPublisher",
                order=999,
                pointcut=AspectPointcut(events=[]),
            ),
        )
        handler = WebSocketPublisherHandler(manifest)
        handler.set_broadcast(mock_broadcast)

        ctx = AspectContext(
            agent_name="test-agent",
            session_id="sess_test",
            tx_id="tx_001",
            tool_name="Read",
        )
        await handler.handle("PreToolUse", ctx)

        assert len(published) == 1
        assert published[0]["type"] == "tool_use"
        assert published[0]["payload"]["tool_name"] == "Read"
        assert published[0]["payload"]["phase"] == "pre"

    async def test_OnError_이벤트_발행(self) -> None:
        published: list[dict] = []

        async def mock_broadcast(data: dict[str, Any]) -> None:
            published.append(data)

        manifest = AspectManifest(
            metadata=AspectMetadata(name="ws-pub"),
            spec=AspectSpec(
                type="WebSocketPublisher",
                order=999,
                pointcut=AspectPointcut(events=[]),
            ),
        )
        handler = WebSocketPublisherHandler(manifest)
        handler.set_broadcast(mock_broadcast)

        ctx = AspectContext(
            agent_name="test-agent",
            session_id="sess_test",
            tx_id="tx_001",
            error="테스트 에러",
        )
        await handler.handle("OnError", ctx)

        assert len(published) == 1
        assert published[0]["type"] == "agent_status_change"
        assert published[0]["payload"]["error"] == "테스트 에러"

    async def test_broadcast_미설정시_무시(self) -> None:
        """broadcast 함수가 설정되지 않으면 아무 것도 하지 않아야 한다."""
        manifest = AspectManifest(
            metadata=AspectMetadata(name="ws-pub"),
            spec=AspectSpec(
                type="WebSocketPublisher",
                order=999,
                pointcut=AspectPointcut(events=[]),
            ),
        )
        handler = WebSocketPublisherHandler(manifest)

        ctx = AspectContext(
            agent_name="test-agent",
            session_id="sess_test",
            tx_id="tx_001",
        )
        # broadcast 미설정 — 예외 없이 통과해야 한다
        await handler.handle("PreQuery", ctx)


# ─── Event Models ────────────────────────────────────


class TestEventModels:
    """AACEvent 이벤트 모델."""

    def test_AACEvent_기본_필드(self) -> None:
        event = AACEvent(type="test")
        assert event.schema_version == "1.0"
        assert event.event_id.startswith("evt_")
        assert event.type == "test"

    def test_QueryStartEvent(self) -> None:
        event = QueryStartEvent(
            session_id="sess_1",
            tx_id="tx_001",
            payload={"agent": "test"},
        )
        assert event.type == "query_start"

    def test_QueryCompleteEvent(self) -> None:
        event = QueryCompleteEvent(payload={"success": True})
        assert event.type == "query_complete"

    def test_ToolUseEvent(self) -> None:
        event = ToolUseEvent(payload={"tool": "Read"})
        assert event.type == "tool_use"

    def test_AgentStatusChangeEvent(self) -> None:
        event = AgentStatusChangeEvent(payload={"status": "error"})
        assert event.type == "agent_status_change"

    def test_event_serialization(self) -> None:
        """이벤트가 JSON 직렬화 가능해야 한다."""
        event = QueryStartEvent(
            session_id="sess_1",
            tx_id="tx_001",
            payload={"agent": "test", "prompt": "hello"},
        )
        data = event.model_dump()
        assert isinstance(data, dict)
        assert data["type"] == "query_start"
        assert "event_id" in data
