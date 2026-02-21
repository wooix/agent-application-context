# CLAUDE.md — AgentApplicationContext (AAC)

## 프로젝트 개요

Spring Framework의 IoC/DI/AOP 개념을 AI Agent 오케스트레이션에 적용한 Python 프레임워크.
다양한 LLM Runtime(Claude Code, Gemini, OpenAI, Codex)을 bean처럼 등록/주입/관리하며,
`aac start`로 서버를 구동하여 REST API endpoint를 제공한다.

## 핵심 개념 매핑

| Spring | AAC |
|---|---|
| `ApplicationContext` | `AgentApplicationContext` (`src/aac/context.py`) |
| `Bean` | `AgentInstance` (`src/aac/models/instance.py`) |
| `BeanDefinition` | `agent.yaml` (`resources/agents/*/agent.yaml`) |
| `@ComponentScan` | `AgentScanner` (`src/aac/scanner.py`) |
| `BeanFactory` | `AgentFactory` (`src/aac/factory.py`) |
| `DataSource` | `AgentRuntime` (`src/aac/runtime/base.py`) |
| `@Aspect` | `AspectManifest` (`resources/aspects/*.yaml`) |

## 기술 스택

- **Python 3.12+**, 패키지 관리: `uv`
- **FastAPI + uvicorn**: HTTP 서버 (`src/aac/server/app.py`)
- **Pydantic v2**: YAML 스키마 검증
- **structlog**: 구조화 로깅
- **PyYAML**: YAML 파싱
- **SQLite**: 감사 로그 (Phase 3)
- **Click**: CLI (Phase 5)
- **Textual**: TUI (Phase 7)

## 디렉토리 구조

```
src/aac/              # 코어 프레임워크
  context.py          # AgentApplicationContext (중앙 IoC 컨테이너)
  factory.py          # AgentFactory (DI 통합 엔진)
  scanner.py          # AgentScanner (resources/ 스캔)
  models/             # Pydantic 데이터 모델
    manifest.py       # AgentManifest, ToolManifest, SkillManifest, AspectManifest
    instance.py       # AgentInstance, AgentStatus, ToolDefinition
    events.py         # WebSocket 이벤트 모델
  runtime/            # LLM Runtime 추상화
    base.py           # AgentRuntime ABC, ExecutionResult
    registry.py       # RuntimeRegistry
    claude_code.py    # Claude Code CLI 어댑터
  di/                 # DI 시스템
    tool_registry.py  # ToolRegistry (DR-1 충돌 해결)
    skill_registry.py # SkillRegistry (DR-2 문서 합성)
  logging/            # 통일 로그 시스템
    formatter.py      # [HH:mm:ss:SSS] [Agent] [sid:txid] msg
  server/             # FastAPI HTTP 서버
    app.py            # 앱 생성 + 라우트 정의
src/ui/               # UI 계층 (aac와 동일 레벨)
  tui/                # TUI (textual) — Phase 7
  gui/                # GUI (React) — Phase 8
resources/            # 리소스 정의 (YAML)
  agents/             # Agent 정의 (agent.yaml)
  tools/              # Tool 번들 정의 (tool.yaml)
  skills/             # Skill 정의 (skill.yaml + SKILL.md)
  aspects/            # Aspect 정의 (*.yaml)
  workflows/          # 워크플로우 정의 — Phase 6
```

## 빌드 & 실행

```bash
# 의존성 설치
uv sync --all-extras

# 서버 시작
uv run python -c "
import asyncio
from aac.server.app import start_server
asyncio.run(start_server('./resources', '127.0.0.1', 8800))
"

# YAML 검증 테스트
uv run python -c "
from aac.scanner import AgentScanner
result = AgentScanner('./resources').scan_all()
print(f'Agents: {len(result.agents)}, Tools: {result.total_tools}, Errors: {len(result.errors)}')
"

# 테스트 실행
uv run pytest tests/ -v
```

## 코딩 컨벤션

- **언어**: 코드 식별자(변수명, 함수명, 클래스명)는 영어. 주석, docstring, 로그 메시지는 한국어
- **타입 힌트**: 모든 함수에 type hints 필수 (`from __future__ import annotations`)
- **비동기**: I/O 바운드 함수는 `async def` 사용
- **모델**: Pydantic v2 (`BaseModel`, `model_validate`)
- **로깅**: `structlog.get_logger()` — 구조화 로그
- **콘솔 로그 포맷**: `[HH:mm:ss:SSS] [Agent] [sessionId:txId] msg`

## 핵심 설계 결정 (Decision Records)

| ID | 결정 |
|---|---|
| DR-1 | Tool 식별: `{bundle}/{item}`. 다른 번들 간 충돌: last-wins + 경고. `strict: true` 시 기동 실패 |
| DR-2 | Prompt 합성: `system_prompt` → `prompt_file` → skill 문서. 중복 skill 무시 |
| DR-3 | 식별자: `sess_{uuid}`, `tx_{seq:03d}`, `exec_{uuid}` |
| DR-5 | Execute API: 기본 동기. `Accept: text/event-stream` → SSE. `?async=true` → 202+폴링 |
| DR-7 | `max_turns`는 `spec.limits.max_turns`에만 정의. 기본값 30 |
| DR-8 | Scope: singleton(Context 수명), task(워크플로우 단위), session(API 요청 단위) |

## 리소스 YAML 스키마 요약

### Agent (`resources/agents/*/agent.yaml`)
필수: `apiVersion`, `kind: Agent`, `metadata.name`, `spec.runtime`
주요 필드: `spec.tools[].ref|name`, `spec.skills[].ref`, `spec.scope`, `spec.lazy`, `spec.limits.max_turns`

### Tool (`resources/tools/*/tool.yaml`)
필수: `kind: Tool`, `metadata.name`, `spec.items[].name`
각 item: `name`, `description`, `input_schema`, `output_schema`, `config`

### Skill (`resources/skills/*/skill.yaml`)
필수: `kind: Skill`, `metadata.name`, `spec.instruction_file`
선택: `spec.required_tools[]` — 이 skill 사용 시 agent에 해당 tool 필요

### Aspect (`resources/aspects/*.yaml`)
필수: `kind: Aspect`, `metadata.name`, `spec.type`, `spec.order`
`spec.pointcut.events[]`: PreQuery, PostQuery, PreToolUse, PostToolUse, OnError

## Phase 1 수용 기준 (현재)

- AC-1: `aac start` → 스캔 결과 로그 + `/api/status` 정상 응답 ✓
- AC-2: `/api/agents` — `tools_loaded_count`, `skills` 목록 노출 ✓
- AC-3: `/api/agents/{name}/execute` → 실행 + 로그 포맷 + 식별자 반환
- AC-4: 잘못된 YAML → 부팅 전 검출 + 파일 경로/필드 포함 에러 ✓
