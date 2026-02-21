"""OpenAIMCPRuntime — OpenAI CLI/MCP 어댑터 (Phase 2).

OpenAI CLI를 subprocess로 실행하여 GPT 모델을 활용한다.
openai 명령어를 통한 MCP 기반 Tool 호출과 대화를 지원한다.

CLI 형식: openai api chat.completions.create -p "prompt" --model {model}
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from aac.runtime.base import AgentRuntime, ExecutionResult, RuntimeStatus

logger = structlog.get_logger()


class OpenAIMCPRuntime(AgentRuntime):
    """OpenAI CLI/MCP를 통한 Agent 실행."""

    _DEFAULT_MODEL = "gpt-4o"
    _CLI_COMMAND = "openai"

    @property
    def name(self) -> str:
        return "openai-mcp"

    async def initialize(self, config: dict[str, Any]) -> None:
        self._config = config
        self._model = config.get("model", self._DEFAULT_MODEL)
        self._cumulative_cost = 0.0
        self._api_key = config.get("api_key", "")
        self._status = RuntimeStatus.READY
        logger.info("openai_mcp_initialized", model=self._model)

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
        """OpenAI CLI로 query 실행."""
        self._status = RuntimeStatus.BUSY
        start = time.monotonic()

        try:
            cmd = self._build_command(prompt, system_prompt)
            logger.debug("openai_mcp_exec", cmd_preview=" ".join(cmd[:6]))

            env = None
            if self._api_key:
                import os
                env = {**os.environ, "OPENAI_API_KEY": self._api_key}

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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
                    "OpenAI CLI가 설치되어 있는지 확인하세요: "
                    "pip install openai"
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
        """OpenAI CLI 명령어 조립."""
        cmd = [
            self._CLI_COMMAND,
            "api", "chat.completions.create",
            "-m", self._model,
            "-g", "user", prompt,
        ]
        if system_prompt:
            cmd.extend(["-g", "system", system_prompt])
        return cmd

    def _parse_output(self, output: str, duration_ms: int) -> ExecutionResult:
        """OpenAI CLI JSON 출력 파싱."""
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                # OpenAI ChatCompletion 응답 형식
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                else:
                    content = data.get("response", output)

                usage = data.get("usage", {})
                return ExecutionResult(
                    response=content,
                    cost_usd=data.get("cost_usd", 0.0),
                    duration_ms=duration_ms,
                    model=data.get("model", self._model),
                    tokens_in=usage.get("prompt_tokens", 0),
                    tokens_out=usage.get("completion_tokens", 0),
                    metadata=data,
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
        logger.info("openai_mcp_shutdown")
