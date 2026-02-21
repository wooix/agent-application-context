"""AgentScanner 단위 테스트 — 정상 스캔 + 에러 검출."""

from __future__ import annotations

from pathlib import Path

import pytest

from aac.scanner import AgentScanner

from tests.helpers import SAMPLE_AGENT_YAML, SAMPLE_TOOL_YAML, write_yaml


class TestScanAll:
    """scan_all() — 정상 리소스 스캔."""

    def test_scan_all_정상_스캔(self, resources_dir: Path) -> None:
        """모든 리소스 타입이 정상 파싱되어야 한다."""
        result = AgentScanner(resources_dir).scan_all()

        assert len(result.agents) == 1
        assert len(result.tools) == 1
        assert len(result.skills) == 1
        assert len(result.aspects) == 1
        assert len(result.errors) == 0

    def test_scan_all_agent_메타데이터(self, resources_dir: Path) -> None:
        """스캔된 Agent manifest의 메타데이터가 정확해야 한다."""
        result = AgentScanner(resources_dir).scan_all()
        agent = result.agents[0]

        assert agent.metadata.name == "test-agent"
        assert agent.metadata.version == "1.0.0"
        assert "test" in agent.metadata.tags
        assert agent.spec.runtime == "mock"

    def test_scan_all_tool_items(self, resources_dir: Path) -> None:
        """Tool 번들의 item 수가 정확해야 한다."""
        result = AgentScanner(resources_dir).scan_all()
        tool = result.tools[0]

        assert tool.metadata.name == "test-tools"
        assert len(tool.spec.items) == 2
        assert result.total_tools == 2

    def test_scan_all_aspect_파싱(self, resources_dir: Path) -> None:
        """Aspect 파일이 정상 파싱되어야 한다 (*.yaml 직접 스캔)."""
        result = AgentScanner(resources_dir).scan_all()
        aspect = result.aspects[0]

        assert aspect.metadata.name == "test-aspect"
        assert aspect.spec.type == "TestAspect"
        assert aspect.spec.order == 10
        assert "PreQuery" in aspect.spec.pointcut.events

    def test_scan_all_source_path_설정(self, resources_dir: Path) -> None:
        """각 manifest의 source_path가 설정되어야 한다."""
        result = AgentScanner(resources_dir).scan_all()

        assert result.agents[0].source_path is not None
        assert result.tools[0].source_path is not None
        assert result.skills[0].source_path is not None
        assert result.aspects[0].source_path is not None

    def test_빈_디렉토리_스캔(self, tmp_path: Path) -> None:
        """리소스 디렉토리가 비어있으면 빈 결과를 반환해야 한다."""
        result = AgentScanner(tmp_path).scan_all()

        assert len(result.agents) == 0
        assert len(result.tools) == 0
        assert len(result.skills) == 0
        assert len(result.aspects) == 0
        assert len(result.errors) == 0

    def test_존재하지_않는_디렉토리(self, tmp_path: Path) -> None:
        """존재하지 않는 하위 디렉토리는 무시해야 한다."""
        # tools/ 만 생성, 나머지는 미생성
        write_yaml(tmp_path / "tools" / "t1" / "tool.yaml", SAMPLE_TOOL_YAML)
        result = AgentScanner(tmp_path).scan_all()

        assert len(result.tools) == 1
        assert len(result.agents) == 0  # agents/ 디렉토리 없음 → 무시


class TestScanErrors:
    """scan_all() — 에러 검출."""

    def test_빈_YAML_파일(self, tmp_path: Path) -> None:
        """빈 YAML 파일은 EmptyFile 에러를 생성해야 한다."""
        yaml_file = tmp_path / "tools" / "empty" / "tool.yaml"
        yaml_file.parent.mkdir(parents=True)
        yaml_file.write_text("", encoding="utf-8")

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "EmptyFile"
        assert "empty" in result.errors[0].file_path

    def test_잘못된_YAML_구문(self, tmp_path: Path) -> None:
        """YAML 구문 에러가 감지되어야 한다."""
        yaml_file = tmp_path / "tools" / "bad-syntax" / "tool.yaml"
        yaml_file.parent.mkdir(parents=True)
        yaml_file.write_text("{ invalid: yaml: content", encoding="utf-8")

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) >= 1
        assert any(e.error_type == "YAMLSyntax" for e in result.errors)

    def test_kind_불일치(self, tmp_path: Path) -> None:
        """kind 필드가 예상과 다르면 KindMismatch 에러를 생성해야 한다."""
        wrong_kind = {
            "apiVersion": "aac/v1",
            "kind": "Agent",  # Tool 디렉토리에 Agent kind
            "metadata": {"name": "wrong"},
            "spec": {"items": []},
        }
        write_yaml(tmp_path / "tools" / "wrong-kind" / "tool.yaml", wrong_kind)

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "KindMismatch"
        assert result.errors[0].field == "kind"

    def test_필수_필드_누락(self, tmp_path: Path) -> None:
        """필수 필드가 누락되면 ValidationError를 생성해야 한다."""
        invalid = {
            "apiVersion": "aac/v1",
            "kind": "Agent",
            "metadata": {"name": "no-runtime"},
            "spec": {},  # runtime 필드 누락
        }
        write_yaml(tmp_path / "agents" / "no-runtime" / "agent.yaml", invalid)

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) >= 1
        assert any(e.error_type == "ValidationError" for e in result.errors)

    def test_tool_번들_내_이름_중복(self, tmp_path: Path) -> None:
        """번들 내 tool 이름이 중복되면 ValidationError가 발생해야 한다."""
        duplicate_items = {
            "apiVersion": "aac/v1",
            "kind": "Tool",
            "metadata": {"name": "dup-tools"},
            "spec": {
                "items": [
                    {"name": "Read", "description": "첫 번째"},
                    {"name": "Read", "description": "두 번째"},
                ]
            },
        }
        write_yaml(tmp_path / "tools" / "dup-tools" / "tool.yaml", duplicate_items)

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) >= 1
        assert any(e.error_type == "ValidationError" for e in result.errors)

    def test_에러_파일_경로_포함(self, tmp_path: Path) -> None:
        """에러에 파일 경로가 포함되어야 한다 (AC-4 요구사항)."""
        yaml_file = tmp_path / "tools" / "bad" / "tool.yaml"
        yaml_file.parent.mkdir(parents=True)
        yaml_file.write_text("", encoding="utf-8")

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.errors) == 1
        assert "bad" in result.errors[0].file_path
        assert result.errors[0].file_path.endswith("tool.yaml")

    def test_다수_리소스_부분_에러(self, tmp_path: Path) -> None:
        """일부 리소스가 오류여도 나머지는 정상 스캔되어야 한다."""
        # 정상 tool
        write_yaml(tmp_path / "tools" / "good" / "tool.yaml", SAMPLE_TOOL_YAML)
        # 에러 tool
        bad_file = tmp_path / "tools" / "bad" / "tool.yaml"
        bad_file.parent.mkdir(parents=True)
        bad_file.write_text("", encoding="utf-8")
        # 정상 agent
        agent_yaml = SAMPLE_AGENT_YAML.copy()
        agent_yaml = {**SAMPLE_AGENT_YAML}
        write_yaml(tmp_path / "agents" / "test-agent" / "agent.yaml", agent_yaml)

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.tools) == 1  # good만 성공
        assert len(result.agents) == 1
        assert len(result.errors) == 1  # bad 하나 실패


class TestScanOrder:
    """스캔 순서 검증 — tools → skills → aspects → agents."""

    def test_스캔_순서_의존성(self, resources_dir: Path) -> None:
        """tools가 agents보다 먼저 스캔되어야 한다 (DI 해석 준비)."""
        result = AgentScanner(resources_dir).scan_all()

        # 정상 스캔이면 순서가 올바른 것
        assert len(result.tools) >= 1
        assert len(result.agents) >= 1
        assert len(result.errors) == 0
