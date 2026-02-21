"""Phase 7 — Lifecycle Manager 테스트.

상태 전이 검증, 건강 검사, 우아한 종료, 이벤트 히스토리,
콜백 등핵심 생명주기 기능 테스트.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aac.lifecycle.manager import (
    HealthCheckResult,
    LifecycleEvent,
    LifecycleManager,
    VALID_TRANSITIONS,
)
from aac.models.instance import AgentInstance, AgentStatus


# ─── 헬퍼 ─────────────────────────────────────────────


def _make_agent(
    name: str = "test-agent",
    status: AgentStatus = AgentStatus.REGISTERED,
    **kwargs,
) -> AgentInstance:
    return AgentInstance(name=name, status=status, **kwargs)


# ─── 상태 전이 테스트 ─────────────────────────────────


class TestStateTransition:
    """상태 전이 검증 테스트."""

    def test_정상_전이_REGISTERED_to_INITIALIZING(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)
        event = mgr.transition(agent, AgentStatus.INITIALIZING)

        assert agent.status == AgentStatus.INITIALIZING
        assert event.old_status == AgentStatus.REGISTERED
        assert event.new_status == AgentStatus.INITIALIZING
        assert event.agent_name == "test-agent"

    def test_정상_전이_INITIALIZING_to_READY(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)
        assert agent.status == AgentStatus.READY

    def test_정상_전이_READY_to_EXECUTING(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.READY)
        mgr.transition(agent, AgentStatus.EXECUTING)
        assert agent.status == AgentStatus.EXECUTING

    def test_정상_전이_EXECUTING_to_READY(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.EXECUTING)
        mgr.transition(agent, AgentStatus.READY)
        assert agent.status == AgentStatus.READY

    def test_에러_전이(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.READY)
        event = mgr.transition(
            agent, AgentStatus.ERROR, error="runtime crash",
        )
        assert agent.status == AgentStatus.ERROR
        assert event.error == "runtime crash"

    def test_LAZY_to_INITIALIZING(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.LAZY)
        mgr.transition(agent, AgentStatus.INITIALIZING)
        assert agent.status == AgentStatus.INITIALIZING

    def test_무효_전이_예외(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)
        with pytest.raises(ValueError, match="전이 불가"):
            mgr.transition(agent, AgentStatus.EXECUTING)

    def test_DESTROYED_전이_불가(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.DESTROYED)
        with pytest.raises(ValueError):
            mgr.transition(agent, AgentStatus.READY)

    def test_전체_라이프사이클(self) -> None:
        """REGISTERED → INIT → READY → EXEC → READY → DESTROY → DESTROYED."""
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)

        mgr.transition(agent, AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)
        mgr.transition(agent, AgentStatus.EXECUTING)
        mgr.transition(agent, AgentStatus.READY)
        mgr.transition(agent, AgentStatus.DESTROYING)
        mgr.transition(agent, AgentStatus.DESTROYED)

        assert agent.status == AgentStatus.DESTROYED

    def test_에러_복구(self) -> None:
        """ERROR → INITIALIZING → READY."""
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.ERROR)
        mgr.transition(agent, AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)
        assert agent.status == AgentStatus.READY


# ─── 이벤트 히스토리 테스트 ───────────────────────────


class TestEventHistory:
    """이벤트 기록 및 조회 테스트."""

    def test_이벤트_기록(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)
        mgr.transition(agent, AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)

        events = mgr.get_events()
        assert len(events) == 2
        assert events[0]["old_status"] == "REGISTERED"
        assert events[0]["new_status"] == "INITIALIZING"

    def test_에이전트_필터(self) -> None:
        mgr = LifecycleManager()
        a1 = _make_agent("agent-a", AgentStatus.REGISTERED)
        a2 = _make_agent("agent-b", AgentStatus.REGISTERED)
        mgr.transition(a1, AgentStatus.INITIALIZING)
        mgr.transition(a2, AgentStatus.INITIALIZING)
        mgr.transition(a1, AgentStatus.READY)

        events = mgr.get_events(agent_name="agent-a")
        assert len(events) == 2
        assert all(e["agent"] == "agent-a" for e in events)

    def test_limit(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)
        mgr.transition(agent, AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)
        mgr.transition(agent, AgentStatus.EXECUTING)

        events = mgr.get_events(limit=2)
        assert len(events) == 2

    def test_이벤트_직렬화(self) -> None:
        event = LifecycleEvent(
            agent_name="test",
            old_status=AgentStatus.READY,
            new_status=AgentStatus.EXECUTING,
        )
        d = event.to_dict()
        assert d["agent"] == "test"
        assert "timestamp" in d


# ─── 콜백 테스트 ──────────────────────────────────────


class TestCallbacks:
    """생명주기 콜백 테스트."""

    def test_콜백_호출(self) -> None:
        mgr = LifecycleManager()
        events_received: list[LifecycleEvent] = []
        mgr.add_callback(events_received.append)

        agent = _make_agent(status=AgentStatus.REGISTERED)
        mgr.transition(agent, AgentStatus.INITIALIZING)

        assert len(events_received) == 1
        assert events_received[0].new_status == AgentStatus.INITIALIZING

    def test_다중_콜백(self) -> None:
        mgr = LifecycleManager()
        count = [0]

        def cb1(_: LifecycleEvent) -> None:
            count[0] += 1

        def cb2(_: LifecycleEvent) -> None:
            count[0] += 10

        mgr.add_callback(cb1)
        mgr.add_callback(cb2)

        agent = _make_agent(status=AgentStatus.REGISTERED)
        mgr.transition(agent, AgentStatus.INITIALIZING)

        assert count[0] == 11

    def test_콜백_에러_무시(self) -> None:
        mgr = LifecycleManager()
        mgr.add_callback(lambda _: 1 / 0)  # 에러 발생하는 콜백

        agent = _make_agent(status=AgentStatus.REGISTERED)
        # 에러가 발생해도 전이는 성공해야 함
        mgr.transition(agent, AgentStatus.INITIALIZING)
        assert agent.status == AgentStatus.INITIALIZING


# ─── 건강 검사 테스트 ─────────────────────────────────


class TestHealthCheck:
    """건강 검사 테스트."""

    def test_READY_건강(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(
            status=AgentStatus.READY, query_count=5,
        )
        result = mgr.check_health(agent)

        assert result.healthy is True
        assert result.status == "READY"
        assert result.details["query_count"] == 5

    def test_EXECUTING_건강(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.EXECUTING)
        result = mgr.check_health(agent)
        assert result.healthy is True

    def test_ERROR_비건강(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.ERROR)
        result = mgr.check_health(agent)
        assert result.healthy is False
        assert "warning" in result.details

    def test_LAZY_비건강(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.LAZY)
        result = mgr.check_health(agent)
        assert result.healthy is False

    def test_전체_건강_검사(self) -> None:
        mgr = LifecycleManager()
        agents = {
            "a": _make_agent("a", AgentStatus.READY),
            "b": _make_agent("b", AgentStatus.ERROR),
            "c": _make_agent("c", AgentStatus.LAZY),
        }
        results = mgr.check_all_health(agents)
        assert len(results) == 3
        assert results["a"].healthy is True
        assert results["b"].healthy is False
        assert results["c"].healthy is False

    def test_결과_직렬화(self) -> None:
        result = HealthCheckResult(
            agent_name="test",
            healthy=True,
            status="READY",
        )
        d = result.to_dict()
        assert d["agent"] == "test"
        assert d["healthy"] is True


# ─── 우아한 종료 테스트 ───────────────────────────────


class TestGracefulShutdown:
    """우아한 종료 테스트."""

    async def test_READY_에이전트_종료(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.READY)
        runtime = MagicMock()
        runtime.shutdown = AsyncMock()
        agent.runtime = runtime

        events = await mgr.graceful_shutdown({"a": agent})

        assert agent.status == AgentStatus.DESTROYED
        assert len(events) == 2  # DESTROYING + DESTROYED
        runtime.shutdown.assert_called_once()

    async def test_LAZY_에이전트_스킵(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.LAZY)

        events = await mgr.graceful_shutdown({"a": agent})
        assert len(events) == 0
        assert agent.status == AgentStatus.LAZY

    async def test_DESTROYED_에이전트_스킵(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.DESTROYED)

        events = await mgr.graceful_shutdown({"a": agent})
        assert len(events) == 0

    async def test_런타임_종료_에러_핸들링(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.READY)
        runtime = MagicMock()
        runtime.shutdown = AsyncMock(side_effect=RuntimeError("fail"))
        agent.runtime = runtime

        events = await mgr.graceful_shutdown({"a": agent})
        # 런타임 에러에도 종료는 진행됨
        assert agent.status == AgentStatus.DESTROYED

    async def test_다중_에이전트_종료(self) -> None:
        mgr = LifecycleManager()
        agents = {
            "a": _make_agent("a", AgentStatus.READY),
            "b": _make_agent("b", AgentStatus.READY),
            "c": _make_agent("c", AgentStatus.LAZY),
        }
        for a in agents.values():
            if a.status != AgentStatus.LAZY:
                a.runtime = MagicMock()
                a.runtime.shutdown = AsyncMock()

        events = await mgr.graceful_shutdown(agents)
        assert agents["a"].status == AgentStatus.DESTROYED
        assert agents["b"].status == AgentStatus.DESTROYED
        assert agents["c"].status == AgentStatus.LAZY


# ─── 요약 통계 테스트 ─────────────────────────────────


class TestSummary:
    """요약 통계 테스트."""

    def test_요약(self) -> None:
        mgr = LifecycleManager()
        agents = {
            "a": _make_agent("a", AgentStatus.READY),
            "b": _make_agent("b", AgentStatus.READY),
            "c": _make_agent("c", AgentStatus.LAZY),
        }

        summary = mgr.get_summary(agents)
        assert summary["total_agents"] == 3
        assert summary["status_counts"]["READY"] == 2
        assert summary["status_counts"]["LAZY"] == 1

    def test_이벤트_카운트(self) -> None:
        mgr = LifecycleManager()
        agent = _make_agent(status=AgentStatus.REGISTERED)
        mgr.transition(agent, AgentStatus.INITIALIZING)
        mgr.transition(agent, AgentStatus.READY)

        summary = mgr.get_summary({"a": agent})
        assert summary["total_events"] == 2


# ─── 유효 전이 맵 완전성 테스트 ──────────────────────


class TestTransitionMap:
    """VALID_TRANSITIONS 맵의 완전성."""

    def test_모든_상태가_맵에_존재(self) -> None:
        for status in AgentStatus:
            assert status in VALID_TRANSITIONS, (
                f"{status.value}가 VALID_TRANSITIONS에 없음"
            )

    def test_DESTROYED는_전이_없음(self) -> None:
        assert VALID_TRANSITIONS[AgentStatus.DESTROYED] == set()
