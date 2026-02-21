"""ToolRegistry — Tool 번들 등록/조회/DI 해석 (FR-3.1, FR-3.3).

resources/tools/ 하위의 tool.yaml 파일들을 파싱하여 등록하고,
Agent 생성 시 ref로 참조된 Tool 번들을 해석하여 ToolDefinition 목록을 반환한다.

충돌 규칙 (DR-1):
- 번들 내 이름 중복: Pydantic validator에서 검증 (ToolSpec.unique_item_names)
- 번들 간 이름 중복: last-wins + 경고 (strict_tools=true 시 실패)
"""

from __future__ import annotations

import structlog

from aac.models.instance import ToolDefinition
from aac.models.manifest import ToolManifest, ToolRef

logger = structlog.get_logger()


class ToolRegistry:
    """Tool 번들 레지스트리."""

    def __init__(self, *, strict: bool = False) -> None:
        self._bundles: dict[str, ToolManifest] = {}
        self._strict = strict

    def register(self, manifest: ToolManifest) -> None:
        """Tool 번들 등록."""
        name = manifest.metadata.name
        if name in self._bundles:
            logger.warning("tool_bundle_override", name=name)
        self._bundles[name] = manifest
        logger.debug(
            "tool_bundle_registered",
            name=name,
            item_count=len(manifest.spec.items),
        )

    def get(self, name: str) -> ToolManifest:
        """번들 이름으로 조회."""
        if name not in self._bundles:
            available = list(self._bundles.keys())
            raise KeyError(f"Tool 번들 '{name}' 미등록. 사용 가능: {available}")
        return self._bundles[name]

    def has(self, name: str) -> bool:
        return name in self._bundles

    def resolve_tools(self, tool_refs: list[ToolRef]) -> list[ToolDefinition]:
        """ToolRef 목록 → ToolDefinition 목록으로 해석 (DI 주입용).

        충돌 규칙 (DR-1): 번들 간 같은 이름 tool → last-wins + 경고.
        strict 모드에서는 충돌 시 예외.
        """
        resolved: dict[str, ToolDefinition] = {}
        for ref in tool_refs:
            if ref.ref:
                bundle = self.get(ref.ref)
                for item in bundle.spec.items:
                    tool_def = ToolDefinition(
                        name=item.name,
                        bundle_name=bundle.metadata.name,
                        description=item.description,
                        input_schema=item.input_schema,
                        output_schema=item.output_schema,
                        config=item.config,
                    )
                    if item.name in resolved:
                        existing = resolved[item.name]
                        if self._strict:
                            raise ValueError(
                                f"Tool 이름 충돌 (strict 모드): '{item.name}' "
                                f"— {existing.bundle_name} vs {bundle.metadata.name}"
                            )
                        logger.warning(
                            "tool_name_conflict",
                            tool=item.name,
                            existing_bundle=existing.bundle_name,
                            new_bundle=bundle.metadata.name,
                            resolution="last-wins",
                        )
                    resolved[item.name] = tool_def
            elif ref.name:
                tool_def = ToolDefinition(name=ref.name)
                if ref.name in resolved:
                    if self._strict:
                        raise ValueError(f"Tool 이름 충돌 (strict 모드): '{ref.name}'")
                    logger.warning("tool_name_conflict", tool=ref.name, resolution="last-wins")
                resolved[ref.name] = tool_def
        return list(resolved.values())

    def list_all(self) -> dict[str, int]:
        """등록된 번들 목록 — {name: item_count}."""
        return {name: len(m.spec.items) for name, m in self._bundles.items()}

    @property
    def total_tool_count(self) -> int:
        """전체 등록된 tool 수."""
        return sum(len(m.spec.items) for m in self._bundles.values())

    def __len__(self) -> int:
        return len(self._bundles)
