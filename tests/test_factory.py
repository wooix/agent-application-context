"""AgentFactory 단위 테스트 — DI 파이프라인 E2E."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aac.di.skill_registry import SkillRegistry
from aac.di.tool_registry import ToolRegistry
from aac.factory import AgentFactory
from aac.models.instance import AgentStatus
from aac.models.manifest import (
    AgentManifest,
    AgentMetadata,
    AgentSpec,
    Limits,
    SkillManifest,
    SkillMetadata,
    SkillRef,
    SkillSpec,
    ToolItem,
    ToolManifest,
    ToolMetadata,
    ToolRef,
    ToolSpec,
)
from aac.runtime.registry import RuntimeRegistry
from tests.helpers import MockRuntime


# ─── 픽스처 ──────────────────────────────────────────


@pytest.fixture
def runtime_registry() -> RuntimeRegistry:
    """MockRuntime이 등록된 RuntimeRegistry."""
    registry = RuntimeRegistry()
    registry.register("mock", MockRuntime)
    return registry


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """샘플 Tool이 등록된 ToolRegistry."""
    registry = ToolRegistry()
    manifest = ToolManifest(
        metadata=ToolMetadata(name="test-tools"),
        spec=ToolSpec(
            items=[
                ToolItem(name="Read", description="파일 읽기"),
                ToolItem(name="Write", description="파일 쓰기"),
            ]
        ),
    )
    registry.register(manifest)
    return registry


@pytest.fixture
def skill_registry(tmp_path: Path) -> SkillRegistry:
    """샘플 Skill이 등록된 SkillRegistry (+ instruction 파일)."""
    registry = SkillRegistry()

    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# 테스트 스킬 지침", encoding="utf-8")

    manifest = SkillManifest(
        metadata=SkillMetadata(name="test-skill"),
        spec=SkillSpec(
            instruction_file="./SKILL.md",
            required_tools=["test-tools"],
        ),
    )
    manifest.source_path = str(skill_dir / "skill.yaml")
    registry.register(manifest)

    return registry


@pytest.fixture
def factory(
    runtime_registry: RuntimeRegistry,
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> AgentFactory:
    """완전한 의존성이 주입된 AgentFactory."""
    return AgentFactory(runtime_registry, tool_registry, skill_registry)


def _make_agent_manifest(
    name: str = "test-agent",
    runtime: str = "mock",
    tools: list[dict[str, Any]] | None = None,
    skills: list[dict[str, str]] | None = None,
    system_prompt: str = "테스트 프롬프트",
    lazy: bool = False,
    max_turns: int = 10,
) -> AgentManifest:
    """테스트용 AgentManifest 생성 헬퍼."""
    if tools is None:
        tools = [{"ref": "test-tools"}]
    if skills is None:
        skills = [{"ref": "test-skill"}]

    return AgentManifest(
        metadata=AgentMetadata(
            name=name,
            description=f"{name} 에이전트",
            version="1.0.0",
            tags=["test"],
        ),
        spec=AgentSpec(
            runtime=runtime,
            tools=[ToolRef(**t) for t in tools],
            skills=[SkillRef(**s) for s in skills],
            system_prompt=system_prompt,
            scope="singleton",
            lazy=lazy,
            capabilities=["testing"],
            limits=Limits(max_turns=max_turns, timeout_seconds=60),
        ),
    )


# ─── 테스트 ──────────────────────────────────────────


class TestCreate:
    """create() — AgentManifest → AgentInstance 변환."""

    async def test_기본_생성(self, factory: AgentFactory) -> None:
        """정상적인 manifest로 AgentInstance를 생성해야 한다."""
        manifest = _make_agent_manifest()
        agent = await factory.create(manifest)

        assert agent.name == "test-agent"
        assert agent.status == AgentStatus.READY
        assert agent.runtime is not None
        assert agent.runtime_name == "mock"

    async def test_tool_DI(self, factory: AgentFactory) -> None:
        """ToolRegistry에서 해석된 tool이 주입되어야 한다."""
        manifest = _make_agent_manifest()
        agent = await factory.create(manifest)

        assert agent.tools_loaded_count == 2
        tool_names = {t.name for t in agent.tools}
        assert "Read" in tool_names
        assert "Write" in tool_names

    async def test_skill_DI(self, factory: AgentFactory) -> None:
        """SkillRegistry에서 해석된 skill이 주입되어야 한다."""
        manifest = _make_agent_manifest()
        agent = await factory.create(manifest)

        assert "test-skill" in agent.skills

    async def test_system_prompt_합성_DR2(self, factory: AgentFactory) -> None:
        """DR-2: system_prompt → skill 문서 순서로 합성되어야 한다."""
        manifest = _make_agent_manifest(system_prompt="기본 프롬프트")
        agent = await factory.create(manifest)

        assert "기본 프롬프트" in agent.system_prompt
        assert "테스트 스킬 지침" in agent.system_prompt
        assert "Injected Skills" in agent.system_prompt

    async def test_메타데이터_전달(self, factory: AgentFactory) -> None:
        """manifest의 메타데이터가 instance에 정확히 전달되어야 한다."""
        manifest = _make_agent_manifest(name="my-agent", max_turns=5)
        agent = await factory.create(manifest)

        assert agent.version == "1.0.0"
        assert "test" in agent.tags
        assert "testing" in agent.capabilities
        assert agent.max_turns == 5
        assert agent.timeout_seconds == 60
        assert agent.scope == "singleton"

    async def test_tool_없이_생성(self, factory: AgentFactory) -> None:
        """Tool 참조 없이도 Agent를 생성할 수 있어야 한다."""
        manifest = _make_agent_manifest(tools=[], skills=[])
        agent = await factory.create(manifest)

        assert agent.tools_loaded_count == 0
        assert len(agent.skills) == 0

    async def test_미등록_runtime_에러(self, factory: AgentFactory) -> None:
        """미등록 runtime을 참조하면 KeyError가 발생해야 한다."""
        manifest = _make_agent_manifest(runtime="nonexistent")

        with pytest.raises(KeyError, match="미등록"):
            await factory.create(manifest)


class TestPromptSynthesis:
    """_synthesize_prompt() — DR-2 프롬프트 합성 순서."""

    async def test_system_prompt만(self, factory: AgentFactory) -> None:
        """system_prompt만 있으면 그것만 포함되어야 한다."""
        manifest = _make_agent_manifest(
            system_prompt="시스템 프롬프트 전용",
            skills=[],
        )
        agent = await factory.create(manifest)

        assert agent.system_prompt == "시스템 프롬프트 전용"

    async def test_prompt_file_합성(self, factory: AgentFactory, tmp_path: Path) -> None:
        """prompt_file이 있으면 system_prompt 뒤에 합성되어야 한다."""
        # prompt_file 생성
        prompt_dir = tmp_path / "agents" / "file-agent"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "custom.md").write_text("# 파일 프롬프트", encoding="utf-8")

        manifest = _make_agent_manifest(
            name="file-agent",
            system_prompt="기본",
            skills=[],
        )
        manifest.spec.prompt_file = "./custom.md"
        manifest.source_path = str(prompt_dir / "agent.yaml")

        agent = await factory.create(manifest)

        assert "기본" in agent.system_prompt
        assert "파일 프롬프트" in agent.system_prompt

    async def test_skill_문서_순서(self, factory: AgentFactory) -> None:
        """skill 문서는 system_prompt 뒤에 구분자와 함께 합성되어야 한다."""
        manifest = _make_agent_manifest(system_prompt="메인 프롬프트")
        agent = await factory.create(manifest)

        # system_prompt → ... → skill 순서
        parts = agent.system_prompt.split("---")
        assert len(parts) >= 2
        assert "메인 프롬프트" in parts[0]


class TestToSummary:
    """생성된 AgentInstance의 to_summary() 검증."""

    async def test_summary_필드(self, factory: AgentFactory) -> None:
        """AC-2: summary에 tools_loaded_count와 skills가 포함되어야 한다."""
        manifest = _make_agent_manifest()
        agent = await factory.create(manifest)
        summary = agent.to_summary()

        assert summary["tools_loaded_count"] == 2
        assert "test-skill" in summary["skills"]
        assert summary["status"] == "READY"
        assert summary["runtime"] == "mock"

    async def test_detail_필드(self, factory: AgentFactory) -> None:
        """detail에 tool 상세 정보와 limits가 포함되어야 한다."""
        manifest = _make_agent_manifest()
        agent = await factory.create(manifest)
        detail = agent.to_detail()

        assert len(detail["tools"]) == 2
        assert detail["tools"][0]["qualified_name"].startswith("test-tools/")
        assert detail["max_turns"] == 10
