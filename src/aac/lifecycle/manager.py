"""Lifecycle Manager — Agent 생명주기 관리 (Phase 7).

Spring의 BeanPostProcessor + DisposableBean + @PostConstruct 에 해당하며,
Agent 인스턴스의 상태 전이, 건강 검사, 우아한 종료를 관리한다.

상태 전이:
  REGISTERED → INITIALIZING → READY ⇄ EXECUTING → DESTROYING → DESTROYED
                                    → ERROR
  LAZY → (on demand) → INITIALIZING → READY
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from aac.logging.formatter import aac_log
from aac.models.instance import AgentInstance, AgentStatus

logger = structlog.get_logger()


# ─── 이벤트 타입 ──────────────────────────────────────


@dataclass
class LifecycleEvent:
    """생명주기 이벤트 — 콜백/로깅용."""

    agent_name: str
    old_status: AgentStatus
    new_status: AgentStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent_name,
            "old_status": self.old_status.value,
            "new_status": self.new_status.value,
            "timestamp": self.timestamp.isoformat(),
            "error": self.error,
        }


# ─── 건강 검사 결과 ───────────────────────────────────


@dataclass
class HealthCheckResult:
    """Agent 건강 검사 결과."""

    agent_name: str
    healthy: bool
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent_name,
            "healthy": self.healthy,
            "status": self.status,
            "details": self.details,
            "checked_at": self.checked_at.isoformat(),
        }


# ─── Lifecycle Manager ────────────────────────────────


LifecycleCallback = Callable[[LifecycleEvent], Any]


# 유효한 상태 전이 맵
VALID_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.REGISTERED: {AgentStatus.INITIALIZING, AgentStatus.LAZY},
    AgentStatus.LAZY: {AgentStatus.INITIALIZING},
    AgentStatus.INITIALIZING: {AgentStatus.READY, AgentStatus.ERROR},
    AgentStatus.READY: {
        AgentStatus.EXECUTING,
        AgentStatus.DESTROYING,
        AgentStatus.ERROR,
    },
    AgentStatus.EXECUTING: {
        AgentStatus.READY,
        AgentStatus.ERROR,
        AgentStatus.DESTROYING,
    },
    AgentStatus.ERROR: {AgentStatus.INITIALIZING, AgentStatus.DESTROYING},
    AgentStatus.DESTROYING: {AgentStatus.DESTROYED, AgentStatus.ERROR},
    AgentStatus.DESTROYED: set(),
}


class LifecycleManager:
    """Agent 생명주기 관리자.

    Spring의 BeanFactory + BeanPostProcessor를 합친 역할.
    - 상태 전이 검증
    - 건강 검사
    - 우아한 종료
    - 생명주기 이벤트 콜백
    """

    def __init__(self) -> None:
        self._callbacks: list[LifecycleCallback] = []
        self._events: list[LifecycleEvent] = []
        self._max_events = 500  # 최근 이벤트만 보관

    def add_callback(self, callback: LifecycleCallback) -> None:
        """생명주기 이벤트 콜백 등록."""
        self._callbacks.append(callback)

    def transition(
        self,
        agent: AgentInstance,
        new_status: AgentStatus,
        *,
        error: str | None = None,
    ) -> LifecycleEvent:
        """상태 전이 — 유효성 검증 + 이벤트 발행.

        Raises:
            ValueError: 유효하지 않은 상태 전이
        """
        old_status = agent.status

        # 전이 유효성 검증
        valid = VALID_TRANSITIONS.get(old_status, set())
        if new_status not in valid:
            raise ValueError(
                f"Agent '{agent.name}': "
                f"{old_status.value} → {new_status.value} 전이 불가. "
                f"허용: {[s.value for s in valid]}"
            )

        # 상태 변경
        agent.status = new_status

        # 이벤트 생성
        event = LifecycleEvent(
            agent_name=agent.name,
            old_status=old_status,
            new_status=new_status,
            error=error,
        )

        # 이벤트 기록
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

        # 콜백 호출
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.warning(
                    "lifecycle_callback_error",
                    callback=str(cb),
                    error=str(e),
                )

        aac_log(
            agent.name, "lifecycle", "transition",
            f"{old_status.value} → {new_status.value}"
            + (f" (error: {error})" if error else ""),
        )

        return event

    def check_health(self, agent: AgentInstance) -> HealthCheckResult:
        """Agent 건강 검사.

        - READY/EXECUTING: healthy
        - ERROR: unhealthy
        - 나머지: 상태 보고만
        """
        healthy_statuses = {AgentStatus.READY, AgentStatus.EXECUTING}
        is_healthy = agent.status in healthy_statuses

        details: dict[str, Any] = {
            "query_count": agent.query_count,
            "total_cost_usd": agent.total_cost_usd,
            "total_duration_ms": agent.total_duration_ms,
            "has_runtime": agent.runtime is not None,
            "tools_count": len(agent.tools),
            "skills_count": len(agent.skills),
        }

        if agent.status == AgentStatus.ERROR:
            details["warning"] = "Agent가 에러 상태입니다"

        return HealthCheckResult(
            agent_name=agent.name,
            healthy=is_healthy,
            status=agent.status.value,
            details=details,
        )

    def check_all_health(
        self, agents: dict[str, AgentInstance],
    ) -> dict[str, HealthCheckResult]:
        """모든 Agent 건강 검사."""
        return {
            name: self.check_health(agent) for name, agent in agents.items()
        }

    async def graceful_shutdown(
        self,
        agents: dict[str, AgentInstance],
        *,
        timeout_seconds: float = 30.0,
    ) -> list[LifecycleEvent]:
        """우아한 종료 — 모든 활성 Agent 순차 종료.

        1. EXECUTING 상태 Agent는 완료 대기 (timeout까지)
        2. READY/ERROR 상태 Agent는 즉시 DESTROYING → DESTROYED
        3. LAZY Agent는 스킵
        """
        events: list[LifecycleEvent] = []
        start_time = time.monotonic()

        for name, agent in agents.items():
            elapsed = time.monotonic() - start_time
            if elapsed > timeout_seconds:
                logger.warning(
                    "graceful_shutdown_timeout",
                    remaining=len(agents) - len(events),
                )
                break

            if agent.status in (
                AgentStatus.LAZY,
                AgentStatus.DESTROYED,
                AgentStatus.DESTROYING,
            ):
                continue

            try:
                # EXECUTING 상태면 완료 대기
                if agent.status == AgentStatus.EXECUTING:
                    remaining = timeout_seconds - elapsed
                    await self._wait_for_ready(
                        agent, timeout=min(remaining, 10.0),
                    )

                # DESTROYING 전이
                ev = self.transition(agent, AgentStatus.DESTROYING)
                events.append(ev)

                # Runtime shutdown
                if agent.runtime:
                    try:
                        await agent.runtime.shutdown()
                    except Exception as e:
                        logger.warning(
                            "runtime_shutdown_error",
                            agent=name,
                            error=str(e),
                        )

                # DESTROYED 전이
                ev = self.transition(agent, AgentStatus.DESTROYED)
                events.append(ev)

            except Exception as e:
                # 전이 실패 시 ERROR 처리
                try:
                    ev = self.transition(
                        agent, AgentStatus.ERROR, error=str(e),
                    )
                    events.append(ev)
                except ValueError:
                    logger.error(
                        "shutdown_transition_error",
                        agent=name,
                        error=str(e),
                    )

        return events

    async def _wait_for_ready(
        self, agent: AgentInstance, *, timeout: float = 10.0,
    ) -> None:
        """EXECUTING → READY 대기."""
        start = time.monotonic()
        while agent.status == AgentStatus.EXECUTING:
            if time.monotonic() - start > timeout:
                break
            await asyncio.sleep(0.1)

    def get_events(
        self,
        agent_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """이벤트 히스토리 조회."""
        events = self._events
        if agent_name:
            events = [e for e in events if e.agent_name == agent_name]
        return [e.to_dict() for e in events[-limit:]]

    def get_summary(
        self, agents: dict[str, AgentInstance],
    ) -> dict[str, Any]:
        """생명주기 요약 통계."""
        status_counts: dict[str, int] = {}
        for agent in agents.values():
            status = agent.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "total_agents": len(agents),
            "status_counts": status_counts,
            "total_events": len(self._events),
            "total_callbacks": len(self._callbacks),
        }
