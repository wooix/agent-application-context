"""ClaudeCodeRuntime — Claude Code CLI 어댑터 (FR-6.2).

claude 명령어를 subprocess로 실행하여
Claude Code의 모든 기능(tool 사용, 멀티턴 대화, 파일 편집 등)을 활용한다.

Phase 1에서는 기본 execute만 구현하고,
Phase 2에서 스트리밍/취소/비용 산출을 강화한다.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

import structlog

from aac.runtime.base import AgentRuntime, ExecutionResult, RuntimeStatus, StreamChunk

logger = structlog.get_logger()


class ClaudeCodeRuntime(AgentRuntime):
    """Claude Code CLI를 통한 Agent 실행."""

    _DEFAULT_MODEL = "sonnet"

    @property
    def name(self) -> str:
        return "claude-code"

    async def initialize(self, config: dict[str, Any]) -> None:
        self._config = config
        self._model = config.get("model", self._DEFAULT_MODEL)
        self._cumulative_cost = 0.0
        self._status = RuntimeStatus.READY
        logger.info("claude_code_initialized", model=self._model)

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
        """Claude Code CLI로 query 실행.

        claude -p "prompt" --model {model} --max-turns {max_turns} --output-format json
        """
        self._status = RuntimeStatus.BUSY
        start = time.monotonic()

        try:
            cmd = self._build_command(prompt, system_prompt, max_turns)
            logger.debug("claude_code_exec", cmd_preview=cmd[:200])

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
                error="claude 명령어를 찾을 수 없습니다. Claude Code가 설치되어 있는지 확인하세요.",
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
        max_turns: int,
    ) -> list[str]:
        """Claude Code CLI 명령어 조립."""
        cmd = [
            "claude",
            "-p", prompt,
            "--model", self._model,
            "--max-turns", str(max_turns),
            "--output-format", "json",
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        # allowedTools는 추후 Phase 2에서 tools 파라미터 기반으로 구현
        return cmd

    def _parse_output(self, output: str, duration_ms: int) -> ExecutionResult:
        """Claude Code JSON 출력 파싱."""
        try:
            data = json.loads(output)
            # Claude Code JSON 출력 형식 파싱
            if isinstance(data, dict):
                return ExecutionResult(
                    response=data.get("result", output),
                    cost_usd=data.get("cost_usd", 0.0),
                    duration_ms=duration_ms,
                    model=self._model,
                    tokens_in=data.get("tokens_in", 0),
                    tokens_out=data.get("tokens_out", 0),
                    metadata=data,
                )
            # 리스트 형식 (대화 블록)
            if isinstance(data, list):
                last_text = ""
                for block in reversed(data):
                    if isinstance(block, dict) and block.get("type") == "result":
                        last_text = block.get("result", "")
                        break
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_text = block.get("text", "")
                        break
                return ExecutionResult(
                    response=last_text or output,
                    duration_ms=duration_ms,
                    model=self._model,
                    metadata={"blocks": data},
                )
        except json.JSONDecodeError:
            pass

        # JSON 파싱 실패 시 원본 텍스트 반환
        return ExecutionResult(
            response=output,
            duration_ms=duration_ms,
            model=self._model,
        )

    async def stream(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        max_turns: int = 30,
        timeout_seconds: int = 600,
    ) -> AsyncIterator[StreamChunk]:
        """Claude Code --output-format stream-json 으로 스트리밍 실행."""
        self._status = RuntimeStatus.BUSY

        try:
            cmd = self._build_stream_command(prompt, system_prompt, max_turns)
            logger.debug("claude_code_stream", cmd_preview=cmd[:200])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async for chunk in self._parse_stream(proc):
                yield chunk

            await proc.wait()
            self._status = RuntimeStatus.READY

        except FileNotFoundError:
            self._status = RuntimeStatus.ERROR
            yield StreamChunk(type="error", content="claude 명령어를 찾을 수 없습니다")
            yield StreamChunk(type="done")
        except Exception as e:
            self._status = RuntimeStatus.ERROR
            yield StreamChunk(type="error", content=str(e))
            yield StreamChunk(type="done")

    def _build_stream_command(
        self,
        prompt: str,
        system_prompt: str,
        max_turns: int,
    ) -> list[str]:
        """스트리밍용 Claude Code CLI 명령어 조립."""
        cmd = [
            "claude",
            "-p", prompt,
            "--model", self._model,
            "--max-turns", str(max_turns),
            "--output-format", "stream-json",
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        return cmd

    async def _parse_stream(
        self,
        proc: asyncio.subprocess.Process,
    ) -> AsyncIterator[StreamChunk]:
        """stream-json 형식 파싱 — 줄 단위로 JSON 객체 수신."""
        assert proc.stdout is not None

        async for line_bytes in proc.stdout:
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                yield StreamChunk(type="text", content=line)
                continue

            chunk_type = data.get("type", "")

            if chunk_type == "assistant" and "message" in data:
                # 텍스트 블록
                msg = data["message"]
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    yield StreamChunk(
                                        type="text",
                                        content=block.get("text", ""),
                                    )
                                elif block.get("type") == "tool_use":
                                    yield StreamChunk(
                                        type="tool_call",
                                        tool_name=block.get("name"),
                                        tool_input=block.get("input"),
                                    )
                    elif isinstance(content, str):
                        yield StreamChunk(type="text", content=content)

            elif chunk_type == "result":
                # 최종 결과
                cost = data.get("cost_usd", 0.0)
                duration = data.get("duration_ms", 0)
                self._cumulative_cost += cost
                yield StreamChunk(
                    type="done",
                    metadata={
                        "cost_usd": cost,
                        "duration_ms": duration,
                        "model": self._model,
                    },
                )
                return

            elif chunk_type == "error":
                yield StreamChunk(type="error", content=data.get("error", "알 수 없는 에러"))

        # 프로세스가 끝났는데 done이 없으면 발행
        yield StreamChunk(type="done")

    async def get_cost(self) -> float:
        return self._cumulative_cost

    async def shutdown(self) -> None:
        self._status = RuntimeStatus.SHUTDOWN
        logger.info("claude_code_shutdown")
