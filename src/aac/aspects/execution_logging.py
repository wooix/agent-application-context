"""ExecutionLoggingAspect — 실행 로그 출력 (FR-7.6).

[HH:mm:ss:SSS] [Agent] [session:tx] 형식으로 실행 이벤트를 콘솔에 출력한다.
"""

from __future__ import annotations

from typing import Any

import structlog

from aac.aspects.engine import AspectContext, AspectEventType, AspectHandler
from aac.logging.formatter import aac_log
from aac.models.manifest import AspectManifest

logger = structlog.get_logger()


class ExecutionLoggingHandler(AspectHandler):
    """실행 로그 콘솔 출력 Aspect."""

    def __init__(self, manifest: AspectManifest) -> None:
        super().__init__(manifest)

    async def handle(self, event_type: str, ctx: AspectContext) -> None:
        if event_type == AspectEventType.PRE_QUERY:
            aac_log(
                ctx.agent_name,
                ctx.session_id,
                ctx.tx_id,
                f"🎯 [ASPECT] PreQuery: prompt={ctx.prompt[:60]}...",
            )
        elif event_type == AspectEventType.POST_QUERY:
            status = "✓" if not ctx.error else "✗"
            aac_log(
                ctx.agent_name,
                ctx.session_id,
                ctx.tx_id,
                f"🎯 [ASPECT] PostQuery: {status} "
                f"({ctx.duration_ms}ms, ${ctx.cost_usd:.4f})",
            )
        elif event_type == AspectEventType.ON_ERROR:
            aac_log(
                ctx.agent_name,
                ctx.session_id,
                ctx.tx_id,
                f"🎯 [ASPECT] OnError: {ctx.error}",
            )
        elif event_type == AspectEventType.PRE_TOOL_USE:
            aac_log(
                ctx.agent_name,
                ctx.session_id,
                ctx.tx_id,
                f"🎯 [ASPECT] PreToolUse: {ctx.tool_name}",
            )
        elif event_type == AspectEventType.POST_TOOL_USE:
            aac_log(
                ctx.agent_name,
                ctx.session_id,
                ctx.tx_id,
                f"🎯 [ASPECT] PostToolUse: {ctx.tool_name} ({ctx.duration_ms}ms)",
            )
