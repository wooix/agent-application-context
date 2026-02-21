"""GeminiMCPRuntime — Gemini CLI 어댑터 (Phase 2).

Gemini CLI를 subprocess로 실행하여 Google의 Gemini 모델을 활용한다.
gemini 명령어는 MCP(Model Context Protocol)를 통해
Tool 호출과 멀티턴 대화를 지원한다.

CLI 형식: gemini -p "prompt" --model {model} --output json
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from aac.runtime.base import AgentRuntime, ExecutionResult, RuntimeStatus

logger = structlog.get_logger()


class GeminiMCPRuntime(AgentRuntime):
    """Gemini CLI/MCP를 통한 Agent 실행."""

    _DEFAULT_MODEL = "gemini-2.5-pro"
    _CLI_COMMAND = "gemini"

    @property
    def name(self) -> str:
        return "gemini-mcp"

    async def initialize(self, config: dict[str, Any]) -> None:
        self._config = config
        self._model = config.get("model", self._DEFAULT_MODEL)
        self._cumulative_cost = 0.0
        self._sandbox = config.get("sandbox", True)
        self._status = RuntimeStatus.READY
        logger.info("gemini_mcp_initialized", model=self._model)

    async def execute(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 600,
    ) -> ExecutionResult:
        """Gemini CLI로 query 실행."""
        self._status = RuntimeStatus.BUSY
        start = time.monotonic()

        try:
            cmd = self._build_command(prompt, system_prompt)
            logger.debug("gemini_mcp_exec", cmd_preview=" ".join(cmd[:6]))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration = int((time.monotonic() - start) * 1000)
                self._status = RuntimeStatus.READY
                return ExecutionResult(
                    error=f"실행 시간 초과 ({timeout_seconds}s)",
                    duration_ms=duration,
                    model=self._model,
                )

            duration = int((time.monotonic() - start) * 1000)
            output = stdout.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                self._status = RuntimeStatus.READY
                return ExecutionResult(
                    error=error_msg or f"exit code {proc.returncode}",
                    duration_ms=duration,
                    model=self._model,
                )

            result = self._parse_output(output, duration)
            self._cumulative_cost += result.cost_usd
            self._status = RuntimeStatus.READY
            return result

        except FileNotFoundError:
            self._status = RuntimeStatus.ERROR
            return ExecutionResult(
                error=(
                    f"'{self._CLI_COMMAND}' 명령어를 찾을 수 없습니다. "
                    "Gemini CLI가 설치되어 있는지 확인하세요: "
                    "npm install -g @anthropic-ai/gemini-cli"
                ),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            self._status = RuntimeStatus.ERROR
            return ExecutionResult(
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    def _build_command(
        self,
        prompt: str,
        system_prompt: str,
    ) -> list[str]:
        """Gemini CLI 명령어 조립."""
        cmd = [
            self._CLI_COMMAND,
            "-p", prompt,
            "--model", self._model,
            "--output", "json",
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if self._sandbox:
            cmd.append("--sandbox")
        return cmd

    def _parse_output(self, output: str, duration_ms: int) -> ExecutionResult:
        """Gemini CLI JSON 출력 파싱."""
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                return ExecutionResult(
                    response=data.get("response", data.get("text", output)),
                    cost_usd=data.get("cost_usd", 0.0),
                    duration_ms=duration_ms,
                    model=data.get("model", self._model),
                    tokens_in=data.get("input_tokens", data.get("tokens_in", 0)),
                    tokens_out=data.get("output_tokens", data.get("tokens_out", 0)),
                    metadata=data,
                )
            if isinstance(data, list):
                # 대화 블록 형식 — 마지막 text/result 블록 추출
                last_text = ""
                for block in reversed(data):
                    if isinstance(block, dict):
                        if block.get("type") in ("result", "text", "modelTurn"):
                            last_text = block.get("text", block.get("response", ""))
                            break
                return ExecutionResult(
                    response=last_text or output,
                    duration_ms=duration_ms,
                    model=self._model,
                    metadata={"blocks": data},
                )
        except json.JSONDecodeError:
            pass

        return ExecutionResult(
            response=output,
            duration_ms=duration_ms,
            model=self._model,
        )

    async def get_cost(self) -> float:
        return self._cumulative_cost

    async def shutdown(self) -> None:
        self._status = RuntimeStatus.SHUTDOWN
        logger.info("gemini_mcp_shutdown")
