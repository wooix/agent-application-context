"""워크플로우 엔진 — 다중 Agent 오케스트레이션 (Phase 6).

Spring Batch의 JobLauncher / StepExecution에 해당하며,
워크플로우 manifest 기반으로 Agent를 순차/병렬/조건부 실행한다.

사용 흐름:
  1. WorkflowManifest 로드 (Scanner)
  2. WorkflowEngine.run(manifest, context) → WorkflowResult
  3. 각 스텝 실행 결과가 WorkflowResult.steps에 축적
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from aac.logging.formatter import aac_log
from aac.models.workflow import OnFailure, StepType, WorkflowManifest, WorkflowStep

if TYPE_CHECKING:
    from aac.context import AgentApplicationContext

logger = structlog.get_logger()


# ─── 실행 결과 모델 ───────────────────────────────────


@dataclass
class StepResult:
    """개별 스텝 실행 결과."""

    name: str
    agent: str | None = None
    success: bool = False
    result: str | None = None
    error: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    skipped: bool = False
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent": self.agent,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "skipped": self.skipped,
            "retries": self.retries,
        }


@dataclass
class WorkflowResult:
    """워크플로우 전체 실행 결과."""

    workflow_name: str
    success: bool = False
    steps: list[StepResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow_name,
            "success": self.success,
            "steps": [s.to_dict() for s in self.steps],
            "total_cost_usd": self.total_cost_usd,
            "total_duration_ms": self.total_duration_ms,
            "error": self.error,
        }


# ─── 워크플로우 엔진 ──────────────────────────────────


class WorkflowEngine:
    """워크플로우 실행 엔진 — Context를 통해 Agent를 호출.

    Spring Batch의 SimpleJobLauncher에 해당.
    """

    def __init__(self, ctx: AgentApplicationContext) -> None:
        self._ctx = ctx

    async def run(
        self,
        manifest: WorkflowManifest,
        *,
        initial_context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """워크플로우 실행 — 모든 스텝을 순서대로 처리.

        Args:
            manifest: 워크플로우 정의
            initial_context: 초기 컨텍스트 (변수 바인딩)

        Returns:
            WorkflowResult: 전체 실행 결과
        """
        wf_name = manifest.metadata.name
        wf_result = WorkflowResult(
            workflow_name=wf_name,
            context={**(manifest.spec.context or {}), **(initial_context or {})},
        )

        session_id = f"wf_{wf_name}"
        aac_log("Workflow", session_id, "run", f"▶ STARTING workflow: {wf_name}")

        start_time = time.monotonic()

        try:
            for step in manifest.spec.steps:
                step_result = await self._execute_step(
                    step, wf_result, session_id, manifest,
                )
                wf_result.steps.append(step_result)
                wf_result.total_cost_usd += step_result.cost_usd
                wf_result.total_duration_ms += step_result.duration_ms

                # 이전 스텝 결과를 컨텍스트에 저장
                wf_result.context[f"steps.{step.name}"] = {
                    "success": step_result.success,
                    "result": step_result.result,
                    "error": step_result.error,
                }

                # 비용/시간 상한 체크
                if self._check_limits(manifest, wf_result):
                    wf_result.error = "비용 또는 시간 상한 초과"
                    break

                # 스텝 실패 + stop 정책이면 중단
                if not step_result.success and not step_result.skipped:
                    if step.on_failure == OnFailure.STOP:
                        wf_result.error = (
                            f"스텝 '{step.name}' 실패로 워크플로우 중단"
                        )
                        break

            wf_result.success = wf_result.error is None

        except Exception as e:
            wf_result.error = str(e)
            wf_result.success = False
            logger.error("workflow_error", workflow=wf_name, error=str(e))

        elapsed = int((time.monotonic() - start_time) * 1000)
        wf_result.total_duration_ms = elapsed

        icon = "✓" if wf_result.success else "✗"
        aac_log(
            "Workflow", session_id, "run",
            f"{icon} COMPLETED {wf_name} "
            f"({len(wf_result.steps)} steps, "
            f"{elapsed}ms, ${wf_result.total_cost_usd:.4f})",
        )

        return wf_result

    async def _execute_step(
        self,
        step: WorkflowStep,
        wf_result: WorkflowResult,
        session_id: str,
        manifest: WorkflowManifest,
    ) -> StepResult:
        """개별 스텝 실행 — 타입에 따라 분기."""
        if step.type == StepType.AGENT:
            return await self._execute_agent_step(step, wf_result, session_id)
        elif step.type == StepType.PARALLEL:
            return await self._execute_parallel_step(
                step, wf_result, session_id, manifest,
            )
        elif step.type == StepType.CONDITION:
            return await self._execute_condition_step(
                step, wf_result, session_id, manifest,
            )
        else:
            return StepResult(
                name=step.name,
                error=f"알 수 없는 스텝 타입: {step.type}",
            )

    async def _execute_agent_step(
        self,
        step: WorkflowStep,
        wf_result: WorkflowResult,
        session_id: str,
    ) -> StepResult:
        """Agent 스텝 실행 — Context.execute() 호출."""
        if not step.agent:
            return StepResult(name=step.name, error="agent 미지정")

        # 프롬프트 구성
        prompt = self._resolve_prompt(step, wf_result)
        if not prompt:
            return StepResult(name=step.name, error="prompt 미지정")

        aac_log("Workflow", session_id, step.name, f"▶ Agent: {step.agent}")

        retries = 0
        last_error: str | None = None

        while retries <= step.retry_count:
            try:
                result = await self._ctx.execute(
                    step.agent,
                    prompt,
                    context=wf_result.context,
                )
                return StepResult(
                    name=step.name,
                    agent=step.agent,
                    success=result.get("success", False),
                    result=result.get("result", ""),
                    error=result.get("error"),
                    cost_usd=result.get("cost_usd", 0.0),
                    duration_ms=result.get("duration_ms", 0),
                    retries=retries,
                )
            except Exception as e:
                last_error = str(e)
                retries += 1

                if retries <= step.retry_count:
                    aac_log(
                        "Workflow", session_id, step.name,
                        f"⚠ Retry {retries}/{step.retry_count}: {last_error}",
                    )
                    await asyncio.sleep(0.1 * retries)

        # 모든 재시도 실패
        if step.on_failure == OnFailure.SKIP:
            return StepResult(
                name=step.name,
                agent=step.agent,
                skipped=True,
                error=last_error,
                retries=retries - 1,
            )

        return StepResult(
            name=step.name,
            agent=step.agent,
            error=last_error,
            retries=retries - 1,
        )

    async def _execute_parallel_step(
        self,
        step: WorkflowStep,
        wf_result: WorkflowResult,
        session_id: str,
        manifest: WorkflowManifest,
    ) -> StepResult:
        """병렬 스텝 실행 — 하위 스텝을 동시에 실행."""
        if not step.steps:
            return StepResult(
                name=step.name, success=True, skipped=True,
            )

        aac_log(
            "Workflow", session_id, step.name,
            f"▶ Parallel: {len(step.steps)} sub-steps",
        )

        tasks = []
        for sub_step in step.steps:
            tasks.append(
                self._execute_step(sub_step, wf_result, session_id, manifest)
            )

        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 집계
        total_cost = 0.0
        total_duration = 0
        all_success = True
        errors: list[str] = []

        for i, sub_result in enumerate(sub_results):
            if isinstance(sub_result, Exception):
                all_success = False
                errors.append(str(sub_result))
            else:
                wf_result.steps.append(sub_result)
                total_cost += sub_result.cost_usd
                total_duration = max(total_duration, sub_result.duration_ms)
                if not sub_result.success and not sub_result.skipped:
                    all_success = False
                    if sub_result.error:
                        errors.append(sub_result.error)

                # 컨텍스트에 결과 저장
                wf_result.context[f"steps.{sub_result.name}"] = {
                    "success": sub_result.success,
                    "result": sub_result.result,
                    "error": sub_result.error,
                }

        return StepResult(
            name=step.name,
            success=all_success,
            cost_usd=total_cost,
            duration_ms=total_duration,
            error="; ".join(errors) if errors else None,
        )

    async def _execute_condition_step(
        self,
        step: WorkflowStep,
        wf_result: WorkflowResult,
        session_id: str,
        manifest: WorkflowManifest,
    ) -> StepResult:
        """조건 분기 스텝 — 조건 평가 후 대상 스텝 실행."""
        if not step.condition:
            return StepResult(name=step.name, error="condition 미지정")

        # 간단한 조건 평가 (steps.step_name.success 형태)
        condition_result = self._evaluate_condition(
            step.condition, wf_result.context,
        )

        target = step.if_true if condition_result else step.if_false

        aac_log(
            "Workflow", session_id, step.name,
            f"▶ Condition: {step.condition} → {condition_result} → {target}",
        )

        if not target:
            return StepResult(
                name=step.name, success=True, skipped=True,
            )

        # target 스텝을 manifest에서 찾아 실행
        target_step = self._find_step(target, manifest.spec.steps)
        if not target_step:
            return StepResult(
                name=step.name,
                error=f"조건 분기 대상 스텝 '{target}' 미발견",
            )

        return await self._execute_step(
            target_step, wf_result, session_id, manifest,
        )

    def _resolve_prompt(
        self, step: WorkflowStep, wf_result: WorkflowResult,
    ) -> str | None:
        """스텝 프롬프트 해석 — 이전 스텝 결과 참조 치환."""
        prompt = step.prompt

        if not prompt:
            return None

        # input_from: 이전 스텝 결과를 프롬프트에 추가
        if step.input_from:
            prev = wf_result.context.get(f"steps.{step.input_from}", {})
            prev_result = prev.get("result", "")
            if prev_result:
                prompt = f"{prompt}\n\n이전 작업 결과:\n{prev_result}"

        # 간단한 템플릿 변수 치환 ({{key}} → value)
        for key, value in wf_result.context.items():
            if isinstance(value, str):
                prompt = prompt.replace(f"{{{{{key}}}}}", value)

        return prompt

    def _evaluate_condition(
        self, condition: str, context: dict[str, Any],
    ) -> bool:
        """조건 표현식 평가.

        지원 패턴:
        - `steps.step_name.success` → boolean
        - `steps.step_name.error` → truthy/falsy
        """
        # dot-notation으로 context 탐색
        parts = condition.split(".")
        current: Any = context

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return False

            if current is None:
                return False

        return bool(current)

    def _find_step(
        self, name: str, steps: list[WorkflowStep],
    ) -> WorkflowStep | None:
        """이름으로 스텝 검색 (중첩 포함)."""
        for step in steps:
            if step.name == name:
                return step
            if step.steps:
                found = self._find_step(name, step.steps)
                if found:
                    return found
        return None

    def _check_limits(
        self,
        manifest: WorkflowManifest,
        wf_result: WorkflowResult,
    ) -> bool:
        """비용/시간 상한 초과 여부."""
        if (
            manifest.spec.max_total_cost_usd
            and wf_result.total_cost_usd > manifest.spec.max_total_cost_usd
        ):
            return True
        if (
            manifest.spec.max_total_duration_seconds
            and wf_result.total_duration_ms
            > manifest.spec.max_total_duration_seconds * 1000
        ):
            return True
        return False
