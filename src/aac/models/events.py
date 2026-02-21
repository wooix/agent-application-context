"""WebSocket 이벤트 스키마 — 실시간 push용 (FR-9.3).

모든 이벤트는 schema_version, event_id, timestamp, session_id, tx_id를 포함한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _generate_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AACEvent(BaseModel):
    """AAC WebSocket 이벤트 기본 스키마."""

    schema_version: str = "1.0"
    event_id: str = Field(default_factory=_generate_event_id)
    timestamp: str = Field(default_factory=_now_iso)
    session_id: str = ""
    tx_id: str = ""
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentStatusChangeEvent(AACEvent):
    type: str = "agent_status_change"


class ToolUseEvent(AACEvent):
    type: str = "tool_use"


class QueryStartEvent(AACEvent):
    type: str = "query_start"


class QueryCompleteEvent(AACEvent):
    type: str = "query_complete"


class ContextBootEvent(AACEvent):
    type: str = "context_boot"
