"""RuntimeRegistry — LLM Runtime 어댑터 등록/조회.

Spring의 BeanFactory가 DataSource를 관리하듯,
RuntimeRegistry는 사용 가능한 Runtime 클래스를 등록하고
agent.yaml의 runtime 필드 값으로 조회한다.

Phase 2: discover()를 통해 resources/runtimes/*.yaml에서 선언적 자동 로드 지원.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aac.models.manifest import RuntimeManifest
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

    def discover(self, manifests: list[RuntimeManifest]) -> list[str]:
        """RuntimeManifest 목록에서 동적으로 Runtime 클래스를 로드하여 등록.

        resources/runtimes/*.yaml에서 선언된 Runtime을 자동 발견한다.
        module 경로와 class 이름으로 importlib을 사용해 동적 임포트.

        Returns:
            성공적으로 등록된 runtime 이름 목록.
        """
        registered: list[str] = []

        for manifest in manifests:
            name = manifest.metadata.name
            module_path = manifest.spec.module
            class_name = manifest.spec.class_name

            try:
                module = importlib.import_module(module_path)
                runtime_cls = getattr(module, class_name)

                self.register(name, runtime_cls)
                registered.append(name)
                logger.info(
                    "runtime_discovered",
                    name=name,
                    module=module_path,
                    cls=class_name,
                )
            except ImportError as e:
                logger.error(
                    "runtime_discover_import_error",
                    name=name,
                    module=module_path,
                    error=str(e),
                )
            except AttributeError as e:
                logger.error(
                    "runtime_discover_class_not_found",
                    name=name,
                    module=module_path,
                    cls=class_name,
                    error=str(e),
                )

        return registered

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
