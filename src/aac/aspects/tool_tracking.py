"""ToolTrackingAspect — Tool 사용 통계 (FR-7.5).

PreToolUse/PostToolUse 이벤트에서 Tool 호출 횟수와 소요 시간을 추적한다.
Agent별 tool 사용 통계를 메모리에 집계한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

from aac.aspects.engine import AspectContext, AspectEventType, AspectHandler
from aac.models.manifest import AspectManifest

logger = structlog.get_logger()


@dataclass
class ToolStats:
    """개별 Tool 사용 통계."""

    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    total_duration_ms: int = 0


class ToolTrackingHandler(AspectHandler):
    """Tool 사용 통계 추적 Aspect."""

    def __init__(self, manifest: AspectManifest) -> None:
        super().__init__(manifest)
        # agent_name → tool_name → ToolStats
        self._stats: dict[str, dict[str, ToolStats]] = defaultdict(
            lambda: defaultdict(ToolStats)
        )

    async def handle(self, event_type: str, ctx: AspectContext) -> None:
        if not ctx.tool_name:
            return

        stats = self._stats[ctx.agent_name][ctx.tool_name]

        if event_type == AspectEventType.PRE_TOOL_USE:
            stats.call_count += 1
            logger.debug(
                "tool_tracking_call",
                agent=ctx.agent_name,
                tool=ctx.tool_name,
                count=stats.call_count,
            )
        elif event_type == AspectEventType.POST_TOOL_USE:
            if ctx.error:
                stats.error_count += 1
            else:
                stats.success_count += 1
            stats.total_duration_ms += ctx.duration_ms

    def get_stats(self, agent_name: str | None = None) -> dict[str, Any]:
        """통계 조회. agent_name 지정 시 해당 agent만."""
        if agent_name:
            return {
                tool: {
                    "call_count": s.call_count,
                    "success_count": s.success_count,
                    "error_count": s.error_count,
                    "total_duration_ms": s.total_duration_ms,
                }
                for tool, s in self._stats.get(agent_name, {}).items()
            }
        return {
            agent: {
                tool: {
                    "call_count": s.call_count,
                    "success_count": s.success_count,
                    "error_count": s.error_count,
                    "total_duration_ms": s.total_duration_ms,
                }
                for tool, s in tools.items()
            }
            for agent, tools in self._stats.items()
        }
