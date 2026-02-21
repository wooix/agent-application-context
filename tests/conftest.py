"""공통 테스트 픽스처 — 임시 리소스 디렉토리 생성."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import (
    SAMPLE_AGENT_YAML,
    SAMPLE_ASPECT_YAML,
    SAMPLE_SKILL_YAML,
    SAMPLE_TOOL_YAML,
    write_yaml,
)


@pytest.fixture
def resources_dir(tmp_path: Path) -> Path:
    """완전한 샘플 리소스 디렉토리 생성."""
    # tools
    write_yaml(tmp_path / "tools" / "test-tools" / "tool.yaml", SAMPLE_TOOL_YAML)

    # skills (+ instruction 문서)
    write_yaml(tmp_path / "skills" / "test-skill" / "skill.yaml", SAMPLE_SKILL_YAML)
    skill_md = tmp_path / "skills" / "test-skill" / "SKILL.md"
    skill_md.write_text("# 테스트 스킬 지침\n\n코드를 리뷰하세요.", encoding="utf-8")

    # aspects
    write_yaml(tmp_path / "aspects" / "test-aspect.yaml", SAMPLE_ASPECT_YAML)

    # agents
    write_yaml(tmp_path / "agents" / "test-agent" / "agent.yaml", SAMPLE_AGENT_YAML)

    return tmp_path
