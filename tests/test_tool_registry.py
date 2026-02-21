"""ToolRegistry 단위 테스트 — DR-1 충돌 해결."""

from __future__ import annotations

import pytest

from aac.di.tool_registry import ToolRegistry
from aac.models.manifest import ToolItem, ToolManifest, ToolMetadata, ToolSpec, ToolRef


def _make_tool_manifest(name: str, items: list[str]) -> ToolManifest:
    """테스트용 ToolManifest 생성 헬퍼."""
    return ToolManifest(
        metadata=ToolMetadata(name=name),
        spec=ToolSpec(
            items=[ToolItem(name=n, description=f"{n} 도구") for n in items]
        ),
    )


class TestRegister:
    """register() — 번들 등록."""

    def test_번들_등록(self) -> None:
        """Tool 번들이 정상 등록되어야 한다."""
        registry = ToolRegistry()
        manifest = _make_tool_manifest("file-ops", ["Read", "Write"])

        registry.register(manifest)

        assert registry.has("file-ops")
        assert len(registry) == 1

    def test_동일_이름_번들_덮어쓰기(self) -> None:
        """같은 이름의 번들을 등록하면 덮어쓰기해야 한다."""
        registry = ToolRegistry()
        m1 = _make_tool_manifest("file-ops", ["Read"])
        m2 = _make_tool_manifest("file-ops", ["Read", "Write", "Edit"])

        registry.register(m1)
        registry.register(m2)

        assert len(registry) == 1
        result = registry.get("file-ops")
        assert len(result.spec.items) == 3

    def test_미등록_번들_조회_에러(self) -> None:
        """등록되지 않은 번들을 조회하면 KeyError가 발생해야 한다."""
        registry = ToolRegistry()

        with pytest.raises(KeyError, match="미등록"):
            registry.get("nonexistent")


class TestResolveTools:
    """resolve_tools() — ToolRef 해석 + DR-1 충돌 규칙."""

    def test_ref_해석(self) -> None:
        """ref로 참조하면 번들 전체 item이 해석되어야 한다."""
        registry = ToolRegistry()
        registry.register(_make_tool_manifest("file-ops", ["Read", "Write", "Edit"]))

        refs = [ToolRef(ref="file-ops")]
        tools = registry.resolve_tools(refs)

        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"Read", "Write", "Edit"}

    def test_name_해석(self) -> None:
        """name으로 참조하면 개별 tool이 해석되어야 한다."""
        registry = ToolRegistry()

        refs = [ToolRef(name="WebSearch")]
        tools = registry.resolve_tools(refs)

        assert len(tools) == 1
        assert tools[0].name == "WebSearch"
        assert tools[0].bundle_name is None

    def test_ref_and_name_혼합(self) -> None:
        """ref와 name을 혼합해서 사용할 수 있어야 한다."""
        registry = ToolRegistry()
        registry.register(_make_tool_manifest("file-ops", ["Read", "Write"]))

        refs = [ToolRef(ref="file-ops"), ToolRef(name="WebSearch")]
        tools = registry.resolve_tools(refs)

        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"Read", "Write", "WebSearch"}

    def test_bundle_name_설정(self) -> None:
        """ref로 해석된 tool에는 bundle_name이 설정되어야 한다."""
        registry = ToolRegistry()
        registry.register(_make_tool_manifest("file-ops", ["Read"]))

        tools = registry.resolve_tools([ToolRef(ref="file-ops")])

        assert tools[0].bundle_name == "file-ops"
        assert tools[0].qualified_name == "file-ops/Read"

    def test_미등록_번들_ref_에러(self) -> None:
        """미등록 번들을 ref로 참조하면 KeyError가 발생해야 한다."""
        registry = ToolRegistry()

        with pytest.raises(KeyError, match="미등록"):
            registry.resolve_tools([ToolRef(ref="nonexistent")])


class TestDR1ConflictResolution:
    """DR-1: 번들 간 Tool 이름 충돌 해결."""

    def test_last_wins_기본(self) -> None:
        """기본 모드: 번들 간 이름 충돌 시 마지막 등록이 우선해야 한다."""
        registry = ToolRegistry(strict=False)
        registry.register(_make_tool_manifest("bundle-a", ["Read", "Write"]))
        registry.register(_make_tool_manifest("bundle-b", ["Read", "Search"]))

        refs = [ToolRef(ref="bundle-a"), ToolRef(ref="bundle-b")]
        tools = registry.resolve_tools(refs)

        # Read는 bundle-b에서 last-wins
        read_tool = next(t for t in tools if t.name == "Read")
        assert read_tool.bundle_name == "bundle-b"

        # 총 3개: Write(a), Read(b), Search(b)
        assert len(tools) == 3

    def test_strict_모드_충돌_에러(self) -> None:
        """strict 모드: 번들 간 이름 충돌 시 ValueError가 발생해야 한다."""
        registry = ToolRegistry(strict=True)
        registry.register(_make_tool_manifest("bundle-a", ["Read"]))
        registry.register(_make_tool_manifest("bundle-b", ["Read"]))

        refs = [ToolRef(ref="bundle-a"), ToolRef(ref="bundle-b")]

        with pytest.raises(ValueError, match="strict 모드"):
            registry.resolve_tools(refs)

    def test_strict_모드_name_충돌(self) -> None:
        """strict 모드: name 참조 간 충돌에서도 에러가 발생해야 한다."""
        registry = ToolRegistry(strict=True)

        refs = [ToolRef(name="Read"), ToolRef(name="Read")]

        with pytest.raises(ValueError, match="strict 모드"):
            registry.resolve_tools(refs)

    def test_충돌_없는_경우(self) -> None:
        """이름이 겹치지 않으면 충돌 없이 해석되어야 한다."""
        registry = ToolRegistry(strict=True)
        registry.register(_make_tool_manifest("bundle-a", ["Read"]))
        registry.register(_make_tool_manifest("bundle-b", ["Search"]))

        refs = [ToolRef(ref="bundle-a"), ToolRef(ref="bundle-b")]
        tools = registry.resolve_tools(refs)

        assert len(tools) == 2


class TestListAll:
    """list_all() / total_tool_count."""

    def test_list_all(self) -> None:
        """등록된 번들 목록과 item 수를 반환해야 한다."""
        registry = ToolRegistry()
        registry.register(_make_tool_manifest("file-ops", ["Read", "Write"]))
        registry.register(_make_tool_manifest("web", ["Search"]))

        result = registry.list_all()

        assert result == {"file-ops": 2, "web": 1}

    def test_total_tool_count(self) -> None:
        """전체 tool 수를 정확히 반환해야 한다."""
        registry = ToolRegistry()
        registry.register(_make_tool_manifest("a", ["T1", "T2"]))
        registry.register(_make_tool_manifest("b", ["T3"]))

        assert registry.total_tool_count == 3
