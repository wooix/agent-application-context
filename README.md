<p align="center">
  <pre>
  ___    ___    ___
 / _ \  / _ \  / __| Agent Application Context
| (_| || (_| || (__
 \__,_| \__,_| \___|  v0.1.0
  </pre>
</p>

<h3 align="center">Spring-inspired IoC/DI/AOP framework for AI Agent orchestration</h3>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?style=flat-square" />
  <img src="https://img.shields.io/badge/framework-FastAPI-009688?style=flat-square" />
  <img src="https://img.shields.io/badge/tests-205%20passed-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" />
</p>

---

## 📖 프로젝트 개요

**AAC(Agent Application Context)** 는 Spring Framework의 IoC/DI/AOP 패턴을 AI Agent 오케스트레이션에 적용한 Python 프레임워크입니다.

다양한 LLM Runtime(Claude Code, Gemini, OpenAI, Codex)을 YAML 선언만으로 등록/주입/관리하며, REST API・CLI・WebSocket을 통해 Agent를 실행합니다.

### 핵심 컨셉 매핑

| Spring Framework             | AAC                                     |
| ---------------------------- | --------------------------------------- |
| `ApplicationContext`         | `AgentApplicationContext`               |
| `@ComponentScan`             | `AgentScanner` (resources/ YAML 스캔)   |
| `BeanFactory` + `@Autowired` | `AgentFactory` (Tool/Skill DI)          |
| `@Aspect` / AOP              | `AspectEngine` (감사/추적/로깅)         |
| Spring Batch Job             | `WorkflowEngine` (순차/병렬/조건)       |
| BeanPostProcessor            | `LifecycleManager` (상태 전이/건강검사) |
| Embedded Tomcat              | FastAPI + uvicorn                       |
| Spring Boot CLI              | Click CLI (`aac` 명령)                  |

---

## 🏗️ 아키텍처

```
resources/                    ← YAML 선언 (agents, tools, skills, aspects, workflows)
  agents/claude-coder/agent.yaml
  tools/file-ops/tool.yaml
  workflows/code-review-pipeline.yaml

src/aac/                      ← 프레임워크 코어 (5,100+ LOC)
  context.py                  ← IoC 컨테이너
  scanner.py                  ← 리소스 스캐너
  factory.py                  ← Agent 팩토리 (DI)
  runtime/                    ← LLM 런타임 어댑터
  aspects/                    ← AOP 위빙 엔진
  orchestration/engine.py     ← 워크플로우 엔진
  lifecycle/manager.py        ← 생명주기 관리
  cli/main.py                 ← CLI 엔트리포인트
  server/app.py               ← FastAPI HTTP API

tests/                        ← 테스트 (3,400+ LOC, 205 테스트)
```

---

## 🚀 빠른 시작

```bash
# 의존성 설치
uv sync --all-extras

# YAML 리소스 검증
uv run aac validate

# 서버 시작
uv run aac start

# Agent 목록 (로컬)
uv run aac agents --local

# Agent 실행 (서버 필요)
uv run aac execute claude-coder "Hello, World를 출력하는 코드 작성"

# 스트리밍 실행
uv run aac execute claude-coder "코드 리뷰해줘" --stream

# 비동기 실행
uv run aac execute claude-coder "분석해줘" --async-mode
uv run aac poll exec_xxxxxxx --watch
```

---

## 📡 API 엔드포인트

| Method   | Path                                    | 설명                 |
| -------- | --------------------------------------- | -------------------- |
| `GET`    | `/api/health`                           | 헬스체크             |
| `GET`    | `/api/status`                           | Context 전체 상태    |
| `GET`    | `/api/agents`                           | Agent 목록           |
| `GET`    | `/api/agents/{name}`                    | Agent 상세           |
| `POST`   | `/api/agents/{name}/execute`            | Agent 실행           |
| `POST`   | `/api/agents/{name}/execute?async=true` | 비동기 실행 (202)    |
| `GET`    | `/api/executions/{id}`                  | 실행 상태 폴링       |
| `DELETE` | `/api/executions/{id}`                  | 실행 취소            |
| `GET`    | `/api/tools`                            | Tool 목록            |
| `GET`    | `/api/skills`                           | Skill 목록           |
| `WS`     | `/ws/events`                            | 실시간 이벤트 스트림 |

**SSE 스트리밍**: `Accept: text/event-stream` 헤더로 실시간 청크 수신

---

## 🧩 YAML 리소스 예시

### Agent 정의
```yaml
apiVersion: aac/v1
kind: Agent
metadata:
  name: claude-coder
  description: "코드 생성 전문 Agent"
spec:
  runtime: claude-code
  tools:
    - ref: file-ops
    - ref: code-exec
  skills:
    - ref: code-review
  scope: singleton
  lazy: false
  limits:
    max_turns: 30
    timeout_seconds: 600
```

### 워크플로우 정의
```yaml
apiVersion: aac/v1
kind: Workflow
metadata:
  name: code-review-pipeline
spec:
  steps:
    - name: generate
      type: agent
      agent: claude-coder
      prompt: "유틸리티 함수를 생성해줘"

    - name: review
      type: agent
      agent: gemini-critic
      prompt: "코드를 리뷰해줘"
      input_from: generate

    - name: parallel-analysis
      type: parallel
      steps:
        - name: security
          agent: claude-coder
          prompt: "보안 분석"
        - name: perf
          agent: gemini-critic
          prompt: "성능 분석"
```

---

## 📊 구현 진행 상황

### Phase 완료 현황

| Phase | 기능                                                 |   상태   | 테스트 |
| :---: | ---------------------------------------------------- | :------: | :----: |
| **1** | 코어 프레임워크 (IoC, Scanner, Factory, DI)          |  ✅ 완료  |  54개  |
| **2** | Multi-Runtime 어댑터 (Claude, Gemini, OpenAI, Codex) |  ✅ 완료  |  18개  |
| **3** | AOP 위빙 엔진 (감사/추적/실행 로깅)                  |  ✅ 완료  |  18개  |
| **4** | SSE 스트리밍 + 비동기 실행 + WebSocket               |  ✅ 완료  |  30개  |
| **5** | CLI (start/validate/agents/tools/execute/poll...)    |  ✅ 완료  |  29개  |
| **6** | 워크플로우 오케스트레이션 (순차/병렬/조건 분기)      |  ✅ 완료  |  24개  |
| **7** | Lifecycle Manager (상태 전이/건강 검사/종료)         |  ✅ 완료  |  32개  |
| **8** | TUI (Textual 대시보드)                               | 🔲 미착수 |   —    |

### 전체 통계

| 항목                | 수치                                          |
| ------------------- | --------------------------------------------- |
| 소스 코드 (src/aac) | **5,100+ LOC**                                |
| 테스트 코드 (tests) | **3,400+ LOC**                                |
| 통과 테스트         | **205개**                                     |
| YAML 리소스         | 14개 (3 agents, 3 tools, 4 skills, 4 aspects) |
| 워크플로우 정의     | 2개                                           |
| Runtime 어댑터      | 4개                                           |

### Phase별 상세

<details>
<summary><strong>Phase 1 — 코어 프레임워크</strong></summary>

- `AgentApplicationContext` — IoC 컨테이너, 부트 시퀀스
- `AgentScanner` — resources/ 디렉토리 YAML 스캔 + 에러 보고
- `AgentFactory` — Agent 인스턴스 생성, Tool/Skill DI 주입
- `ToolRegistry` — Tool 번들 관리, 충돌 해결 (DR-1: last-wins / strict)
- `SkillRegistry` — Skill 관리
- FastAPI HTTP 서버 (`/api/status`, `/api/agents`, `/api/agents/{name}/execute`)
- 통일 로그 포맷: `[HH:mm:ss:SSS] [Agent] [sessionId:txId] msg`
</details>

<details>
<summary><strong>Phase 2 — Multi-Runtime</strong></summary>

- `RuntimeRegistry` — 런타임 등록 + 자동 발견 (resources/runtimes/*.yaml)
- `ClaudeCodeRuntime` — Claude Code CLI 연동
- `GeminiMCPRuntime` — Gemini MCP 프로토콜
- `OpenAIMCPRuntime` — OpenAI MCP 프로토콜
- `CodexCLIRuntime` — Codex CLI 연동
- 모든 Runtime에 `execute()`, `stream()`, `shutdown()` 인터페이스
</details>

<details>
<summary><strong>Phase 3 — AOP 위빙</strong></summary>

- `AspectEngine` — Aspect manifest 로딩, pointcut 매칭, 이벤트 발행
- `AuditLoggingHandler` — 쿼리/응답 감사 로깅 (마스킹 지원)
- `ExecutionLoggingHandler` — 실행 시간/비용 로깅
- `ToolTrackingHandler` — Tool 사용 추적
- Aspect YAML: `spec.pointcut.events[]`, `spec.order`
</details>

<details>
<summary><strong>Phase 4 — 스트리밍/비동기</strong></summary>

- SSE 스트리밍 (`Accept: text/event-stream` → `EventSourceResponse`)
- `StreamChunk` 모델 (text, tool_call, error, done + metadata)
- 비동기 실행 (`?async=true` → 202 + `execution_id` 반환)
- 폴링 (`GET /api/executions/{id}`) + 취소 (`DELETE /api/executions/{id}`)
- `WebSocketPublisher` — 실시간 이벤트 브로드캐스트
</details>

<details>
<summary><strong>Phase 5 — CLI</strong></summary>

- Click 기반 9개 명령어: `start`, `validate`, `agents`, `tools`, `skills`, `status`, `execute`, `poll`, `cancel`
- Rich 테이블/패널 출력
- 로컬 모드 (`--local`) — 서버 없이 resources/ 직접 스캔
- SSE 스트리밍 + 비동기 실행 지원
</details>

<details>
<summary><strong>Phase 6 — 워크플로우</strong></summary>

- `WorkflowManifest` — YAML 스키마 (순차/병렬/조건 스텝)
- `WorkflowEngine` — 다중 Agent 오케스트레이션
- 순차 실행 + `input_from` (이전 스텝 결과 연결)
- 병렬 실행 (`type: parallel` → `asyncio.gather`)
- 조건 분기 (`type: condition` → `if_true`/`if_false`)
- 재시도 (`retry_count`), 비용/시간 상한 (`max_total_cost_usd`)
</details>

<details>
<summary><strong>Phase 7 — Lifecycle Manager</strong></summary>

- `LifecycleManager` — Agent 상태 전이 검증
- `VALID_TRANSITIONS` — 유효 전이 맵 (무효 전이 차단)
- 건강 검사 (`check_health`, `check_all_health`)
- 우아한 종료 (`graceful_shutdown` — EXECUTING 대기 후 순차 종료)
- 이벤트 히스토리 + 콜백 시스템
</details>

---

## 🛠️ 기술 스택

| 분류          | 기술                    |
| ------------- | ----------------------- |
| 언어          | Python 3.12+            |
| 패키지 관리   | uv                      |
| 웹 프레임워크 | FastAPI + uvicorn       |
| 데이터 모델   | Pydantic v2             |
| CLI           | Click + Rich            |
| 로깅          | structlog               |
| 테스트        | pytest + pytest-asyncio |
| 린터          | ruff                    |
| 설정          | YAML (PyYAML)           |

---

## 📁 핵심 설계 결정 (Decision Records)

| ID   | 결정                                                                              |
| ---- | --------------------------------------------------------------------------------- |
| DR-1 | Tool 식별: `{bundle}/{item}`. 충돌: last-wins + 경고. `strict: true` 시 기동 실패 |
| DR-2 | Prompt 합성: `system_prompt` → `prompt_file` → skill 문서. 중복 skill 무시        |
| DR-3 | 식별자: `sess_{uuid}`, `tx_{seq:03d}`, `exec_{uuid}`                              |
| DR-5 | Execute API: 기본 동기. SSE or 비동기 선택 가능                                   |
| DR-7 | `max_turns`는 `spec.limits.max_turns`에만 정의. 기본값 30                         |
| DR-8 | Scope: singleton(Context 수명), task(워크플로우 단위), session(API 요청 단위)     |

---

## 📜 License

MIT License

