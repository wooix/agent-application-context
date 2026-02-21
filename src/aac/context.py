"""AgentApplicationContext — 중앙 IoC 컨테이너 (FR-1.1~1.4).

Spring의 ApplicationContext에 해당하며, AAC 프레임워크의 핵심.
aac start 시 이 Context가 기동되어:
1. resources/ 스캔 (AgentScanner)
2. 레지스트리 구성 (Tool, Skill, Runtime, Agent)
3. DI 해석 (AgentFactory)
4. eager agent 초기화
5. FastAPI 서버 시작

모든 Agent 조회/실행은 이 Context를 통해 이루어진다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from aac.di.skill_registry import SkillRegistry
from aac.di.tool_registry import ToolRegistry
from aac.factory import AgentFactory
from aac.logging.formatter import aac_log, boot_log
from aac.models.instance import AgentInstance, AgentStatus
from aac.models.manifest import AgentManifest
from aac.runtime.claude_code import ClaudeCodeRuntime
from aac.runtime.registry import RuntimeRegistry
from aac.scanner import AgentScanner, ScanResult

logger = structlog.get_logger()


def _short_uuid() -> str:
    return uuid.uuid4().hex[:8]


class AgentApplicationContext:
    """AAC 중앙 컨테이너 — Spring ApplicationContext."""

    AAC_BANNER = r"""
  ___    ___    ___
 / _ \  / _ \  / __| Agent Application Context
| (_| || (_| || (__
 \__,_| \__,_| \___|  v0.1.0
"""

    def __init__(
        self,
        resources_dir: str | Path = "./resources",
        *,
        strict_tools: bool = False,
    ) -> None:
        self._resources_dir = Path(resources_dir)
        self._strict_tools = strict_tools

        # 레지스트리
        self._runtime_registry = RuntimeRegistry()
        self._tool_registry = ToolRegistry(strict=strict_tools)
        self._skill_registry = SkillRegistry()

        # Agent 관리
        self._agents: dict[str, AgentInstance] = {}
        self._manifests: dict[str, AgentManifest] = {}
        self._factory: AgentFactory | None = None

        # 스캔 결과
        self._scan_result: ScanResult | None = None

        # 상태
        self._started = False
        self._started_at: datetime | None = None

        # TX 카운터
        self._tx_counter = 0

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def agents(self) -> dict[str, AgentInstance]:
        return self._agents

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def skill_registry(self) -> SkillRegistry:
        return self._skill_registry

    @property
    def runtime_registry(self) -> RuntimeRegistry:
        return self._runtime_registry

    async def start(self) -> None:
        """Context 전체 기동 — Spring Boot의 SpringApplication.run()."""
        print(self.AAC_BANNER)

        boot_log("▶ Starting AgentApplicationContext...")

        # 1. 기본 Runtime 등록
        self._register_default_runtimes()

        # 2. resources/ 스캔
        scanner = AgentScanner(self._resources_dir)
        self._scan_result = scanner.scan_all()

        # 에러 보고
        if self._scan_result.errors:
            for err in self._scan_result.errors:
                boot_log(
                    f"⚠ SCAN_ERROR: {err.file_path} "
                    f"[{err.error_type}] {err.field or ''}: {err.message}"
                )

        boot_log(
            f"📂 Scanning resources/agents/ → {len(self._scan_result.agents)} agents"
        )
        boot_log(
            f"🔧 Scanning resources/tools/ → "
            f"{len(self._scan_result.tools)} bundles ({self._scan_result.total_tools} tools)"
        )
        boot_log(
            f"📋 Scanning resources/skills/ → {len(self._scan_result.skills)} skills"
        )
        boot_log(
            f"🎯 Scanning resources/aspects/ → {len(self._scan_result.aspects)} aspects"
        )

        # 3. 레지스트리 등록
        for tool in self._scan_result.tools:
            self._tool_registry.register(tool)
        for skill in self._scan_result.skills:
            self._skill_registry.register(skill)

        # 4. Factory 생성
        self._factory = AgentFactory(
            self._runtime_registry,
            self._tool_registry,
            self._skill_registry,
        )

        # 5. Agent 생성 (eager / lazy)
        boot_log("🚀 Initializing eager agents...")
        for manifest in self._scan_result.agents:
            self._manifests[manifest.metadata.name] = manifest
            if manifest.spec.lazy:
                # lazy agent는 placeholder만 등록
                agent = AgentInstance(
                    name=manifest.metadata.name,
                    description=manifest.metadata.description,
                    runtime_name=manifest.spec.runtime,
                    status=AgentStatus.LAZY,
                    scope=manifest.spec.scope.value,
                    lazy=True,
                    tags=manifest.metadata.tags,
                    capabilities=manifest.spec.capabilities,
                )
                self._agents[manifest.metadata.name] = agent
            else:
                agent = await self._factory.create(manifest)
                self._agents[manifest.metadata.name] = agent

        self._started = True
        self._started_at = datetime.now(timezone.utc)

        agent_count = len(self._agents)
        tool_count = self._scan_result.total_tools
        skill_count = len(self._scan_result.skills)
        aspect_count = len(self._scan_result.aspects)

        boot_log(
            f"✓ Context ready: {agent_count} agents, "
            f"{tool_count} tools, {skill_count} skills, {aspect_count} aspects"
        )

    def _register_default_runtimes(self) -> None:
        """기본 Runtime 어댑터 등록."""
        self._runtime_registry.register("claude-code", ClaudeCodeRuntime)
        # Phase 2에서 추가: gemini-mcp, openai-mcp, codex-cli

    def get_agent(self, name: str) -> AgentInstance:
        """이름으로 Agent 인스턴스 조회."""
        if name not in self._agents:
            available = list(self._agents.keys())
            raise KeyError(f"Agent '{name}' 미등록. 사용 가능: {available}")
        return self._agents[name]

    async def execute(
        self,
        agent_name: str,
        prompt: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Agent에 query 실행 (FR-9.2).

        Returns:
            execution_id, session_id, tx_id, result, cost_usd, duration_ms
        """
        agent = self.get_agent(agent_name)

        # lazy 초기화 (FR-5.3)
        if agent.status == AgentStatus.LAZY:
            manifest = self._manifests.get(agent_name)
            if manifest and self._factory:
                new_agent = await self._factory.create(manifest)
                self._agents[agent_name] = new_agent
                agent = new_agent

        if agent.runtime is None:
            raise RuntimeError(f"Agent '{agent_name}'의 runtime이 초기화되지 않았습니다")

        # ID 생성 (DR-3)
        session_id = f"sess_{_short_uuid()}"
        self._tx_counter += 1
        tx_id = f"tx_{self._tx_counter:03d}"
        execution_id = f"exec_{_short_uuid()}"

        aac_log(agent_name, session_id, tx_id, f'▶ STARTING query: "{prompt[:80]}"')

        agent.status = AgentStatus.EXECUTING

        # tool 정보 구성
        tools_for_runtime = [
            {"name": t.name, "description": t.description}
            for t in agent.tools
        ] if agent.tools else None

        result = await agent.runtime.execute(
            prompt,
            system_prompt=agent.system_prompt,
            tools=tools_for_runtime,
            context=context,
            max_turns=agent.max_turns,
            timeout_seconds=agent.timeout_seconds,
        )

        agent.status = AgentStatus.READY
        agent.query_count += 1
        agent.total_cost_usd += result.cost_usd
        agent.total_duration_ms += result.duration_ms

        status_icon = "✓" if result.success else "✗"
        aac_log(
            agent_name, session_id, tx_id,
            f"{status_icon} COMPLETED ({result.duration_ms}ms, "
            f"${result.cost_usd:.4f})"
        )

        return {
            "execution_id": execution_id,
            "session_id": session_id,
            "tx_id": tx_id,
            "agent": agent_name,
            "result": result.response,
            "success": result.success,
            "error": result.error,
            "cost_usd": result.cost_usd,
            "duration_ms": result.duration_ms,
            "model": result.model,
        }

    async def shutdown(self) -> None:
        """Context 종료 — 모든 Agent runtime shutdown."""
        boot_log("Shutting down AgentApplicationContext...")
        for name, agent in self._agents.items():
            if agent.runtime and agent.status != AgentStatus.LAZY:
                try:
                    agent.status = AgentStatus.DESTROYING
                    await agent.runtime.shutdown()
                    agent.status = AgentStatus.DESTROYED
                except Exception as e:
                    logger.error("agent_shutdown_error", agent=name, error=str(e))
        self._started = False
        boot_log("✓ Context shutdown complete")

    def get_status(self) -> dict[str, Any]:
        """Context 전체 상태 (FR-9.1: GET /api/status)."""
        return {
            "version": "0.1.0",
            "started": self._started,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "agents": {
                "total": len(self._agents),
                "active": sum(
                    1 for a in self._agents.values()
                    if a.status in (AgentStatus.READY, AgentStatus.EXECUTING)
                ),
                "lazy": sum(1 for a in self._agents.values() if a.status == AgentStatus.LAZY),
            },
            "tools": {
                "bundles": len(self._tool_registry),
                "total": self._tool_registry.total_tool_count,
            },
            "skills": {
                "total": len(self._skill_registry),
            },
            "aspects": {
                "total": len(self._scan_result.aspects) if self._scan_result else 0,
            },
        }

    def list_agents(self) -> list[dict[str, Any]]:
        """Agent 목록 (FR-9.1: GET /api/agents)."""
        return [agent.to_summary() for agent in self._agents.values()]
