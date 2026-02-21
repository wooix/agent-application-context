"""AAC 통일 로그 포맷터 (FR-8.2).

포맷: [HH:mm:ss:SSS] [Agent] [sessionId:txId] msg
모든 실행 로그, Tool 사용 로그, 부트 로그가 이 형식을 따른다.
"""

from __future__ import annotations

from datetime import datetime


class AACLogFormatter:
    """통일 로그 포맷 생성기."""

    @staticmethod
    def format(
        agent_name: str,
        session_id: str,
        tx_id: str,
        msg: str,
    ) -> str:
        """[HH:mm:ss:SSS] [agent] [session:tx] msg 형식으로 포맷."""
        now = datetime.now()
        ts = now.strftime("%H:%M:%S") + f":{now.microsecond // 1000:03d}"
        return f"[{ts}] [{agent_name}] [{session_id}:{tx_id}] {msg}"

    @staticmethod
    def format_boot(msg: str) -> str:
        """부트 로그 — [HH:mm:ss:SSS] [AAC] [system:boot] msg."""
        return AACLogFormatter.format("AAC", "system", "boot", msg)

    @staticmethod
    def format_init(agent_name: str, msg: str) -> str:
        """초기화 로그 — [HH:mm:ss:SSS] [agent] [system:init] msg."""
        return AACLogFormatter.format(agent_name, "system", "init", msg)


def aac_log(agent_name: str, session_id: str, tx_id: str, msg: str) -> None:
    """포맷된 로그를 콘솔에 출력."""
    print(AACLogFormatter.format(agent_name, session_id, tx_id, msg))


def boot_log(msg: str) -> None:
    """부트 로그 출력."""
    print(AACLogFormatter.format_boot(msg))


def init_log(agent_name: str, msg: str) -> None:
    """초기화 로그 출력."""
    print(AACLogFormatter.format_init(agent_name, msg))
