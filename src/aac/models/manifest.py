"""YAML 스키마 정의 — Agent, Tool, Skill, Aspect manifest 모델.

모든 YAML 파일은 부팅 시 이 모델들로 검증된다 (FR-2.1, FR-2.2).
apiVersion 필드로 스키마 버전을 관리하며, kind 필드로 리소스 타입을 구분한다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ─── 공통 ─────────────────────────────────────────────────

class ResourceKind(str, Enum):
    AGENT = "Agent"
    TOOL = "Tool"
    SKILL = "Skill"
    ASPECT = "Aspect"
    RUNTIME = "Runtime"
    WORKFLOW = "Workflow"


class ScopeType(str, Enum):
    """Agent 인스턴스 생명주기 정책 (DR-8)."""

    SINGLETON = "singleton"
    TASK = "task"
    SESSION = "session"


# ─── Tool ─────────────────────────────────────────────────

class ToolItem(BaseModel):
    """Tool 번들 내 개별 도구 정의 (FR-3.2)."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class ToolMetadata(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    items: list[ToolItem] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def unique_item_names(cls, v: list[ToolItem]) -> list[ToolItem]:
        """번들 내 도구 이름 중복 검사."""
        names = [item.name for item in v]
        dupes = [n for n in names if names.count(n) > 1]
        if dupes:
            raise ValueError(f"번들 내 Tool 이름 중복: {set(dupes)}")
        return v


class ToolManifest(BaseModel):
    """Tool YAML 스키마 (resources/tools/*/tool.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.TOOL] = ResourceKind.TOOL
    metadata: ToolMetadata
    spec: ToolSpec

    # 파싱 후 주입되는 메타 — YAML에는 없음
    source_path: str | None = Field(default=None, exclude=True)


# ─── Skill ────────────────────────────────────────────────

class SkillMetadata(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillSpec(BaseModel):
    """Skill 정의 — instruction 문서를 Agent system_prompt에 주입 (FR-4.1)."""

    instruction_file: str
    checklist_file: str | None = None
    examples_dir: str | None = None
    required_tools: list[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    """Skill YAML 스키마 (resources/skills/*/skill.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.SKILL] = ResourceKind.SKILL
    metadata: SkillMetadata
    spec: SkillSpec

    source_path: str | None = Field(default=None, exclude=True)


# ─── Agent ────────────────────────────────────────────────

class ToolRef(BaseModel):
    """Agent에서 Tool을 참조하는 방법 (번들 ref 또는 개별 name)."""

    ref: str | None = None
    name: str | None = None

    @field_validator("name")
    @classmethod
    def ref_or_name(cls, v: str | None, info: Any) -> str | None:
        ref = info.data.get("ref")
        if not ref and not v:
            raise ValueError("ToolRef에는 ref 또는 name 중 하나가 필요합니다")
        return v


class SkillRef(BaseModel):
    """Agent에서 Skill을 참조."""

    ref: str


class DependsOn(BaseModel):
    """Agent 간 의존성 선언 (FR-5.4)."""

    name: str | None = None
    capability: str | None = None
    optional: bool = False
    qualifier: str | None = None

    @field_validator("capability")
    @classmethod
    def name_or_capability(cls, v: str | None, info: Any) -> str | None:
        name = info.data.get("name")
        if not name and not v:
            raise ValueError("DependsOn에는 name 또는 capability 중 하나가 필요합니다")
        return v


class Hooks(BaseModel):
    on_init: list[dict[str, str]] = Field(default_factory=list)
    on_destroy: list[dict[str, str]] = Field(default_factory=list)


class Limits(BaseModel):
    """Agent 실행 제한 — max_turns의 유일한 정의 위치 (DR-7)."""

    max_turns: int = 30
    timeout_seconds: int = 600


class AgentMetadata(BaseModel):
    name: str
    description: str = ""
    version: str = "0.0.0"
    tags: list[str] = Field(default_factory=list)


class AgentSpec(BaseModel):
    """Agent 스펙 — Runtime, Tool/Skill DI, 의존성, 생명주기 (FR-5)."""

    # Runtime
    runtime: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)

    # Tool DI (FR-3.1)
    tools: list[ToolRef] = Field(default_factory=list)

    # Skill DI (FR-4.1)
    skills: list[SkillRef] = Field(default_factory=list)

    # System Prompt (DR-2 합성 순서: system_prompt → prompt_file → skill 문서)
    system_prompt: str = ""
    prompt_file: str | None = None

    # Agent DI (FR-5.4)
    depends_on: list[DependsOn] = Field(default_factory=list)

    # Lifecycle (DR-8)
    scope: ScopeType = ScopeType.SINGLETON
    lazy: bool = False
    capabilities: list[str] = Field(default_factory=list)
    hooks: Hooks = Field(default_factory=Hooks)

    # Limits (DR-7)
    limits: Limits = Field(default_factory=Limits)


class AgentManifest(BaseModel):
    """Agent YAML 스키마 (resources/agents/*/agent.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.AGENT] = ResourceKind.AGENT
    metadata: AgentMetadata
    spec: AgentSpec

    source_path: str | None = Field(default=None, exclude=True)


# ─── Aspect ───────────────────────────────────────────────

class AspectPointcut(BaseModel):
    """Aspect가 적용될 대상 필터 (FR-7.2)."""

    agents: list[str] = Field(default_factory=list)        # agent 이름 패턴
    tags: list[str] = Field(default_factory=list)           # agent 태그 매칭
    events: list[str] = Field(default_factory=list)         # 이벤트 타입 필터


class AspectMetadata(BaseModel):
    name: str
    description: str = ""


class AspectSpec(BaseModel):
    """Aspect 스펙 — 타입, 적용 대상, 실행 순서 (FR-7.3)."""

    type: str                       # audit-logging | tool-tracking | transaction | execution-logging
    pointcut: AspectPointcut = Field(default_factory=AspectPointcut)
    order: int = 100                # 낮을수록 먼저 실행
    config: dict[str, Any] = Field(default_factory=dict)


class AspectManifest(BaseModel):
    """Aspect YAML 스키마 (resources/aspects/*.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.ASPECT] = ResourceKind.ASPECT
    metadata: AspectMetadata
    spec: AspectSpec

    source_path: str | None = Field(default=None, exclude=True)


# ─── Runtime ─────────────────────────────────────────────

class RuntimeMetadata(BaseModel):
    name: str
    description: str = ""


class RuntimeSpec(BaseModel):
    """Runtime 선언적 정의 — module + class로 자동 로드."""

    type: str                       # 클래스 이름 (참고용)
    module: str                     # Python 모듈 경로 (예: aac.runtime.gemini_mcp)
    class_name: str = Field(alias="class")  # 클래스명 (예: GeminiMCPRuntime)
    default_config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class RuntimeManifest(BaseModel):
    """Runtime YAML 스키마 (resources/runtimes/*.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.RUNTIME] = ResourceKind.RUNTIME
    metadata: RuntimeMetadata
    spec: RuntimeSpec

    source_path: str | None = Field(default=None, exclude=True)
