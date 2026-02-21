"""워크플로우 Manifest — YAML 스키마 (resources/workflows/*.yaml).

워크플로우는 여러 Agent를 순차(sequence) 또는 병렬(parallel)로
조합하여 실행하는 오케스트레이션 단위이다.
Spring Batch의 Job/Step 개념에 해당.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from aac.models.manifest import ResourceKind


class StepType(StrEnum):
    """워크플로우 스텝 유형."""
    AGENT = "agent"           # Agent 실행
    CONDITION = "condition"   # 조건 분기
    PARALLEL = "parallel"     # 병렬 실행 그룹


class OnFailure(StrEnum):
    """스텝 실패 시 행동."""
    STOP = "stop"       # 워크플로우 중단
    SKIP = "skip"       # 해당 스텝 건너뜀
    RETRY = "retry"     # 재시도


class WorkflowStep(BaseModel):
    """워크플로우 개별 스텝."""

    name: str
    type: StepType = StepType.AGENT
    agent: str | None = None              # type=agent 시 실행할 agent 이름
    prompt: str | None = None             # 직접 프롬프트 (템플릿 지원)
    prompt_template: str | None = None    # Jinja2 스타일 템플릿 참조
    input_from: str | None = None         # 이전 스텝 결과를 입력으로 사용
    on_failure: OnFailure = OnFailure.STOP
    timeout_seconds: int = 600
    retry_count: int = 0

    # type=parallel 시 하위 스텝
    steps: list[WorkflowStep] | None = None

    # type=condition 시 조건
    condition: str | None = None          # 조건 표현식 (예: "steps.step1.success")
    if_true: str | None = None            # 조건 참일 때 실행할 스텝 이름
    if_false: str | None = None           # 조건 거짓일 때 실행할 스텝 이름


class WorkflowMetadata(BaseModel):
    name: str
    description: str = ""
    version: str = "0.0.0"
    tags: list[str] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """워크플로우 스펙 — 스텝 목록 + 글로벌 설정."""

    steps: list[WorkflowStep]
    max_total_cost_usd: float | None = None      # 전체 비용 상한
    max_total_duration_seconds: int | None = None  # 전체 시간 상한
    context: dict[str, Any] = Field(default_factory=dict)   # 초기 컨텍스트


class WorkflowManifest(BaseModel):
    """Workflow YAML 스키마 (resources/workflows/*.yaml)."""

    apiVersion: str = "aac/v1"  # noqa: N815
    kind: Literal[ResourceKind.WORKFLOW] = ResourceKind.WORKFLOW
    metadata: WorkflowMetadata
    spec: WorkflowSpec

    source_path: str | None = Field(default=None, exclude=True)
