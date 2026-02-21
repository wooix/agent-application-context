"""AgentFactory — DI 통합 엔진 (FR-5.1~5.4).

Spring의 BeanFactory에 해당하며,
AgentManifest → AgentInstance 생성 과정에서:
1. RuntimeRegistry에서 runtime 인스턴스 생성
2. ToolRegistry에서 tool 번들 해석 + 충돌 검사 (DR-1)
3. SkillRegistry에서 skill 문서 로드 + required_tools 검사 (FR-4.2)
4. system_prompt 합성 (DR-2: system_prompt → prompt_file → skill 문서)
5. AgentInstance 생성 + 메타 정보 설정
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from aac.di.skill_registry import SkillRegistry
from aac.di.tool_registry import ToolRegistry
from aac.logging.formatter import init_log
from aac.models.instance import AgentInstance, AgentStatus
from aac.models.manifest import AgentManifest
from aac.runtime.registry import RuntimeRegistry

logger = structlog.get_logger()


class AgentFactory:
    """Agent 인스턴스 생성 팩토리 — DI 통합."""

    def __init__(
        self,
        runtime_registry: RuntimeRegistry,
        tool_registry: ToolRegistry,
        skill_registry: SkillRegistry,
    ) -> None:
        self._runtime_registry = runtime_registry
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry

    async def create(
        self,
        manifest: AgentManifest,
        *,
        skip_runtime_init: bool = False,
    ) -> AgentInstance:
        """AgentManifest → AgentInstance (DI 완료 상태).

        Args:
            manifest: 파싱/검증된 Agent YAML
            skip_runtime_init: True면 runtime.initialize() 건너뜀 (lazy 초기화용)
        """
        name = manifest.metadata.name
        logger.info("agent_factory_create", name=name, runtime=manifest.spec.runtime)

        # 1. Runtime 인스턴스 생성
        runtime_cls = self._runtime_registry.get(manifest.spec.runtime)
        runtime = runtime_cls()
        if not skip_runtime_init:
            await runtime.initialize(manifest.spec.runtime_config)

        # 2. Tool 해석 & DI (FR-3.1, DR-1)
        resolved_tools = self._tool_registry.resolve_tools(manifest.spec.tools)

        # tool 이름 집합 (skill required_tools 검사용)
        available_tool_names: set[str] = set()
        for t in resolved_tools:
            available_tool_names.add(t.name)
            if t.bundle_name:
                available_tool_names.add(t.bundle_name)

        tools_summary = self._build_tools_summary(resolved_tools)
        init_log(name, f"⚙ TOOLS_LOADED: {len(resolved_tools)} tools ({tools_summary})")

        # 3. Skill 해석 & 문서 로드 (FR-4.1, FR-4.2)
        skill_instructions = self._skill_registry.resolve_skills(
            manifest.spec.skills,
            available_tool_names,
        )
        skill_names = [ref.ref for ref in manifest.spec.skills]

        if skill_names:
            init_log(name, f"📋 SKILLS_INJECTED: {len(skill_names)} ({', '.join(skill_names)})")

        # 4. System Prompt 합성 (DR-2)
        system_prompt = self._synthesize_prompt(manifest, skill_instructions)

        # 5. AgentInstance 생성
        agent = AgentInstance(
            name=name,
            description=manifest.metadata.description,
            version=manifest.metadata.version,
            tags=manifest.metadata.tags,
            runtime=runtime,
            runtime_name=manifest.spec.runtime,
            tools=resolved_tools,
            skills=skill_names,
            system_prompt=system_prompt,
            capabilities=manifest.spec.capabilities,
            status=(
                AgentStatus.LAZY if manifest.spec.lazy and skip_runtime_init
                else AgentStatus.READY
            ),
            scope=manifest.spec.scope.value,
            lazy=manifest.spec.lazy,
            max_turns=manifest.spec.limits.max_turns,
            timeout_seconds=manifest.spec.limits.timeout_seconds,
        )

        logger.info(
            "agent_created",
            name=name,
            tools_count=agent.tools_loaded_count,
            skills=skill_names,
            status=agent.status.value,
        )
        return agent

    def _synthesize_prompt(
        self,
        manifest: AgentManifest,
        skill_instructions: list[str],
    ) -> str:
        """System Prompt 합성 (DR-2).

        순서: system_prompt → prompt_file → skill 문서들.
        """
        parts: list[str] = []

        # system_prompt (직접 선언)
        if manifest.spec.system_prompt:
            parts.append(manifest.spec.system_prompt.strip())

        # prompt_file (파일 참조)
        if manifest.spec.prompt_file and manifest.source_path:
            prompt_path = Path(manifest.source_path).parent / manifest.spec.prompt_file
            if prompt_path.exists():
                content = prompt_path.read_text(encoding="utf-8")
                parts.append(content.strip())
            else:
                logger.warning(
                    "prompt_file_not_found",
                    agent=manifest.metadata.name,
                    path=str(prompt_path),
                )

        # skill 문서들
        if skill_instructions:
            parts.append("\n---\n## Injected Skills")
            parts.extend(skill_instructions)

        return "\n\n".join(parts)

    @staticmethod
    def _build_tools_summary(tools: list[Any]) -> str:
        """Tool 요약 문자열 생성: "file-ops:5, code-exec:2, WebSearch:1"."""
        from collections import Counter
        bundle_counts: Counter[str] = Counter()
        individual: list[str] = []

        for t in tools:
            if t.bundle_name:
                bundle_counts[t.bundle_name] += 1
            else:
                individual.append(t.name)

        parts = [f"{name}:{count}" for name, count in bundle_counts.items()]
        parts.extend(f"{name}:1" for name in individual)
        return ", ".join(parts)
