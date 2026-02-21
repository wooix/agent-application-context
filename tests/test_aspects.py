"""AspectEngine + 구체적 Aspect Handler 단위 테스트 (Phase 3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aac.aspects.audit_logging import AuditLoggingHandler
from aac.aspects.engine import (
    AspectContext,
    AspectEngine,
    AspectEventType,
    AspectHandler,
)
from aac.aspects.execution_logging import ExecutionLoggingHandler
from aac.aspects.tool_tracking import ToolTrackingHandler
from aac.models.manifest import (
    AspectManifest,
    AspectMetadata,
    AspectPointcut,
    AspectSpec,
)


def _make_aspect_manifest(
    name: str,
    aspect_type: str = "TestAspect",
    order: int = 100,
    events: list[str] | None = None,
    agents: list[str] | None = None,
    config: dict | None = None,
) -> AspectManifest:
    """테스트용 AspectManifest 생성 헬퍼."""
    return AspectManifest(
        metadata=AspectMetadata(name=name),
        spec=AspectSpec(
            type=aspect_type,
            order=order,
            pointcut=AspectPointcut(
                events=events or [],
                agents=agents or [],
            ),
            config=config or {},
        ),
    )


def _make_ctx(
    agent_name: str = "test-agent",
    session_id: str = "sess_test",
    tx_id: str = "tx_001",
    execution_id: str = "exec_test",
    prompt: str = "테스트 프롬프트",
) -> AspectContext:
    return AspectContext(
        agent_name=agent_name,
        session_id=session_id,
        tx_id=tx_id,
        execution_id=execution_id,
        prompt=prompt,
    )


# ─── AspectEngine ─────────────────────────────────────


class TestAspectEngine:
    """AspectEngine 기본 동작."""

    def test_handler_등록(self) -> None:
        engine = AspectEngine()
        manifest = _make_aspect_manifest("test", events=["PreQuery"])

        engine.register(manifest)

        assert engine.handler_count == 1

    def test_order_정렬(self) -> None:
        """낮은 order가 먼저 실행되어야 한다."""
        engine = AspectEngine()
        engine.register(_make_aspect_manifest("high", order=100))
        engine.register(_make_aspect_manifest("low", order=10))
        engine.register(_make_aspect_manifest("mid", order=50))

        handlers = engine.list_handlers()
        orders = [h["order"] for h in handlers]

        assert orders == [10, 50, 100]

    async def test_이벤트_매칭(self) -> None:
        """pointcut.events에 매칭되는 handler만 실행되어야 한다."""
        engine = AspectEngine()
        calls: list[str] = []

        class TrackingHandler(AspectHandler):
            async def handle(self, event_type: str, ctx: AspectContext) -> None:
                calls.append(f"{self.name}:{event_type}")

        engine.register_handler_type("Tracking", TrackingHandler)
        engine.register(
            _make_aspect_manifest("pre-only", aspect_type="Tracking", events=["PreQuery"])
        )
        engine.register(
            _make_aspect_manifest("post-only", aspect_type="Tracking", events=["PostQuery"])
        )

        ctx = _make_ctx()
        await engine.apply(AspectEventType.PRE_QUERY, ctx)

        assert "pre-only:PreQuery" in calls
        assert "post-only:PreQuery" not in calls

    async def test_agent_필터(self) -> None:
        """pointcut.agents에 매칭되지 않는 agent는 건너뛰어야 한다."""
        engine = AspectEngine()
        calls: list[str] = []

        class TrackingHandler(AspectHandler):
            async def handle(self, event_type: str, ctx: AspectContext) -> None:
                calls.append(ctx.agent_name)

        engine.register_handler_type("Tracking", TrackingHandler)
        engine.register(
            _make_aspect_manifest(
                "agent-filter",
                aspect_type="Tracking",
                events=["PreQuery"],
                agents=["claude-coder"],
            )
        )

        await engine.apply(AspectEventType.PRE_QUERY, _make_ctx(agent_name="claude-coder"))
        await engine.apply(AspectEventType.PRE_QUERY, _make_ctx(agent_name="gemini-critic"))

        assert calls == ["claude-coder"]

    async def test_빈_events_전부_매칭(self) -> None:
        """events가 비어있으면 모든 이벤트에 매칭되어야 한다."""
        engine = AspectEngine()
        calls: list[str] = []

        class TrackingHandler(AspectHandler):
            async def handle(self, event_type: str, ctx: AspectContext) -> None:
                calls.append(event_type)

        engine.register_handler_type("Tracking", TrackingHandler)
        engine.register(_make_aspect_manifest("all-events", aspect_type="Tracking", events=[]))

        ctx = _make_ctx()
        await engine.apply(AspectEventType.PRE_QUERY, ctx)
        await engine.apply(AspectEventType.POST_QUERY, ctx)

        assert len(calls) == 2

    async def test_handler_에러_격리(self) -> None:
        """handler 에러가 다른 handler 실행을 막지 않아야 한다."""
        engine = AspectEngine()
        calls: list[str] = []

        class ErrorHandler(AspectHandler):
            async def handle(self, event_type: str, ctx: AspectContext) -> None:
                raise RuntimeError("의도된 에러")

        class OkHandler(AspectHandler):
            async def handle(self, event_type: str, ctx: AspectContext) -> None:
                calls.append("ok")

        engine.register_handler_type("Error", ErrorHandler)
        engine.register_handler_type("Ok", OkHandler)
        engine.register(_make_aspect_manifest("err", aspect_type="Error", order=1))
        engine.register(_make_aspect_manifest("ok", aspect_type="Ok", order=2))

        await engine.apply(AspectEventType.PRE_QUERY, _make_ctx())

        assert calls == ["ok"]


# ─── AuditLoggingHandler ─────────────────────────────


class TestAuditLoggingHandler:
    """AuditLoggingHandler — SQLite 감사 로그."""

    async def test_PreQuery_레코드_생성(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "audit.db")
        manifest = _make_aspect_manifest(
            "audit",
            aspect_type="AuditLoggingAspect",
            events=["PreQuery", "PostQuery"],
            config={"db_path": db_path},
        )
        handler = AuditLoggingHandler(manifest)

        ctx = _make_ctx()
        await handler.handle(AspectEventType.PRE_QUERY, ctx)

        # DB 직접 검증
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT * FROM executions").fetchall()
        conn.close()
        handler.close()

        assert len(rows) == 1
        assert rows[0][0] == "exec_test"  # id

        # status 컬럼 확인 (name 기반 조회)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT * FROM executions").fetchone()
        conn2.close()
        assert row["status"] == "running"

    async def test_PostQuery_결과_업데이트(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "audit.db")
        manifest = _make_aspect_manifest(
            "audit",
            aspect_type="AuditLoggingAspect",
            config={"db_path": db_path},
        )
        handler = AuditLoggingHandler(manifest)

        ctx = _make_ctx()
        await handler.handle(AspectEventType.PRE_QUERY, ctx)

        ctx.response = "테스트 응답"
        ctx.cost_usd = 0.005
        ctx.duration_ms = 200
        ctx.model = "test-model"
        await handler.handle(AspectEventType.POST_QUERY, ctx)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT status, cost_usd, duration_ms FROM executions").fetchone()
        conn.close()
        handler.close()

        assert row[0] == "completed"
        assert row[1] == 0.005
        assert row[2] == 200

    async def test_OnError_에러_기록(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "audit.db")
        manifest = _make_aspect_manifest(
            "audit",
            aspect_type="AuditLoggingAspect",
            config={"db_path": db_path},
        )
        handler = AuditLoggingHandler(manifest)

        ctx = _make_ctx()
        await handler.handle(AspectEventType.PRE_QUERY, ctx)

        ctx.error = "실행 실패"
        await handler.handle(AspectEventType.ON_ERROR, ctx)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT status, error FROM executions").fetchone()
        conn.close()
        handler.close()

        assert row[0] == "error"
        assert row[1] == "실행 실패"


# ─── ToolTrackingHandler ─────────────────────────────


class TestToolTrackingHandler:
    """ToolTrackingHandler — Tool 사용 통계."""

    async def test_호출_횟수_추적(self) -> None:
        manifest = _make_aspect_manifest(
            "tracking",
            aspect_type="ToolTrackingAspect",
            events=["PreToolUse", "PostToolUse"],
        )
        handler = ToolTrackingHandler(manifest)

        ctx = _make_ctx()
        ctx.tool_name = "Read"
        await handler.handle(AspectEventType.PRE_TOOL_USE, ctx)
        await handler.handle(AspectEventType.POST_TOOL_USE, ctx)

        stats = handler.get_stats("test-agent")

        assert stats["Read"]["call_count"] == 1
        assert stats["Read"]["success_count"] == 1

    async def test_에러_카운트(self) -> None:
        manifest = _make_aspect_manifest("tracking", aspect_type="ToolTrackingAspect")
        handler = ToolTrackingHandler(manifest)

        ctx = _make_ctx()
        ctx.tool_name = "Write"
        await handler.handle(AspectEventType.PRE_TOOL_USE, ctx)

        ctx.error = "권한 오류"
        await handler.handle(AspectEventType.POST_TOOL_USE, ctx)

        stats = handler.get_stats("test-agent")

        assert stats["Write"]["error_count"] == 1
        assert stats["Write"]["success_count"] == 0

    async def test_tool_name_없으면_무시(self) -> None:
        manifest = _make_aspect_manifest("tracking", aspect_type="ToolTrackingAspect")
        handler = ToolTrackingHandler(manifest)

        ctx = _make_ctx()
        ctx.tool_name = None
        await handler.handle(AspectEventType.PRE_TOOL_USE, ctx)

        stats = handler.get_stats("test-agent")
        assert len(stats) == 0


# ─── ExecutionLoggingHandler ─────────────────────────


class TestExecutionLoggingHandler:
    """ExecutionLoggingHandler — 실행 로그 출력."""

    async def test_PreQuery_출력(self, capsys) -> None:
        manifest = _make_aspect_manifest(
            "exec-log",
            aspect_type="ExecutionLoggingAspect",
            events=["PreQuery"],
        )
        handler = ExecutionLoggingHandler(manifest)

        ctx = _make_ctx()
        await handler.handle(AspectEventType.PRE_QUERY, ctx)

        captured = capsys.readouterr()
        assert "[ASPECT] PreQuery" in captured.out
        assert "test-agent" in captured.out

    async def test_PostQuery_출력(self, capsys) -> None:
        manifest = _make_aspect_manifest(
            "exec-log",
            aspect_type="ExecutionLoggingAspect",
        )
        handler = ExecutionLoggingHandler(manifest)

        ctx = _make_ctx()
        ctx.duration_ms = 500
        ctx.cost_usd = 0.01
        await handler.handle(AspectEventType.POST_QUERY, ctx)

        captured = capsys.readouterr()
        assert "PostQuery" in captured.out
        assert "500ms" in captured.out
