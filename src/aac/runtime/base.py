"""AgentRuntime ABC — LLM Runtime 추상화 (FR-6.1).

Spring의 DataSource에 해당하는 개념으로,
모든 LLM 런타임(Claude Code, Gemini, OpenAI, Codex)은
이 인터페이스를 구현하여 통일된 실행 모델을 제공한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


class RuntimeStatus(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    READY = "READY"
    BUSY = "BUSY"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class ExecutionResult:
    """Runtime 실행 결과 — 모든 어댑터가 동일한 형식으로 반환 (FR-6.2)."""

    response: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class StreamChunk:
    """스트리밍 응답의 개별 청크."""

    type: str                   # text | tool_call | error | done
    content: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(ABC):
    """LLM Runtime 공통 인터페이스.

    모든 어댑터는 이 ABC를 구현한다.
    initialize → execute/stream → cancel(선택) → shutdown 순서로 사용.
    """

    def __init__(self) -> None:
        self._status = RuntimeStatus.UNINITIALIZED
        self._config: dict[str, Any] = {}

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    @property
    def name(self) -> str:
        """런타임 식별자 (예: 'claude-code', 'gemini-mcp')."""
        return self.__class__.__name__

    @abstractmethod
    async def initialize(self, config: dict[str, Any]) -> None:
        """런타임 초기화 — config 적용, 연결 확인 등."""
        ...

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 600,
    ) -> ExecutionResult:
        """동기 실행 — 완료까지 대기 후 결과 반환."""
        ...

    async def stream(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 600,
    ) -> AsyncIterator[StreamChunk]:
        """스트리밍 실행 — 기본 구현은 execute를 래핑."""
        result = await self.execute(
            prompt,
            system_prompt=system_prompt,
            tools=tools,
            context=context,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        yield StreamChunk(type="text", content=result.response)
        yield StreamChunk(type="done")

    async def cancel(self) -> None:
        """실행 취소 — 기본 구현은 no-op."""

    async def get_cost(self) -> float:
        """누적 비용 조회 — 기본 0.0."""
        return 0.0

    async def get_status(self) -> RuntimeStatus:
        """현재 상태 조회."""
        return self._status

    @abstractmethod
    async def shutdown(self) -> None:
        """런타임 정리 — 연결 해제, 리소스 반환."""
        ...
