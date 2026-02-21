"""Runtime 어댑터 + RuntimeRegistry 단위 테스트 (Phase 2).

3개 Runtime (Gemini, OpenAI, Codex)의 기본 동작 + RuntimeRegistry 자동 발견.
실제 CLI 호출은 하지 않고, 초기화/상태 전이/명령어 조립/출력 파싱을 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aac.models.manifest import RuntimeManifest, RuntimeMetadata, RuntimeSpec
from aac.runtime.base import ExecutionResult, RuntimeStatus
from aac.runtime.claude_code import ClaudeCodeRuntime
from aac.runtime.codex_cli import CodexCLIRuntime
from aac.runtime.gemini_mcp import GeminiMCPRuntime
from aac.runtime.openai_mcp import OpenAIMCPRuntime
from aac.runtime.registry import RuntimeRegistry
from aac.scanner import AgentScanner

from tests.helpers import write_yaml


# ─── GeminiMCPRuntime ────────────────────────────────


class TestGeminiMCPRuntime:
    """GeminiMCPRuntime 기본 동작 검증."""

    async def test_초기화(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({"model": "gemini-2.5-pro"})

        assert runtime.status == RuntimeStatus.READY
        assert runtime.name == "gemini-mcp"

    async def test_기본_모델(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({})

        assert runtime._model == "gemini-2.5-pro"

    async def test_명령어_조립(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({"model": "gemini-2.5-flash", "sandbox": True})

        cmd = runtime._build_command("테스트 프롬프트", "시스템 프롬프트")

        assert cmd[0] == "gemini"
        assert "-p" in cmd
        assert "테스트 프롬프트" in cmd
        assert "--model" in cmd
        assert "gemini-2.5-flash" in cmd
        assert "--system-prompt" in cmd
        assert "--sandbox" in cmd

    async def test_JSON_dict_파싱(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({})

        output = '{"response": "답변입니다", "cost_usd": 0.005, "model": "gemini-2.5-pro"}'
        result = runtime._parse_output(output, 200)

        assert result.response == "답변입니다"
        assert result.cost_usd == 0.005
        assert result.model == "gemini-2.5-pro"

    async def test_JSON_list_파싱(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({})

        output = '[{"type": "text", "text": "블록 응답"}]'
        result = runtime._parse_output(output, 100)

        assert result.response == "블록 응답"

    async def test_비JSON_파싱(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({})

        result = runtime._parse_output("일반 텍스트 응답", 50)

        assert result.response == "일반 텍스트 응답"

    async def test_shutdown(self) -> None:
        runtime = GeminiMCPRuntime()
        await runtime.initialize({})
        await runtime.shutdown()

        assert runtime.status == RuntimeStatus.SHUTDOWN


# ─── OpenAIMCPRuntime ────────────────────────────────


class TestOpenAIMCPRuntime:
    """OpenAIMCPRuntime 기본 동작 검증."""

    async def test_초기화(self) -> None:
        runtime = OpenAIMCPRuntime()
        await runtime.initialize({"model": "gpt-4o"})

        assert runtime.status == RuntimeStatus.READY
        assert runtime.name == "openai-mcp"

    async def test_명령어_조립(self) -> None:
        runtime = OpenAIMCPRuntime()
        await runtime.initialize({"model": "gpt-4o"})

        cmd = runtime._build_command("테스트", "시스템")

        assert cmd[0] == "openai"
        assert "api" in cmd
        assert "chat.completions.create" in cmd
        assert "gpt-4o" in cmd

    async def test_ChatCompletion_파싱(self) -> None:
        runtime = OpenAIMCPRuntime()
        await runtime.initialize({})

        output = (
            '{"choices": [{"message": {"content": "GPT 응답"}}], '
            '"usage": {"prompt_tokens": 10, "completion_tokens": 20}, '
            '"model": "gpt-4o"}'
        )
        result = runtime._parse_output(output, 300)

        assert result.response == "GPT 응답"
        assert result.tokens_in == 10
        assert result.tokens_out == 20

    async def test_shutdown(self) -> None:
        runtime = OpenAIMCPRuntime()
        await runtime.initialize({})
        await runtime.shutdown()

        assert runtime.status == RuntimeStatus.SHUTDOWN


# ─── CodexCLIRuntime ─────────────────────────────────


class TestCodexCLIRuntime:
    """CodexCLIRuntime 기본 동작 검증."""

    async def test_초기화(self) -> None:
        runtime = CodexCLIRuntime()
        await runtime.initialize({"model": "codex-mini"})

        assert runtime.status == RuntimeStatus.READY
        assert runtime.name == "codex-cli"

    async def test_명령어_조립(self) -> None:
        runtime = CodexCLIRuntime()
        await runtime.initialize({"model": "codex-mini", "approval_mode": "full-auto"})

        cmd = runtime._build_command("코드 생성", "")

        assert cmd[0] == "codex"
        assert "codex-mini" in cmd
        assert "--approval-mode" in cmd
        assert "full-auto" in cmd
        assert "--system-prompt" not in cmd  # 빈 system_prompt

    async def test_JSON_파싱(self) -> None:
        runtime = CodexCLIRuntime()
        await runtime.initialize({})

        output = '{"result": "생성된 코드", "cost_usd": 0.002}'
        result = runtime._parse_output(output, 150)

        assert result.response == "생성된 코드"
        assert result.cost_usd == 0.002

    async def test_shutdown(self) -> None:
        runtime = CodexCLIRuntime()
        await runtime.initialize({})
        await runtime.shutdown()

        assert runtime.status == RuntimeStatus.SHUTDOWN


# ─── RuntimeRegistry discover ────────────────────────


class TestRuntimeRegistryDiscover:
    """RuntimeRegistry.discover() — YAML 기반 자동 발견."""

    def test_discover_정상(self) -> None:
        """manifest에서 Runtime 클래스를 동적 로드하여 등록해야 한다."""
        registry = RuntimeRegistry()
        manifests = [
            RuntimeManifest(
                metadata=RuntimeMetadata(name="gemini-mcp"),
                spec=RuntimeSpec(
                    type="GeminiMCPRuntime",
                    module="aac.runtime.gemini_mcp",
                    **{"class": "GeminiMCPRuntime"},
                ),
            ),
        ]

        registered = registry.discover(manifests)

        assert "gemini-mcp" in registered
        assert registry.has("gemini-mcp")
        assert registry.get("gemini-mcp") is GeminiMCPRuntime

    def test_discover_복수(self) -> None:
        """여러 Runtime을 한 번에 자동 발견해야 한다."""
        registry = RuntimeRegistry()
        manifests = [
            RuntimeManifest(
                metadata=RuntimeMetadata(name="gemini-mcp"),
                spec=RuntimeSpec(
                    type="GeminiMCPRuntime",
                    module="aac.runtime.gemini_mcp",
                    **{"class": "GeminiMCPRuntime"},
                ),
            ),
            RuntimeManifest(
                metadata=RuntimeMetadata(name="openai-mcp"),
                spec=RuntimeSpec(
                    type="OpenAIMCPRuntime",
                    module="aac.runtime.openai_mcp",
                    **{"class": "OpenAIMCPRuntime"},
                ),
            ),
            RuntimeManifest(
                metadata=RuntimeMetadata(name="codex-cli"),
                spec=RuntimeSpec(
                    type="CodexCLIRuntime",
                    module="aac.runtime.codex_cli",
                    **{"class": "CodexCLIRuntime"},
                ),
            ),
        ]

        registered = registry.discover(manifests)

        assert len(registered) == 3
        assert registry.has("gemini-mcp")
        assert registry.has("openai-mcp")
        assert registry.has("codex-cli")

    def test_discover_잘못된_모듈(self) -> None:
        """존재하지 않는 모듈은 건너뛰어야 한다 (에러 로그)."""
        registry = RuntimeRegistry()
        manifests = [
            RuntimeManifest(
                metadata=RuntimeMetadata(name="bad-runtime"),
                spec=RuntimeSpec(
                    type="BadRuntime",
                    module="aac.runtime.nonexistent",
                    **{"class": "BadRuntime"},
                ),
            ),
        ]

        registered = registry.discover(manifests)

        assert len(registered) == 0
        assert not registry.has("bad-runtime")

    def test_discover_잘못된_클래스(self) -> None:
        """모듈은 있지만 클래스가 없으면 건너뛰어야 한다."""
        registry = RuntimeRegistry()
        manifests = [
            RuntimeManifest(
                metadata=RuntimeMetadata(name="bad-class"),
                spec=RuntimeSpec(
                    type="NonExistent",
                    module="aac.runtime.gemini_mcp",
                    **{"class": "NonExistentRuntime"},
                ),
            ),
        ]

        registered = registry.discover(manifests)

        assert len(registered) == 0

    def test_기존_등록_유지(self) -> None:
        """discover()가 기존 수동 등록된 runtime을 덮어쓰지 않아야 한다."""
        registry = RuntimeRegistry()
        registry.register("claude-code", ClaudeCodeRuntime)

        manifests = [
            RuntimeManifest(
                metadata=RuntimeMetadata(name="gemini-mcp"),
                spec=RuntimeSpec(
                    type="GeminiMCPRuntime",
                    module="aac.runtime.gemini_mcp",
                    **{"class": "GeminiMCPRuntime"},
                ),
            ),
        ]

        registry.discover(manifests)

        assert registry.has("claude-code")
        assert registry.has("gemini-mcp")
        assert len(registry) == 2


# ─── Scanner runtimes 스캔 ───────────────────────────


class TestScannerRuntimes:
    """AgentScanner의 runtimes/ 스캔 검증."""

    def test_runtimes_스캔(self, tmp_path: Path) -> None:
        """resources/runtimes/*.yaml을 스캔해야 한다."""
        runtime_yaml = {
            "apiVersion": "aac/v1",
            "kind": "Runtime",
            "metadata": {"name": "test-runtime"},
            "spec": {
                "type": "TestRuntime",
                "module": "aac.runtime.gemini_mcp",
                "class": "GeminiMCPRuntime",
                "default_config": {"model": "test"},
            },
        }
        write_yaml(tmp_path / "runtimes" / "test-runtime.yaml", runtime_yaml)

        result = AgentScanner(tmp_path).scan_all()

        assert len(result.runtimes) == 1
        assert result.runtimes[0].metadata.name == "test-runtime"
        assert result.runtimes[0].spec.module == "aac.runtime.gemini_mcp"

    def test_runtimes_디렉토리_없음(self, tmp_path: Path) -> None:
        """runtimes/ 디렉토리가 없으면 빈 목록이어야 한다."""
        result = AgentScanner(tmp_path).scan_all()

        assert len(result.runtimes) == 0
