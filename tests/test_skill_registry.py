"""SkillRegistry 단위 테스트 — DR-2 문서 합성 + required_tools 검증."""

from __future__ import annotations

from pathlib import Path

import pytest

from aac.di.skill_registry import SkillRegistry
from aac.models.manifest import SkillManifest, SkillMetadata, SkillRef, SkillSpec


def _make_skill_manifest(
    name: str,
    instruction_file: str = "./SKILL.md",
    required_tools: list[str] | None = None,
    source_path: str | None = None,
) -> SkillManifest:
    """테스트용 SkillManifest 생성 헬퍼."""
    m = SkillManifest(
        metadata=SkillMetadata(name=name, description=f"{name} 스킬"),
        spec=SkillSpec(
            instruction_file=instruction_file,
            required_tools=required_tools or [],
        ),
    )
    m.source_path = source_path
    return m


class TestRegister:
    """register() — Skill 등록."""

    def test_스킬_등록(self) -> None:
        registry = SkillRegistry()
        manifest = _make_skill_manifest("code-review")

        registry.register(manifest)

        assert registry.has("code-review")
        assert len(registry) == 1

    def test_동일_이름_덮어쓰기(self) -> None:
        registry = SkillRegistry()
        m1 = _make_skill_manifest("code-review")
        m2 = _make_skill_manifest("code-review")

        registry.register(m1)
        registry.register(m2)

        assert len(registry) == 1

    def test_미등록_스킬_조회_에러(self) -> None:
        registry = SkillRegistry()

        with pytest.raises(KeyError, match="미등록"):
            registry.get("nonexistent")


class TestLoadInstruction:
    """load_instruction() — SKILL.md 로드 + 캐싱."""

    def test_정상_로드(self, tmp_path: Path) -> None:
        """SKILL.md를 정상 읽어야 한다."""
        registry = SkillRegistry()
        skill_dir = tmp_path / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# 리뷰 지침", encoding="utf-8")

        manifest = _make_skill_manifest(
            "review",
            source_path=str(skill_dir / "skill.yaml"),
        )
        registry.register(manifest)

        content = registry.load_instruction("review")

        assert "리뷰 지침" in content

    def test_캐싱(self, tmp_path: Path) -> None:
        """같은 skill을 두 번 로드하면 캐시를 사용해야 한다."""
        registry = SkillRegistry()
        skill_dir = tmp_path / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# 원본", encoding="utf-8")

        manifest = _make_skill_manifest(
            "review",
            source_path=str(skill_dir / "skill.yaml"),
        )
        registry.register(manifest)

        # 첫 로드
        content1 = registry.load_instruction("review")
        # 파일 변경
        (skill_dir / "SKILL.md").write_text("# 변경됨", encoding="utf-8")
        # 두 번째 로드 → 캐시에서 반환
        content2 = registry.load_instruction("review")

        assert content1 == content2
        assert "원본" in content2

    def test_source_path_미설정_에러(self) -> None:
        """source_path가 없으면 ValueError가 발생해야 한다."""
        registry = SkillRegistry()
        manifest = _make_skill_manifest("no-path", source_path=None)
        registry.register(manifest)

        with pytest.raises(ValueError, match="source_path"):
            registry.load_instruction("no-path")

    def test_instruction_파일_없음_에러(self, tmp_path: Path) -> None:
        """instruction 파일이 없으면 FileNotFoundError가 발생해야 한다."""
        registry = SkillRegistry()
        skill_dir = tmp_path / "skills" / "missing"
        skill_dir.mkdir(parents=True)
        # SKILL.md는 생성하지 않음

        manifest = _make_skill_manifest(
            "missing",
            source_path=str(skill_dir / "skill.yaml"),
        )
        registry.register(manifest)

        with pytest.raises(FileNotFoundError, match="찾을 수 없습니다"):
            registry.load_instruction("missing")


class TestResolveSkills:
    """resolve_skills() — DR-2 문서 합성 + required_tools 검증."""

    def _setup_registry(self, tmp_path: Path) -> SkillRegistry:
        """테스트용 레지스트리 + 실제 파일 세팅."""
        registry = SkillRegistry()

        # skill 1: required_tools 있음
        s1_dir = tmp_path / "skills" / "review"
        s1_dir.mkdir(parents=True)
        (s1_dir / "SKILL.md").write_text("# 코드 리뷰 지침", encoding="utf-8")
        m1 = _make_skill_manifest(
            "review",
            required_tools=["file-ops"],
            source_path=str(s1_dir / "skill.yaml"),
        )
        registry.register(m1)

        # skill 2: required_tools 없음
        s2_dir = tmp_path / "skills" / "security"
        s2_dir.mkdir(parents=True)
        (s2_dir / "SKILL.md").write_text("# 보안 감사 지침", encoding="utf-8")
        m2 = _make_skill_manifest(
            "security",
            required_tools=[],
            source_path=str(s2_dir / "skill.yaml"),
        )
        registry.register(m2)

        return registry

    def test_정상_해석(self, tmp_path: Path) -> None:
        """required_tools 충족 시 instruction을 정상 반환해야 한다."""
        registry = self._setup_registry(tmp_path)

        refs = [SkillRef(ref="review"), SkillRef(ref="security")]
        instructions = registry.resolve_skills(refs, {"file-ops", "Read"})

        assert len(instructions) == 2
        assert any("코드 리뷰 지침" in i for i in instructions)
        assert any("보안 감사 지침" in i for i in instructions)

    def test_문서_포맷(self, tmp_path: Path) -> None:
        """합성된 문서에 구분자와 Skill 이름이 포함되어야 한다."""
        registry = self._setup_registry(tmp_path)

        refs = [SkillRef(ref="review")]
        instructions = registry.resolve_skills(refs, {"file-ops"})

        assert instructions[0].startswith("\n---\n## Skill: review")

    def test_중복_스킬_무시(self, tmp_path: Path) -> None:
        """같은 skill을 여러 번 참조하면 첫 번째만 처리해야 한다."""
        registry = self._setup_registry(tmp_path)

        refs = [SkillRef(ref="review"), SkillRef(ref="review")]
        instructions = registry.resolve_skills(refs, {"file-ops"})

        assert len(instructions) == 1

    def test_required_tools_미충족_경고(self, tmp_path: Path) -> None:
        """기본 모드: required_tools 미충족 시 에러 없이 경고만."""
        registry = self._setup_registry(tmp_path)

        refs = [SkillRef(ref="review")]
        # file-ops를 제공하지 않음
        instructions = registry.resolve_skills(refs, set())

        # 에러 없이 반환되어야 함 (경고 로그만)
        assert len(instructions) == 1

    def test_required_tools_미충족_strict(self, tmp_path: Path) -> None:
        """strict 모드: required_tools 미충족 시 ValueError 발생."""
        registry = self._setup_registry(tmp_path)

        refs = [SkillRef(ref="review")]
        with pytest.raises(ValueError, match="요구하는 Tool"):
            registry.resolve_skills(refs, set(), strict=True)


class TestListAll:
    """list_all() — 등록된 Skill 이름 목록."""

    def test_목록_반환(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill_manifest("a"))
        registry.register(_make_skill_manifest("b"))

        result = registry.list_all()

        assert set(result) == {"a", "b"}
