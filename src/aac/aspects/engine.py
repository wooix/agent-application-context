"""AspectEngine — AOP 위빙 엔진 (FR-7.1~7.3).

Spring AOP의 PointcutAdvisor에 해당하며,
Aspect YAML에 정의된 pointcut 이벤트(PreQuery, PostQuery, PreToolUse 등)를
Agent 실행 흐름에 삽입한다.

사용 흐름:
1. Context.start()에서 aspect manifest 등록
2. Context.execute()에서 apply() 호출
3. 각 aspect handler가 이벤트별 로직 실행
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from aac.models.manifest import AspectManifest

logger = structlog.get_logger()


class AspectEventType:
    """Aspect Pointcut 이벤트 타입 상수."""

    PRE_QUERY = "PreQuery"
    POST_QUERY = "PostQuery"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    ON_ERROR = "OnError"


@dataclass
class AspectContext:
    """Aspect 실행 시 전달되는 컨텍스트 데이터."""

    agent_name: str
    session_id: str
    tx_id: str
    execution_id: str = ""
    event_type: str = ""
    prompt: str = ""
    response: str = ""
    error: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    model: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AspectHandler:
    """개별 Aspect 처리기 — manifest 설정 기반 동작.

    Phase 3에서 AuditLoggingAspect, ToolTrackingAspect 등
    구체적 handler가 이 클래스를 상속하거나 엔진에 등록된다.
    """

    def __init__(self, manifest: AspectManifest) -> None:
        self._manifest = manifest
        self._name = manifest.metadata.name
        self._config = manifest.spec.config

    @property
    def name(self) -> str:
        return self._name

    @property
    def order(self) -> int:
        return self._manifest.spec.order

    @property
    def events(self) -> list[str]:
        return self._manifest.spec.pointcut.events

    @property
    def target_agents(self) -> list[str]:
        return self._manifest.spec.pointcut.agents

    @property
    def target_tags(self) -> list[str]:
        return self._manifest.spec.pointcut.tags

    async def handle(self, event_type: str, ctx: AspectContext) -> None:
        """이벤트 처리 — 하위 클래스에서 오버라이드."""
        logger.debug(
            "aspect_handle",
            aspect=self._name,
            event=event_type,
            agent=ctx.agent_name,
        )


class AspectEngine:
    """AOP 위빙 엔진 — Aspect 등록 + 이벤트 적용."""

    def __init__(self) -> None:
        self._handlers: list[AspectHandler] = []
        self._handler_registry: dict[str, type[AspectHandler]] = {}

    def register_handler_type(self, aspect_type: str, handler_cls: type[AspectHandler]) -> None:
        """Aspect type → Handler 클래스 매핑 등록.

        예: register_handler_type("AuditLoggingAspect", AuditLoggingHandler)
        """
        self._handler_registry[aspect_type] = handler_cls
        logger.debug("aspect_handler_type_registered", type=aspect_type, cls=handler_cls.__name__)

    def register(self, manifest: AspectManifest) -> None:
        """AspectManifest로부터 handler 인스턴스를 생성하여 등록.

        manifest.spec.type에 대응하는 handler 클래스가 있으면 사용하고,
        없으면 기본 AspectHandler를 사용한다.
        """
        handler_cls = self._handler_registry.get(manifest.spec.type, AspectHandler)
        handler = handler_cls(manifest)
        self._handlers.append(handler)

        # order 기준 정렬 (낮을수록 먼저)
        self._handlers.sort(key=lambda h: h.order)

        logger.info(
            "aspect_registered",
            name=manifest.metadata.name,
            type=manifest.spec.type,
            order=manifest.spec.order,
            events=manifest.spec.pointcut.events,
        )

    async def apply(
        self,
        event_type: str,
        ctx: AspectContext,
    ) -> None:
        """해당 이벤트 타입에 매칭되는 모든 Aspect를 순서대로 실행.

        필터 규칙:
        1. pointcut.events에 event_type 포함
        2. pointcut.agents가 비어있거나 agent_name 포함
        3. pointcut.tags가 비어있거나 metadata.tags에 하나라도 포함 (Phase 3+)
        """
        ctx.event_type = event_type

        for handler in self._handlers:
            if not self._matches(handler, event_type, ctx):
                continue

            try:
                await handler.handle(event_type, ctx)
            except Exception as e:
                logger.error(
                    "aspect_handle_error",
                    aspect=handler.name,
                    event_type=event_type,
                    agent=ctx.agent_name,
                    error_msg=str(e),
                )

    def _matches(
        self,
        handler: AspectHandler,
        event_type: str,
        ctx: AspectContext,
    ) -> bool:
        """handler가 이 이벤트에 적용되어야 하는지 판단."""
        # 이벤트 타입 매칭
        if handler.events and event_type not in handler.events:
            return False

        # agent 이름 필터
        if handler.target_agents and ctx.agent_name not in handler.target_agents:
            return False

        return True

    @property
    def handler_count(self) -> int:
        return len(self._handlers)

    def list_handlers(self) -> list[dict[str, Any]]:
        """등록된 handler 요약 목록."""
        return [
            {
                "name": h.name,
                "order": h.order,
                "events": h.events,
            }
            for h in self._handlers
        ]
