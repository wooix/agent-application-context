"""Phase 6 — 워크플로우 오케스트레이션 테스트.

WorkflowManifest 파싱, WorkflowEngine 순차/병렬/조건 실행,
에러 핸들링, 재시도, 비용 상한 테스트.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aac.models.workflow import (
    OnFailure,
    StepType,
    WorkflowManifest,
    WorkflowMetadata,
    WorkflowSpec,
    WorkflowStep,
)
from aac.orchestration.engine import StepResult, WorkflowEngine, WorkflowResult


# ─── Manifest 파싱 테스트 ─────────────────────────────


class TestWorkflowManifest:
    """WorkflowManifest Pydantic 모델 테스트."""

    def test_기본_매니페스트_생성(self) -> None:
        manifest = WorkflowManifest(
            metadata=WorkflowMetadata(name="test-wf"),
            spec=WorkflowSpec(
                steps=[
                    WorkflowStep(name="step1", agent="test-agent", prompt="hello"),
                ]
            ),
        )
        assert manifest.metadata.name == "test-wf"
        assert manifest.kind.value == "Workflow"
        assert len(manifest.spec.steps) == 1

    def test_병렬_스텝(self) -> None:
        manifest = WorkflowManifest(
            metadata=WorkflowMetadata(name="parallel-wf"),
            spec=WorkflowSpec(
                steps=[
                    WorkflowStep(
                        name="parallel-group",
                        type=StepType.PARALLEL,
                        steps=[
                            WorkflowStep(
                                name="sub1", agent="a", prompt="p1",
                            ),
                            WorkflowStep(
                                name="sub2", agent="b", prompt="p2",
                            ),
                        ],
                    ),
                ]
            ),
        )
        assert manifest.spec.steps[0].type == StepType.PARALLEL
        assert len(manifest.spec.steps[0].steps) == 2

    def test_조건_스텝(self) -> None:
        step = WorkflowStep(
            name="decide",
            type=StepType.CONDITION,
            condition="steps.step1.success",
            if_true="go",
            if_false="stop",
        )
        assert step.type == StepType.CONDITION
        assert step.condition == "steps.step1.success"

    def test_on_failure_기본값(self) -> None:
        step = WorkflowStep(name="s", agent="a", prompt="p")
        assert step.on_failure == OnFailure.STOP

    def test_비용_상한(self) -> None:
        manifest = WorkflowManifest(
            metadata=WorkflowMetadata(name="limited"),
            spec=WorkflowSpec(
                steps=[WorkflowStep(name="s1", agent="a", prompt="p")],
                max_total_cost_usd=1.0,
                max_total_duration_seconds=60,
            ),
        )
        assert manifest.spec.max_total_cost_usd == 1.0
        assert manifest.spec.max_total_duration_seconds == 60


# ─── WorkflowResult 테스트 ────────────────────────────


class TestWorkflowResult:
    """WorkflowResult 직렬화 테스트."""

    def test_to_dict(self) -> None:
        result = WorkflowResult(
            workflow_name="test",
            success=True,
            steps=[
                StepResult(name="s1", success=True, cost_usd=0.01),
            ],
            total_cost_usd=0.01,
            total_duration_ms=500,
        )
        d = result.to_dict()
        assert d["workflow"] == "test"
        assert d["success"] is True
        assert len(d["steps"]) == 1
        assert d["total_cost_usd"] == 0.01

    def test_step_result_to_dict(self) -> None:
        sr = StepResult(
            name="step1",
            agent="claude-coder",
            success=True,
            result="Hello",
            cost_usd=0.02,
            duration_ms=1000,
        )
        d = sr.to_dict()
        assert d["name"] == "step1"
        assert d["agent"] == "claude-coder"
        assert d["result"] == "Hello"


# ─── WorkflowEngine 테스트 ────────────────────────────


def _make_ctx_mock(execute_result: dict[str, Any] | None = None) -> MagicMock:
    """AgentApplicationContext 모킹."""
    ctx = MagicMock()
    if execute_result is None:
        execute_result = {
            "success": True,
            "result": "OK",
            "error": None,
            "cost_usd": 0.01,
            "duration_ms": 100,
            "model": "test-model",
            "session_id": "sess_test",
            "tx_id": "tx_001",
        }
    ctx.execute = AsyncMock(return_value=execute_result)
    return ctx


def _make_manifest(
    steps: list[WorkflowStep],
    *,
    name: str = "test-wf",
    context: dict[str, Any] | None = None,
    max_cost: float | None = None,
) -> WorkflowManifest:
    return WorkflowManifest(
        metadata=WorkflowMetadata(name=name),
        spec=WorkflowSpec(
            steps=steps,
            context=context or {},
            max_total_cost_usd=max_cost,
        ),
    )


class TestWorkflowEngineSequential:
    """순차 실행 테스트."""

    async def test_단일_스텝_성공(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="s1", agent="agent-a", prompt="hello"),
        ])

        result = await engine.run(manifest)

        assert result.success is True
        assert len(result.steps) == 1
        assert result.steps[0].name == "s1"
        assert result.steps[0].success is True
        ctx.execute.assert_called_once()

    async def test_다중_스텝_순차(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="s1", agent="a", prompt="p1"),
            WorkflowStep(name="s2", agent="b", prompt="p2"),
            WorkflowStep(name="s3", agent="c", prompt="p3"),
        ])

        result = await engine.run(manifest)
        assert result.success is True
        assert len(result.steps) == 3
        assert ctx.execute.call_count == 3

    async def test_input_from_연결(self) -> None:
        """이전 스텝 결과를 다음 프롬프트에 포함."""
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="s1", agent="a", prompt="generate"),
            WorkflowStep(
                name="s2", agent="b", prompt="review this",
                input_from="s1",
            ),
        ])

        result = await engine.run(manifest)
        assert result.success is True

        # s2 호출 시 prompt에 이전 결과가 포함되어야 함
        s2_call = ctx.execute.call_args_list[1]
        prompt_arg = s2_call.args[1]
        assert "이전 작업 결과" in prompt_arg

    async def test_컨텍스트_변수_치환(self) -> None:
        """{{key}} 템플릿 변수가 치환되는지."""
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest(
            [WorkflowStep(name="s1", agent="a", prompt="language is {{lang}}")],
            context={"lang": "Python"},
        )

        result = await engine.run(manifest)
        assert result.success is True
        prompt_arg = ctx.execute.call_args.args[1]
        assert "Python" in prompt_arg

    async def test_스텝_실패_stop(self) -> None:
        """on_failure=stop 시 후속 스텝 실행 안됨."""
        ctx = _make_ctx_mock({"success": False, "result": "", "error": "fail",
                              "cost_usd": 0, "duration_ms": 0, "model": ""})
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="s1", agent="a", prompt="p1",
                on_failure=OnFailure.STOP,
            ),
            WorkflowStep(name="s2", agent="b", prompt="p2"),
        ])

        result = await engine.run(manifest)
        assert result.success is False
        assert len(result.steps) == 1  # s2 실행 안됨
        assert "실패" in result.error

    async def test_스텝_실패_skip(self) -> None:
        """on_failure=skip 시 후속 스텝 계속 진행."""
        call_count = 0

        async def failing_then_success(*args: Any, **kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call fails")
            return {
                "success": True, "result": "OK", "error": None,
                "cost_usd": 0, "duration_ms": 0, "model": "",
            }

        ctx = MagicMock()
        ctx.execute = AsyncMock(side_effect=failing_then_success)
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="s1", agent="a", prompt="p1",
                on_failure=OnFailure.SKIP,
            ),
            WorkflowStep(name="s2", agent="b", prompt="p2"),
        ])

        result = await engine.run(manifest)
        assert len(result.steps) == 2  # 둘 다 실행됨
        assert result.steps[0].skipped is True
        assert result.steps[1].success is True


class TestWorkflowEngineParallel:
    """병렬 실행 테스트."""

    async def test_병렬_실행(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="par",
                type=StepType.PARALLEL,
                steps=[
                    WorkflowStep(name="p1", agent="a", prompt="pa"),
                    WorkflowStep(name="p2", agent="b", prompt="pb"),
                ],
            ),
        ])

        result = await engine.run(manifest)
        # parallel 스텝 + 2개의 sub-step
        assert result.success is True
        assert ctx.execute.call_count == 2

    async def test_빈_병렬_그룹(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="empty", type=StepType.PARALLEL, steps=[]),
        ])

        result = await engine.run(manifest)
        assert result.success is True


class TestWorkflowEngineCondition:
    """조건 분기 테스트."""

    async def test_조건_참(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="s1", agent="a", prompt="p1"),
            WorkflowStep(
                name="decide",
                type=StepType.CONDITION,
                condition="steps.s1.success",
                if_true="s3",
                if_false="s4",
            ),
            WorkflowStep(name="s3", agent="c", prompt="true path"),
            WorkflowStep(name="s4", agent="d", prompt="false path"),
        ])

        result = await engine.run(manifest)
        # s1 성공 → decide → s3 실행, s4는 steps에 안 나타남
        executed_names = [s.name for s in result.steps]
        assert "s1" in executed_names
        # s3가 execute된 경우 confirm
        assert ctx.execute.call_count >= 2

    async def test_조건_거짓(self) -> None:
        ctx = _make_ctx_mock({"success": False, "result": "", "error": "fail",
                              "cost_usd": 0, "duration_ms": 0, "model": ""})
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="s1", agent="a", prompt="p1",
                on_failure=OnFailure.SKIP,
            ),
            WorkflowStep(
                name="decide",
                type=StepType.CONDITION,
                condition="steps.s1.success",
                if_true="go-true",
                if_false="go-false",
            ),
            WorkflowStep(name="go-true", agent="b", prompt="true"),
            WorkflowStep(name="go-false", agent="c", prompt="false"),
        ])

        # s1 실패(skip) → decide에서 success=False → go-false 실행
        result = await engine.run(manifest)
        executed_names = [s.name for s in result.steps]
        assert "s1" in executed_names

    async def test_조건_대상_없음(self) -> None:
        """if_true/if_false가 None이면 skip."""
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="s1", agent="a", prompt="p1"),
            WorkflowStep(
                name="decide",
                type=StepType.CONDITION,
                condition="steps.s1.success",
                if_true=None,
                if_false=None,
            ),
        ])

        result = await engine.run(manifest)
        assert result.success is True


class TestWorkflowEngineLimits:
    """비용/시간 상한 테스트."""

    async def test_비용_상한_초과(self) -> None:
        ctx = _make_ctx_mock({"success": True, "result": "OK", "error": None,
                              "cost_usd": 0.5, "duration_ms": 100, "model": ""})
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest(
            [
                WorkflowStep(name="s1", agent="a", prompt="p1"),
                WorkflowStep(name="s2", agent="b", prompt="p2"),
                WorkflowStep(name="s3", agent="c", prompt="p3"),
            ],
            max_cost=0.8,
        )

        result = await engine.run(manifest)
        assert result.success is False
        assert "상한" in result.error
        # s1 + s2 = $1.0 > $0.8, s3는 실행 안됨
        assert len(result.steps) == 2


class TestWorkflowEngineRetry:
    """재시도 테스트."""

    async def test_재시도_성공(self) -> None:
        call_count = 0

        async def retry_then_success(*args: Any, **kwargs: Any) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("transient error")
            return {
                "success": True, "result": "OK", "error": None,
                "cost_usd": 0.01, "duration_ms": 50, "model": "",
            }

        ctx = MagicMock()
        ctx.execute = AsyncMock(side_effect=retry_then_success)
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="s1", agent="a", prompt="p1",
                retry_count=3,
            ),
        ])

        result = await engine.run(manifest)
        assert result.success is True
        assert result.steps[0].retries == 2  # 2번 재시도 후 3번째에 성공

    async def test_재시도_모두_실패(self) -> None:
        ctx = MagicMock()
        ctx.execute = AsyncMock(side_effect=RuntimeError("always fails"))
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(
                name="s1", agent="a", prompt="p1",
                retry_count=2,
                on_failure=OnFailure.STOP,
            ),
        ])

        result = await engine.run(manifest)
        assert result.success is False
        assert "always fails" in result.steps[0].error


class TestWorkflowEngineEdgeCases:
    """에지 케이스 테스트."""

    async def test_agent_미지정(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="bad", prompt="p1"),
        ])

        result = await engine.run(manifest)
        assert result.steps[0].error == "agent 미지정"

    async def test_prompt_미지정(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest([
            WorkflowStep(name="bad", agent="a"),
        ])

        result = await engine.run(manifest)
        assert result.steps[0].error == "prompt 미지정"

    async def test_initial_context_주입(self) -> None:
        ctx = _make_ctx_mock()
        engine = WorkflowEngine(ctx)
        manifest = _make_manifest(
            [WorkflowStep(name="s1", agent="a", prompt="use {{extra}}")],
            context={"base": "val"},
        )

        result = await engine.run(manifest, initial_context={"extra": "injected"})
        assert result.success is True
        prompt_arg = ctx.execute.call_args.args[1]
        assert "injected" in prompt_arg
