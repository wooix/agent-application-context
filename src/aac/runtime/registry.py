"""RuntimeRegistry — LLM Runtime 어댑터 등록/조회.

Spring의 BeanFactory가 DataSource를 관리하듯,
RuntimeRegistry는 사용 가능한 Runtime 클래스를 등록하고
agent.yaml의 runtime 필드 값으로 조회한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aac.runtime.base import AgentRuntime

import structlog

logger = structlog.get_logger()


class RuntimeRegistry:
    """Runtime 어댑터 레지스트리 — 이름 → Runtime 클래스 매핑."""

    def __init__(self) -> None:
        self._registry: dict[str, type[AgentRuntime]] = {}

    def register(self, name: str, runtime_cls: type[AgentRuntime]) -> None:
        """Runtime 클래스 등록."""
        if name in self._registry:
            logger.warning("runtime_override", name=name, new=runtime_cls.__name__)
        self._registry[name] = runtime_cls
        logger.debug("runtime_registered", name=name, cls=runtime_cls.__name__)

    def get(self, name: str) -> type[AgentRuntime]:
        """이름으로 Runtime 클래스 조회."""
        if name not in self._registry:
            available = list(self._registry.keys())
            raise KeyError(
                f"Runtime '{name}' 미등록. 사용 가능: {available}"
            )
        return self._registry[name]

    def has(self, name: str) -> bool:
        return name in self._registry

    def list_all(self) -> dict[str, str]:
        """등록된 Runtime 목록 — {name: class_name}."""
        return {name: cls.__name__ for name, cls in self._registry.items()}

    def __len__(self) -> int:
        return len(self._registry)
