"""WebSocketPublisherAspect — WebSocket 이벤트 발행 (FR-9.3).

Aspect 이벤트를 WebSocket으로 연결된 모든 클라이언트에 broadcast한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aac.aspects.engine import AspectContext, AspectHandler
from aac.models.events import (
    AACEvent,
    AgentStatusChangeEvent,
    QueryCompleteEvent,
    QueryStartEvent,
    ToolUseEvent,
)
from aac.models.manifest import AspectManifest

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class WebSocketPublisherHandler(AspectHandler):
    """Aspect 이벤트를 WebSocket broadcast로 발행."""

    def __init__(self, manifest: AspectManifest) -> None:
        super().__init__(manifest)
        self._broadcast_fn: Callable[[dict[str, Any]], Coroutine] | None = None

    def set_broadcast(
        self,
        fn: Callable[[dict[str, Any]], Coroutine],
    ) -> None:
        """broadcast 함수 주입 — ConnectionManager.broadcast."""
        self._broadcast_fn = fn

    async def handle(self, event_type: str, ctx: AspectContext) -> None:
        if self._broadcast_fn is None:
            return

        event = self._build_event(event_type, ctx)
        if event:
            await self._broadcast_fn(event.model_dump())

    def _build_event(self, event_type: str, ctx: AspectContext) -> AACEvent | None:
        """이벤트 타입에 따라 적절한 AACEvent 생성."""
        base = {
            "session_id": ctx.session_id,
            "tx_id": ctx.tx_id,
        }

        if event_type == "PreQuery":
            return QueryStartEvent(
                **base,
                payload={
                    "agent": ctx.agent_name,
                    "prompt": ctx.prompt[:200],
                    "execution_id": ctx.execution_id,
                },
            )
        elif event_type == "PostQuery":
            return QueryCompleteEvent(
                **base,
                payload={
                    "agent": ctx.agent_name,
                    "success": ctx.error is None,
                    "cost_usd": ctx.cost_usd,
                    "duration_ms": ctx.duration_ms,
                    "model": ctx.model,
                    "execution_id": ctx.execution_id,
                },
            )
        elif event_type in ("PreToolUse", "PostToolUse"):
            return ToolUseEvent(
                **base,
                payload={
                    "agent": ctx.agent_name,
                    "tool_name": ctx.tool_name,
                    "phase": "pre" if event_type == "PreToolUse" else "post",
                    "duration_ms": ctx.duration_ms if event_type == "PostToolUse" else 0,
                },
            )
        elif event_type == "OnError":
            return AgentStatusChangeEvent(
                **base,
                payload={
                    "agent": ctx.agent_name,
                    "status": "error",
                    "error": ctx.error,
                },
            )
        return None
