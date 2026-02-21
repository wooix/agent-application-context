"""AgentScanner — resources/ 디렉토리 자동 스캔 (FR-1.2).

Spring의 @ComponentScan에 해당하며,
resources/{agents,tools,skills,aspects}/ 하위를 순회하여
YAML 파일을 파싱하고 Pydantic 모델로 검증한다.

부팅 시 호출 순서:
  scanner.scan_all() → tool/skill/aspect/agent manifest 목록 반환
  → 각 registry에 등록
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
import structlog
from pydantic import ValidationError

from aac.models.manifest import (
    AgentManifest,
    AspectManifest,
    ResourceKind,
    SkillManifest,
    ToolManifest,
)

logger = structlog.get_logger()


@dataclass
class ScanResult:
    """스캔 결과 — 파싱/검증된 manifest 목록 + 에러."""

    agents: list[AgentManifest] = field(default_factory=list)
    tools: list[ToolManifest] = field(default_factory=list)
    skills: list[SkillManifest] = field(default_factory=list)
    aspects: list[AspectManifest] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)

    @property
    def total_tools(self) -> int:
        """전체 tool 수 (번들 내 개별 item 합산)."""
        return sum(len(t.spec.items) for t in self.tools)


@dataclass
class ScanError:
    """YAML 파싱/검증 에러 정보 (FR-2.2)."""

    file_path: str
    error_type: str
    message: str
    field: str | None = None


class AgentScanner:
    """resources/ 디렉토리 자동 스캔."""

    # 각 리소스 타입별 YAML 파일 이름 패턴
    _FILE_PATTERNS: dict[str, str] = {
        "agents": "agent.yaml",
        "tools": "tool.yaml",
        "skills": "skill.yaml",
    }

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def scan_all(self) -> ScanResult:
        """resources/ 전체 스캔 → ScanResult."""
        result = ScanResult()

        # tools (먼저 스캔 — agent의 tool ref 해석에 필요)
        tools_dir = self._base_dir / "tools"
        if tools_dir.exists():
            for manifest in self._scan_directory(tools_dir, "tool.yaml", ToolManifest, result):
                result.tools.append(manifest)

        # skills
        skills_dir = self._base_dir / "skills"
        if skills_dir.exists():
            for manifest in self._scan_directory(skills_dir, "skill.yaml", SkillManifest, result):
                result.skills.append(manifest)

        # aspects (파일 패턴이 다름 — 디렉토리가 아닌 직접 yaml)
        aspects_dir = self._base_dir / "aspects"
        if aspects_dir.exists():
            for yaml_file in sorted(aspects_dir.glob("*.yaml")):
                parsed = self._parse_yaml(yaml_file, AspectManifest, result)
                if parsed:
                    result.aspects.append(parsed)

        # agents (마지막 — 의존성 해석 준비용)
        agents_dir = self._base_dir / "agents"
        if agents_dir.exists():
            for manifest in self._scan_directory(agents_dir, "agent.yaml", AgentManifest, result):
                result.agents.append(manifest)

        return result

    def _scan_directory(
        self,
        parent_dir: Path,
        filename: str,
        model_cls: type,
        result: ScanResult,
    ) -> list:
        """parent_dir 하위 디렉토리를 순회하며 filename을 파싱."""
        manifests = []
        for subdir in sorted(parent_dir.iterdir()):
            if not subdir.is_dir():
                continue
            yaml_file = subdir / filename
            if yaml_file.exists():
                parsed = self._parse_yaml(yaml_file, model_cls, result)
                if parsed:
                    manifests.append(parsed)
        return manifests

    def _parse_yaml(
        self,
        yaml_file: Path,
        model_cls: type,
        result: ScanResult,
    ) -> object | None:
        """YAML 파일을 파싱하고 Pydantic 모델로 검증."""
        file_str = str(yaml_file)
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            if raw is None:
                result.errors.append(ScanError(
                    file_path=file_str,
                    error_type="EmptyFile",
                    message="YAML 파일이 비어있습니다",
                ))
                return None

            # kind 필드 검증
            kind = raw.get("kind")
            expected_kind = self._expected_kind(model_cls)
            if kind and expected_kind and kind != expected_kind:
                result.errors.append(ScanError(
                    file_path=file_str,
                    error_type="KindMismatch",
                    message=f"kind '{kind}'이 예상과 다릅니다 (기대: '{expected_kind}')",
                    field="kind",
                ))
                return None

            manifest = model_cls.model_validate(raw)
            manifest.source_path = file_str
            logger.debug("yaml_parsed", file=file_str, kind=kind)
            return manifest

        except yaml.YAMLError as e:
            result.errors.append(ScanError(
                file_path=file_str,
                error_type="YAMLSyntax",
                message=str(e),
            ))
        except ValidationError as e:
            for err in e.errors():
                result.errors.append(ScanError(
                    file_path=file_str,
                    error_type="ValidationError",
                    message=err["msg"],
                    field=" → ".join(str(loc) for loc in err["loc"]),
                ))
        except Exception as e:
            result.errors.append(ScanError(
                file_path=file_str,
                error_type=type(e).__name__,
                message=str(e),
            ))
        return None

    @staticmethod
    def _expected_kind(model_cls: type) -> str | None:
        if model_cls is AgentManifest:
            return ResourceKind.AGENT.value
        if model_cls is ToolManifest:
            return ResourceKind.TOOL.value
        if model_cls is SkillManifest:
            return ResourceKind.SKILL.value
        if model_cls is AspectManifest:
            return ResourceKind.ASPECT.value
        return None
