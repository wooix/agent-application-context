"""Agent 런타임 인스턴스 모델 — Spring의 Bean에 해당.

AgentInstance는 AgentFactory에 의해 생성되며,
runtime + tools + skills + depends_on이 DI로 주입된 상태의 살아있는 객체이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aac.runtime.base import AgentRuntime


class AgentStatus(str, Enum):
    """Agent 생명주기 상태."""

    REGISTERED = "REGISTERED"       # manifest 파싱됨, 아직 초기화 안됨
    INITIALIZING = "INITIALIZING"   # on_init 실행 중
    READY = "READY"                 # 실행 대기 중
    EXECUTING = "EXECUTING"         # query 처리 중
    ERROR = "ERROR"                 # 오류 상태
    DESTROYING = "DESTROYING"       # on_destroy 실행 중
    DESTROYED = "DESTROYED"         # 종료됨
    LAZY = "LAZY"                   # lazy=true, 아직 초기화 안됨


@dataclass
class ToolDefinition:
    """해석 완료된 개별 Tool 정보."""

    name: str
    bundle_name: str | None = None
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

    @property
    def qualified_name(self) -> str:
        """Tool 고유 식별자 (DR-1): bundle_name/name."""
        if self.bundle_name:
            return f"{self.bundle_name}/{self.name}"
        return self.name


@dataclass
class AgentInstance:
    """DI가 완료된 Agent 인스턴스 — Spring의 Bean 객체."""

    # 식별
    name: str
    description: str = ""
    version: str = "0.0.0"
    tags: list[str] = field(default_factory=list)

    # DI 주입된 구성 요소
    runtime: AgentRuntime | None = None
    runtime_name: str = ""
    tools: list[ToolDefinition] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)             # skill 이름 목록
    system_prompt: str = ""
    capabilities: list[str] = field(default_factory=list)

    # 의존 Agent
    dependencies: dict[str, AgentInstance] = field(default_factory=dict)

    # 생명주기
    status: AgentStatus = AgentStatus.REGISTERED
    scope: str = "singleton"
    lazy: bool = False
    created_at: datetime = field(default_factory=datetime.now)

    # 실행 통계
    query_count: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0

    # Limits (DR-7)
    max_turns: int = 30
    timeout_seconds: int = 600

    @property
    def tools_loaded_count(self) -> int:
        """로딩된 tool 수 (FR-8.4)."""
        return len(self.tools)

    def to_summary(self) -> dict[str, Any]:
        """API 응답용 요약 정보 (FR-9.1)."""
        return {
            "name": self.name,
            "description": self.description,
            "runtime": self.runtime_name,
            "status": self.status.value,
            "tools_loaded_count": self.tools_loaded_count,
            "skills": self.skills,
            "capabilities": self.capabilities,
            "scope": self.scope,
            "lazy": self.lazy,
            "query_count": self.query_count,
            "total_cost_usd": self.total_cost_usd,
            "tags": self.tags,
        }

    def to_detail(self) -> dict[str, Any]:
        """Agent 상세 정보."""
        summary = self.to_summary()
        summary.update({
            "version": self.version,
            "tools": [
                {"name": t.name, "bundle": t.bundle_name, "qualified_name": t.qualified_name}
                for t in self.tools
            ],
            "dependencies": list(self.dependencies.keys()),
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at.isoformat(),
            "total_duration_ms": self.total_duration_ms,
        })
        return summary
