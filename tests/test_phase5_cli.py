"""Phase 5 — CLI 테스트.

CliRunner를 사용한 Click CLI 단위 테스트.
서버 통신이 필요한 명령은 모킹하고,
로컬 모드 명령은 실제 resources/를 사용한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from aac.cli.main import cli

# ─── 테스트 fixtures ──────────────────────────────────

RESOURCES_DIR = str(Path(__file__).parent.parent / "resources")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ─── 기본 CLI 테스트 ──────────────────────────────────


class TestCLIBasic:
    """기본 CLI 동작 테스트."""

    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "AAC" in result.output
        assert "Agent Application Context" in result.output

    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_start_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["start", "--help"])
        assert result.exit_code == 0
        assert "--resources" in result.output
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--strict" in result.output

    def test_validate_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["validate", "--help"])
        assert result.exit_code == 0
        assert "YAML" in result.output or "검증" in result.output

    def test_execute_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["execute", "--help"])
        assert result.exit_code == 0
        assert "AGENT_NAME" in result.output
        assert "PROMPT" in result.output
        assert "--stream" in result.output

    def test_poll_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["poll", "--help"])
        assert result.exit_code == 0
        assert "EXECUTION_ID" in result.output
        assert "--watch" in result.output

    def test_cancel_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["cancel", "--help"])
        assert result.exit_code == 0
        assert "EXECUTION_ID" in result.output


# ─── validate 명령 테스트 ─────────────────────────────


class TestValidateCommand:
    """aac validate 명령 테스트."""

    def test_validate_성공(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["validate", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        assert "검증 통과" in result.output or "✓" in result.output

    def test_validate_상세(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["validate", "-r", RESOURCES_DIR, "-v"])
        assert result.exit_code == 0
        assert "Agents" in result.output

    def test_validate_잘못된_경로(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["validate", "-r", "/nonexistent/path"])
        assert result.exit_code != 0

    def test_validate_스캔_결과_포함(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["validate", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        # 테이블에 리소스 카운트가 표시되어야 함
        assert "Agents" in result.output
        assert "Tools" in result.output
        assert "Skills" in result.output
        assert "Aspects" in result.output


# ─── agents 명령 테스트 ───────────────────────────────


class TestAgentsCommand:
    """aac agents 명령 테스트."""

    def test_agents_로컬(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["agents", "--local", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        assert "Agents" in result.output

    def test_agents_로컬_상세(self, runner: CliRunner) -> None:
        """로컬 Agent 목록에 Agent 이름이 포함되는지."""
        result = runner.invoke(cli, ["agents", "--local", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        # resources/agents/ 하위에 있는 agent가 출력되어야 함
        assert "claude-coder" in result.output or "orchestrator" in result.output

    def test_agents_서버_연결실패(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["agents", "--url", "http://127.0.0.1:19999"])
        assert result.exit_code != 0
        assert "연결 실패" in result.output or "실패" in result.output


# ─── tools 명령 테스트 ────────────────────────────────


class TestToolsCommand:
    """aac tools 명령 테스트."""

    def test_tools_로컬(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["tools", "--local", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        assert "Tools" in result.output

    def test_tools_서버_연결실패(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["tools", "--url", "http://127.0.0.1:19999"])
        assert result.exit_code != 0


# ─── skills 명령 테스트 ───────────────────────────────


class TestSkillsCommand:
    """aac skills 명령 테스트."""

    def test_skills_로컬(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["skills", "--local", "-r", RESOURCES_DIR])
        assert result.exit_code == 0
        assert "Skills" in result.output

    def test_skills_서버_연결실패(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["skills", "--url", "http://127.0.0.1:19999"])
        assert result.exit_code != 0


# ─── status 명령 테스트 ───────────────────────────────


class TestStatusCommand:
    """aac status 명령 테스트."""

    def test_status_서버_연결실패(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["status", "--url", "http://127.0.0.1:19999"])
        assert result.exit_code != 0
        assert "연결 실패" in result.output or "실패" in result.output

    @patch("urllib.request.urlopen")
    def test_status_정상(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "version": "0.1.0",
            "started": True,
            "started_at": "2026-02-22T00:00:00Z",
            "agents": {"total": 3, "active": 2, "lazy": 1},
            "tools": {"bundles": 3, "total": 9},
            "skills": {"total": 4},
            "aspects": {"total": 4},
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "RUNNING" in result.output
        assert "0.1.0" in result.output


# ─── execute 명령 테스트 ──────────────────────────────


class TestExecuteCommand:
    """aac execute 명령 테스트."""

    def test_execute_인자_부족(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["execute"])
        assert result.exit_code != 0

    @patch("urllib.request.urlopen")
    def test_execute_동기_성공(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "execution_id": "exec_test123",
            "session_id": "sess_abc",
            "tx_id": "tx_001",
            "agent": "claude-coder",
            "result": "Hello, World!",
            "success": True,
            "error": None,
            "cost_usd": 0.01,
            "duration_ms": 1500,
            "model": "claude-sonnet-4-20250514",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["execute", "claude-coder", "Hello"])
        assert result.exit_code == 0
        assert "Hello, World!" in result.output
        assert "exec_test123" in result.output

    @patch("urllib.request.urlopen")
    def test_execute_비동기(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "execution_id": "exec_async123",
            "status": "running",
            "poll_url": "/api/executions/exec_async123",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["execute", "claude-coder", "Hello", "--async-mode"])
        assert result.exit_code == 0
        assert "exec_async123" in result.output
        assert "RUNNING" in result.output or "running" in result.output.lower()

    def test_execute_서버_다운(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["execute", "test-agent", "hello", "--url", "http://127.0.0.1:19999"],
        )
        assert result.exit_code != 0
        assert "실패" in result.output


# ─── poll 명령 테스트 ─────────────────────────────────


class TestPollCommand:
    """aac poll 명령 테스트."""

    @patch("urllib.request.urlopen")
    def test_poll_completed(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "execution_id": "exec_poll123",
            "agent": "claude-coder",
            "status": "completed",
            "result": "Done!",
            "cost_usd": 0.02,
            "duration_ms": 2000,
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["poll", "exec_poll123"])
        assert result.exit_code == 0
        assert "COMPLETED" in result.output

    @patch("urllib.request.urlopen")
    def test_poll_error(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "execution_id": "exec_err",
            "agent": "claude-coder",
            "status": "error",
            "error": "timeout",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["poll", "exec_err"])
        assert result.exit_code == 0
        assert "ERROR" in result.output

    def test_poll_서버_다운(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["poll", "exec_test", "--url", "http://127.0.0.1:19999"],
        )
        assert result.exit_code != 0


# ─── cancel 명령 테스트 ───────────────────────────────


class TestCancelCommand:
    """aac cancel 명령 테스트."""

    @patch("urllib.request.urlopen")
    def test_cancel_성공(self, mock_urlopen: MagicMock, runner: CliRunner) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "status": "cancelled",
            "execution_id": "exec_cancel123",
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = runner.invoke(cli, ["cancel", "exec_cancel123"])
        assert result.exit_code == 0
        assert "취소" in result.output

    def test_cancel_서버_다운(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["cancel", "exec_test", "--url", "http://127.0.0.1:19999"],
        )
        assert result.exit_code != 0
