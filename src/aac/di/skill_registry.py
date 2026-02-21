"""SkillRegistry — Skill 문서 등록/조회/DI 해석 (FR-4.1~4.3).

resources/skills/ 하위의 skill.yaml 파일들을 파싱하여 등록하고,
Agent 생성 시 ref로 참조된 Skill의 instruction 문서를 로드하여
system_prompt에 합성한다.

합성 규칙 (DR-2):
- 순서: system_prompt → prompt_file → skill 문서들 (skills 배열 순서)
- 구분자: \\n---\\n## Skill: {name}\\n
- 중복 skill 무시
- 토큰 초과 시 마지막 skill부터 절삭 + 경고
"""

from __future__ import annotations

from pathlib import Path

import structlog

from aac.models.manifest import SkillManifest, SkillRef

logger = structlog.get_logger()


class SkillRegistry:
    """Skill 문서 레지스트리."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}
        self._instruction_cache: dict[str, str] = {}

    def register(self, manifest: SkillManifest) -> None:
        """Skill 등록."""
        name = manifest.metadata.name
        if name in self._skills:
            logger.warning("skill_override", name=name)
        self._skills[name] = manifest
        logger.debug("skill_registered", name=name)

    def get(self, name: str) -> SkillManifest:
        """이름으로 Skill 조회."""
        if name not in self._skills:
            available = list(self._skills.keys())
            raise KeyError(f"Skill '{name}' 미등록. 사용 가능: {available}")
        return self._skills[name]

    def has(self, name: str) -> bool:
        return name in self._skills

    def load_instruction(self, name: str) -> str:
        """Skill instruction 문서 로드 (캐싱).

        skill.yaml의 source_path를 기준으로 instruction_file 경로를 해석한다.
        """
        if name in self._instruction_cache:
            return self._instruction_cache[name]

        manifest = self.get(name)
        if not manifest.source_path:
            raise ValueError(f"Skill '{name}'의 source_path가 설정되지 않았습니다")

        base_dir = Path(manifest.source_path).parent
        instruction_path = base_dir / manifest.spec.instruction_file

        if not instruction_path.exists():
            raise FileNotFoundError(
                f"Skill '{name}'의 instruction 파일을 찾을 수 없습니다: {instruction_path}"
            )

        content = instruction_path.read_text(encoding="utf-8")
        self._instruction_cache[name] = content
        logger.debug("skill_instruction_loaded", name=name, path=str(instruction_path))
        return content

    def resolve_skills(
        self,
        skill_refs: list[SkillRef],
        available_tools: set[str],
        *,
        strict: bool = False,
    ) -> list[str]:
        """SkillRef 목록 → instruction 문서 목록으로 해석.

        required_tools 충족 검사 (FR-4.2):
        - strict=false: 미충족 시 경고
        - strict=true: 미충족 시 예외
        """
        instructions: list[str] = []
        seen: set[str] = set()

        for ref in skill_refs:
            name = ref.ref
            if name in seen:
                logger.debug("skill_duplicate_skipped", name=name)
                continue
            seen.add(name)

            manifest = self.get(name)

            # required_tools 검사 (FR-4.2)
            for req_tool in manifest.spec.required_tools:
                if not self._tool_available(req_tool, available_tools):
                    msg = (
                        f"Skill '{name}'이 요구하는 Tool '{req_tool}'이 "
                        f"agent에 주입되지 않았습니다"
                    )
                    if strict:
                        raise ValueError(msg)
                    logger.warning("skill_required_tool_missing", skill=name, tool=req_tool)

            instruction = self.load_instruction(name)
            instructions.append(f"\n---\n## Skill: {name}\n\n{instruction}")

        return instructions

    def _tool_available(self, tool_name: str, available_tools: set[str]) -> bool:
        """tool 이름이 available_tools에 존재하는지 확인.

        번들 이름(file-ops)이나 개별 tool 이름(Read) 모두 매칭.
        """
        return tool_name in available_tools

    def list_all(self) -> list[str]:
        """등록된 Skill 이름 목록."""
        return list(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)
