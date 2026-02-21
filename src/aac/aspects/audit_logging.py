"""AuditLoggingAspect — SQLite 감사 로그 (FR-7.4).

모든 Agent 실행을 SQLite DB에 기록한다.
PreQuery에서 레코드 생성, PostQuery에서 결과 업데이트, OnError에서 에러 기록.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from aac.aspects.engine import AspectContext, AspectEventType, AspectHandler
from aac.models.manifest import AspectManifest

logger = structlog.get_logger()

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tx_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    prompt TEXT,
    response TEXT,
    cost_usd REAL DEFAULT 0.0,
    duration_ms INTEGER DEFAULT 0,
    model TEXT DEFAULT '',
    status TEXT DEFAULT 'running',
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""


class AuditLoggingHandler(AspectHandler):
    """SQLite 기반 감사 로그 Aspect."""

    def __init__(self, manifest: AspectManifest) -> None:
        super().__init__(manifest)
        self._db_path = self._config.get("db_path", "data/audit.db")
        self._summary_max_length = self._config.get("summary_max_length", 200)
        self._conn: sqlite3.Connection | None = None

    def _ensure_db(self) -> sqlite3.Connection:
        """DB 연결 보장 + 테이블 생성."""
        if self._conn is None:
            db_path = Path(self._db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path))
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.commit()
            logger.info("audit_db_initialized", path=str(db_path))
        return self._conn

    async def handle(self, event_type: str, ctx: AspectContext) -> None:
        """이벤트별 감사 로그 처리."""
        conn = self._ensure_db()

        if event_type == AspectEventType.PRE_QUERY:
            self._insert_execution(conn, ctx)
        elif event_type == AspectEventType.POST_QUERY:
            self._update_execution(conn, ctx)
        elif event_type == AspectEventType.ON_ERROR:
            self._update_error(conn, ctx)

    def _insert_execution(self, conn: sqlite3.Connection, ctx: AspectContext) -> None:
        """PreQuery: 실행 레코드 생성."""
        prompt_summary = ctx.prompt[:self._summary_max_length]
        conn.execute(
            """INSERT INTO executions (id, session_id, tx_id, agent_name, prompt, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)""",
            (
                ctx.execution_id,
                ctx.session_id,
                ctx.tx_id,
                ctx.agent_name,
                prompt_summary,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    def _update_execution(self, conn: sqlite3.Connection, ctx: AspectContext) -> None:
        """PostQuery: 결과 업데이트."""
        response_summary = ctx.response[:self._summary_max_length] if ctx.response else ""
        status = "error" if ctx.error else "completed"
        conn.execute(
            """UPDATE executions
            SET response = ?, cost_usd = ?, duration_ms = ?, model = ?,
                status = ?, error = ?, completed_at = ?
            WHERE id = ?""",
            (
                response_summary,
                ctx.cost_usd,
                ctx.duration_ms,
                ctx.model,
                status,
                ctx.error,
                datetime.now(timezone.utc).isoformat(),
                ctx.execution_id,
            ),
        )
        conn.commit()

    def _update_error(self, conn: sqlite3.Connection, ctx: AspectContext) -> None:
        """OnError: 에러 상태 기록."""
        conn.execute(
            """UPDATE executions SET status = 'error', error = ? WHERE id = ?""",
            (ctx.error, ctx.execution_id),
        )
        conn.commit()

    def close(self) -> None:
        """DB 연결 종료."""
        if self._conn:
            self._conn.close()
            self._conn = None
