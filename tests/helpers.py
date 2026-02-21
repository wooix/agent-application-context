"""공유 테스트 헬퍼 — 샘플 YAML 데이터 + 유틸리티."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from collections.abc import AsyncIterator

from aac.runtime.base import AgentRuntime, ExecutionResult, RuntimeStatus, StreamChunk


# ─── Mock Runtime ─────────────────────────────────────


class MockRuntime(AgentRuntime):
    """테스트용 Mock Runtime — 실제 LLM 호출 없이 고정 응답 반환."""

    @property
    def name(self) -> str:
        return "mock"

    async def initialize(self, config: dict[str, Any]) -> None:
        self._config = config
        self._status = RuntimeStatus.READY

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
        return ExecutionResult(
            response=f"mock response to: {prompt}",
            cost_usd=0.001,
            duration_ms=100,
            model="mock-model",
        )

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
        yield StreamChunk(type="text", content=f"mock streaming: {prompt}")
        yield StreamChunk(
            type="done",
            metadata={"cost_usd": 0.001, "duration_ms": 100, "model": "mock-model"},
        )

    async def shutdown(self) -> None:
        self._status = RuntimeStatus.SHUTDOWN


# ─── YAML 생성 헬퍼 ──────────────────────────────────


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """딕셔너리를 YAML 파일로 작성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ─── 샘플 데이터 ─────────────────────────────────────


SAMPLE_TOOL_YAML: dict[str, Any] = {
    "apiVersion": "aac/v1",
    "kind": "Tool",
    "metadata": {"name": "test-tools", "description": "테스트용 도구"},
    "spec": {
        "items": [
            {"name": "Read", "description": "파일 읽기"},
            {"name": "Write", "description": "파일 쓰기"},
        ]
    },
}

SAMPLE_TOOL_YAML_2: dict[str, Any] = {
    "apiVersion": "aac/v1",
    "kind": "Tool",
    "metadata": {"name": "extra-tools", "description": "추가 도구"},
    "spec": {
        "items": [
            {"name": "Search", "description": "검색"},
        ]
    },
}

SAMPLE_SKILL_YAML: dict[str, Any] = {
    "apiVersion": "aac/v1",
    "kind": "Skill",
    "metadata": {"name": "test-skill", "description": "테스트 스킬"},
    "spec": {
        "instruction_file": "./SKILL.md",
        "required_tools": ["test-tools"],
    },
}

SAMPLE_ASPECT_YAML: dict[str, Any] = {
    "apiVersion": "aac/v1",
    "kind": "Aspect",
    "metadata": {"name": "test-aspect", "description": "테스트 Aspect"},
    "spec": {
        "type": "TestAspect",
        "order": 10,
        "pointcut": {"events": ["PreQuery", "PostQuery"]},
    },
}

SAMPLE_AGENT_YAML: dict[str, Any] = {
    "apiVersion": "aac/v1",
    "kind": "Agent",
    "metadata": {
        "name": "test-agent",
        "description": "테스트 에이전트",
        "version": "1.0.0",
        "tags": ["test"],
    },
    "spec": {
        "runtime": "mock",
        "tools": [{"ref": "test-tools"}],
        "skills": [{"ref": "test-skill"}],
        "system_prompt": "테스트 프롬프트",
        "scope": "singleton",
        "lazy": False,
        "capabilities": ["testing"],
        "limits": {"max_turns": 10, "timeout_seconds": 60},
    },
}
