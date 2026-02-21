# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

Spring Framework의 IoC/DI/AOP 개념을 AI Agent 오케스트레이션에 적용한 Python 프레임워크.
다양한 LLM Runtime(Claude Code, Gemini, OpenAI, Codex)을 bean처럼 등록/주입/관리하며,
REST API endpoint를 통해 Agent를 실행한다.

> **참고**: `aac` CLI가 구현되어 있으며 (`aac.cli.main:cli`), `uv run aac` 또는 설치 후 `aac`로 사용 가능.

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
- **Click**: CLI (`src/aac/cli/main.py`)
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
    app.py            # 앱 생성 + 인라인 라우트 정의
    routes/           # (placeholder — 현재 라우트는 app.py에 인라인)
  cli/                # Click CLI 엔트리포인트
    main.py           # aac start|validate|agents|...
    commands/         # (확장용 하위 명령)
  lifecycle/          # (stub — Phase 3)
  orchestration/      # (stub — Phase 6)
  aspects/            # (stub — Aspect 런타임 처리)
src/ui/               # UI 계층 (aac와 동일 레벨)
  tui/                # TUI (textual) — Phase 7
  gui/                # GUI (React) — Phase 8
resources/            # 리소스 정의 (YAML)
  agents/             # Agent 정의 (agent.yaml)
  tools/              # Tool 번들 정의 (tool.yaml)
  skills/             # Skill 정의 (skill.yaml + SKILL.md)
  aspects/            # Aspect 정의 (*.yaml)
```

## 빌드 & 실행

```bash
# 의존성 설치
uv sync --all-extras

# 서버 시작 (CLI)
uv run aac start
uv run aac start --port 9000 --strict

# 서버 시작 (Python)
uv run python -c "
import asyncio; from aac.server.app import start_server
asyncio.run(start_server('./resources', '127.0.0.1', 8800))
"

# YAML 검증 (CLI)
uv run aac validate
uv run aac validate -v

# Agent/Tool/Skill 목록 (로컬)
uv run aac agents --local
uv run aac tools --local
uv run aac skills --local

# Agent 실행 (서버 필요)
uv run aac execute claude-coder "Hello, World"
uv run aac execute claude-coder "Hello" --stream
uv run aac execute claude-coder "Hello" --async-mode

# 상태 조회 (서버 필요)
uv run aac status

# YAML 검증 테스트
uv run python -c "
from aac.scanner import AgentScanner
result = AgentScanner('./resources').scan_all()
print(f'Agents: {len(result.agents)}, Tools: {result.total_tools}, Errors: {len(result.errors)}')
"

# 테스트 실행
uv run pytest tests/ -v

# 단일 테스트 실행
uv run pytest tests/test_scanner.py -v -k "test_name"

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

## 부트 시퀀스

`AgentApplicationContext.start()` 기동 순서 (`src/aac/context.py`):

1. 기본 Runtime 등록 (`claude-code` → `ClaudeCodeRuntime`)
2. `AgentScanner.scan_all()` — tools → skills → aspects → agents 순서로 스캔
3. `ToolRegistry` / `SkillRegistry`에 manifest 등록
4. `AgentFactory` 생성 (RuntimeRegistry + ToolRegistry + SkillRegistry 주입)
5. eager agent 초기화 (`spec.lazy: false`), lazy agent는 placeholder만 등록
6. FastAPI 서버 시작 (`create_app()` → `uvicorn.Server`)

## API 엔드포인트

`src/aac/server/app.py`에 인라인 정의된 라우트:

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/health` | 헬스체크 |
| `GET` | `/api/status` | Context 상태 (FR-9.1) |
| `GET` | `/api/agents` | Agent 목록 — `tools_loaded_count`, `skills` 포함 (AC-2) |
| `GET` | `/api/agents/{name}` | Agent 상세 정보 |
| `POST` | `/api/agents/{name}/execute` | Agent 실행 (FR-9.2) |
| `GET` | `/api/tools` | Tool 목록 |
| `GET` | `/api/skills` | Skill 목록 |

## 코딩 컨벤션

- **언어**: 코드 식별자(변수명, 함수명, 클래스명)는 영어. 주석, docstring, 로그 메시지는 한국어
- **타입 힌트**: 모든 함수에 type hints 필수 (`from __future__ import annotations`)
- **비동기**: I/O 바운드 함수는 `async def` 사용
- **모델**: Pydantic v2 (`BaseModel`, `model_validate`)
- **로깅**: `structlog.get_logger()` — 구조화 로그
- **콘솔 로그 포맷**: `[HH:mm:ss:SSS] [Agent] [sessionId:txId] msg`
- **Linter**: ruff — `line-length = 100`, `target-version = "py312"`, select: `["E", "F", "I", "N", "W", "UP"]`
- **테스트**: pytest — `asyncio_mode = "auto"`, `pythonpath = ["src"]`

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

## Phase 수용 기준

### Phase 1 — 코어 프레임워크
- AC-1: `aac start` → 스캔 결과 로그 + `/api/status` 정상 응답 ✓
- AC-2: `/api/agents` — `tools_loaded_count`, `skills` 목록 노출 ✓
- AC-3: `/api/agents/{name}/execute` → 실행 + 로그 포맷 + 식별자 반환 ✓
- AC-4: 잘못된 YAML → 부팅 전 검출 + 파일 경로/필드 포함 에러 ✓

### Phase 2 — Multi-Runtime
- 자동 발견 (resources/runtimes/*.yaml) ✓
- claude-code, gemini-mcp, openai-mcp, codex-cli 어댑터 ✓

### Phase 3 — AOP 위빙
- AspectEngine + AuditLogging/ExecutionLogging/ToolTracking Handler ✓
- Aspect manifest 로딩 + pointcut 매칭 ✓

### Phase 4 — 스트리밍/비동기
- SSE 스트리밍 (Accept: text/event-stream) ✓
- 비동기 실행 (?async=true → 202+폴링) ✓
- WebSocket 이벤트 퍼블리셔 ✓

### Phase 5 — CLI
- `aac start/validate/agents/tools/skills/status/execute/poll/cancel` ✓
- Rich 테이블/패널 출력 ✓
- 로컬 모드 (--local) + 서버 모드 지원 ✓
