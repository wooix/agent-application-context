"""AAC CLI — Click 기반 커맨드라인 인터페이스 (Phase 5).

`aac` 명령으로 서버 제어, Agent 관리, YAML 검증 등을 수행한다.
Spring Boot의 `./gradlew bootRun` / actuator 에 해당하는 역할.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
error_console = Console(stderr=True)

# ─── 유틸리티 ──────────────────────────────────────────


def _run_async(coro: Any) -> Any:
    """비동기 함수를 동기적으로 실행."""
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ 중단됨[/yellow]")
        sys.exit(130)


def _resolve_resources_dir(resources: str | None) -> Path:
    """resources 디렉토리 경로 해석."""
    if resources:
        p = Path(resources)
    else:
        # 현재 디렉토리 기준 탐색
        p = Path.cwd() / "resources"
    if not p.exists():
        error_console.print(f"[red]✗ resources 디렉토리 없음: {p}[/red]")
        sys.exit(1)
    return p


# ─── 메인 그룹 ─────────────────────────────────────────

AAC_BANNER = r"""
  ___    ___    ___
 / _ \  / _ \  / __| Agent Application Context
| (_| || (_| || (__
 \__,_| \__,_| \___|  v0.1.0
"""


@click.group()
@click.version_option(version="0.1.0", prog_name="aac")
def cli() -> None:
    """🤖 AAC — Agent Application Context CLI.

    Spring-inspired IoC/DI/AOP 기반 AI Agent 오케스트레이션 프레임워크.
    """


# ─── aac start ─────────────────────────────────────────


@cli.command()
@click.option(
    "--resources", "-r",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="resources/ 디렉토리 경로 (기본: ./resources)",
)
@click.option("--host", "-h", default="127.0.0.1", help="바인딩 호스트 (기본: 127.0.0.1)")
@click.option("--port", "-p", default=8800, type=int, help="포트 번호 (기본: 8800)")
@click.option("--strict", is_flag=True, help="strict mode — tool 충돌 시 기동 실패")
def start(
    resources: str | None,
    host: str,
    port: int,
    strict: bool,
) -> None:
    """🚀 AAC 서버 시작 — Context 기동 + HTTP API 서버.

    Spring Boot의 `./gradlew bootRun`에 해당.
    """
    from aac.server.app import start_server

    resources_path = _resolve_resources_dir(resources)

    _run_async(start_server(
        resources_dir=str(resources_path),
        host=host,
        port=port,
        strict_tools=strict,
    ))


# ─── aac validate ──────────────────────────────────────


@cli.command()
@click.option(
    "--resources", "-r",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="resources/ 디렉토리 경로 (기본: ./resources)",
)
@click.option("--verbose", "-v", is_flag=True, help="상세 출력")
def validate(resources: str | None, verbose: bool) -> None:
    """✅ YAML 리소스 검증 — 부팅 없이 스캔만 수행.

    모든 agent.yaml, tool.yaml, skill.yaml, aspect.yaml을 파싱하고,
    스키마 오류, 누락 필드, 참조 불일치를 보고한다.
    """
    from aac.scanner import AgentScanner

    resources_path = _resolve_resources_dir(resources)
    scanner = AgentScanner(resources_path)
    result = scanner.scan_all()

    # 요약 테이블
    summary_table = Table(title="📂 스캔 결과", show_header=True, header_style="bold cyan")
    summary_table.add_column("리소스", style="bold")
    summary_table.add_column("개수", justify="right")
    summary_table.add_column("상태", justify="center")

    def _status(count: int, errors: int = 0) -> Text:
        if errors > 0:
            return Text(f"⚠ {errors} 에러", style="red bold")
        if count > 0:
            return Text("✓", style="green bold")
        return Text("—", style="dim")

    agent_errors = sum(1 for e in result.errors if "agent" in str(e.file_path).lower())
    tool_errors = sum(1 for e in result.errors if "tool" in str(e.file_path).lower())
    skill_errors = sum(1 for e in result.errors if "skill" in str(e.file_path).lower())
    aspect_errors = sum(1 for e in result.errors if "aspect" in str(e.file_path).lower())

    summary_table.add_row(
        "Agents", str(len(result.agents)),
        _status(len(result.agents), agent_errors),
    )
    summary_table.add_row(
        "Tools",
        f"{len(result.tools)} bundles ({result.total_tools} items)",
        _status(len(result.tools), tool_errors),
    )
    summary_table.add_row(
        "Skills", str(len(result.skills)),
        _status(len(result.skills), skill_errors),
    )
    summary_table.add_row(
        "Aspects", str(len(result.aspects)),
        _status(len(result.aspects), aspect_errors),
    )

    console.print()
    console.print(summary_table)

    # 에러 상세
    if result.errors:
        console.print()
        err_table = Table(title="⚠ 스캔 에러", show_header=True, header_style="bold red")
        err_table.add_column("파일", style="dim")
        err_table.add_column("유형", style="yellow")
        err_table.add_column("필드", style="cyan")
        err_table.add_column("메시지", style="red")

        for err in result.errors:
            err_table.add_row(
                str(err.file_path),
                err.error_type,
                err.field or "—",
                err.message,
            )
        console.print(err_table)
        sys.exit(1)

    # 상세 모드
    if verbose:
        console.print()
        _print_agents_table(result.agents)

    console.print()
    total = len(result.agents) + len(result.tools) + len(result.skills) + len(result.aspects)
    console.print(
        Panel(
            f"[green bold]✓[/green bold] 모든 리소스 검증 통과 — "
            f"총 {total}개 리소스",
            style="green",
        )
    )


def _print_agents_table(agents: list) -> None:
    """Agent 목록을 Rich 테이블로 출력."""
    table = Table(title="🤖 Agents", show_header=True, header_style="bold magenta")
    table.add_column("이름", style="bold")
    table.add_column("Runtime", style="cyan")
    table.add_column("Scope", style="yellow")
    table.add_column("Lazy", justify="center")
    table.add_column("Tools", justify="right")
    table.add_column("Skills", justify="right")

    for agent in agents:
        tool_count = len(agent.spec.tools) if agent.spec.tools else 0
        skill_count = len(agent.spec.skills) if agent.spec.skills else 0
        lazy = "⏸" if agent.spec.lazy else "—"
        table.add_row(
            agent.metadata.name,
            agent.spec.runtime,
            agent.spec.scope.value if hasattr(agent.spec.scope, "value") else str(agent.spec.scope),
            lazy,
            str(tool_count),
            str(skill_count),
        )
    console.print(table)


# ─── aac agents ────────────────────────────────────────


@cli.command()
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
@click.option("--local", "-l", is_flag=True, help="로컬 resources/ 직접 스캔 (서버 불필요)")
@click.option(
    "--resources", "-r",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="--local 사용 시 resources/ 경로",
)
def agents(url: str, local: bool, resources: str | None) -> None:
    """🤖 Agent 목록 조회."""
    if local:
        from aac.scanner import AgentScanner

        resources_path = _resolve_resources_dir(resources)
        result = AgentScanner(resources_path).scan_all()
        _print_agents_table(result.agents)
        return

    # HTTP 클라이언트로 서버에서 조회
    _fetch_and_display(f"{url}/api/agents", "agents")


# ─── aac tools ─────────────────────────────────────────


@cli.command()
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
@click.option("--local", "-l", is_flag=True, help="로컬 resources/ 직접 스캔")
@click.option(
    "--resources", "-r",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="--local 사용 시 resources/ 경로",
)
def tools(url: str, local: bool, resources: str | None) -> None:
    """🔧 Tool 목록 조회."""
    if local:
        from aac.di.tool_registry import ToolRegistry
        from aac.scanner import AgentScanner

        resources_path = _resolve_resources_dir(resources)
        result = AgentScanner(resources_path).scan_all()
        registry = ToolRegistry()
        for tool in result.tools:
            registry.register(tool)

        table = Table(title="🔧 Tools", show_header=True, header_style="bold cyan")
        table.add_column("Bundle", style="bold")
        table.add_column("Tool", style="cyan")
        table.add_column("설명")

        bundle_summary = registry.list_all()  # {name: item_count}
        for bundle_name in bundle_summary:
            manifest = registry.get(bundle_name)
            for item in manifest.spec.items:
                table.add_row(bundle_name, item.name, item.description or "—")

        console.print(table)
        return

    _fetch_and_display(f"{url}/api/tools", "tools")


# ─── aac skills ────────────────────────────────────────


@cli.command()
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
@click.option("--local", "-l", is_flag=True, help="로컬 resources/ 직접 스캔")
@click.option(
    "--resources", "-r",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="--local 사용 시 resources/ 경로",
)
def skills(url: str, local: bool, resources: str | None) -> None:
    """📋 Skill 목록 조회."""
    if local:
        from aac.scanner import AgentScanner

        resources_path = _resolve_resources_dir(resources)
        result = AgentScanner(resources_path).scan_all()

        table = Table(title="📋 Skills", show_header=True, header_style="bold green")
        table.add_column("이름", style="bold")
        table.add_column("Instruction 파일", style="cyan")
        table.add_column("Required Tools", style="yellow")

        for skill in result.skills:
            req_tools = ", ".join(skill.spec.required_tools) if skill.spec.required_tools else "—"
            table.add_row(
                skill.metadata.name,
                skill.spec.instruction_file,
                req_tools,
            )
        console.print(table)
        return

    _fetch_and_display(f"{url}/api/skills", "skills")


# ─── aac status ────────────────────────────────────────


@cli.command()
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
def status(url: str) -> None:
    """📊 서버 상태 조회 — Context, Agent, Tool, Skill 요약."""
    import json

    try:
        import urllib.request

        with urllib.request.urlopen(f"{url}/api/status", timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        error_console.print(f"[red]✗ 서버 연결 실패: {e}[/red]")
        error_console.print(f"[dim]  서버가 실행 중인지 확인: {url}[/dim]")
        sys.exit(1)

    # 서버 상태 패널
    if data.get("started"):
        status_text = "[green bold]● RUNNING[/green bold]"
    else:
        status_text = "[red bold]● STOPPED[/red bold]"

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("상태", status_text)
    table.add_row("버전", data.get("version", "?"))
    table.add_row("시작 시각", data.get("started_at", "—"))

    agents_info = data.get("agents", {})
    a_total = agents_info.get("total", 0)
    a_active = agents_info.get("active", 0)
    a_lazy = agents_info.get("lazy", 0)
    table.add_row("Agents", f"{a_total} total, {a_active} active, {a_lazy} lazy")

    tools_info = data.get("tools", {})
    t_bundles = tools_info.get("bundles", 0)
    t_total = tools_info.get("total", 0)
    table.add_row("Tools", f"{t_bundles} bundles ({t_total} items)")

    skills_info = data.get("skills", {})
    table.add_row("Skills", f"{skills_info.get('total', 0)} total")

    aspects_info = data.get("aspects", {})
    table.add_row("Aspects", f"{aspects_info.get('total', 0)} total")

    console.print()
    console.print(Panel(table, title="📊 AAC Server Status", border_style="cyan"))
    console.print()


# ─── aac execute ───────────────────────────────────────


@cli.command()
@click.argument("agent_name")
@click.argument("prompt")
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
@click.option("--stream", "-s", is_flag=True, help="SSE 스트리밍 모드")
@click.option("--async-mode", "-a", is_flag=True, help="비동기 실행 모드")
def execute(agent_name: str, prompt: str, url: str, stream: bool, async_mode: bool) -> None:
    """⚡ Agent 실행 — 프롬프트를 Agent에게 전달.

    \b
    예시:
      aac execute claude-coder "Hello, world를 출력하는 Python 코드를 작성해줘"
      aac execute claude-coder "코드 리뷰해줘" --stream
      aac execute claude-coder "분석해줘" --async-mode
    """
    import json
    import urllib.request

    try:
        payload = json.dumps({"prompt": prompt}).encode()
        headers = {"Content-Type": "application/json"}

        if stream:
            headers["Accept"] = "text/event-stream"
        if async_mode:
            url_with_params = f"{url}/api/agents/{agent_name}/execute?async=true"
        else:
            url_with_params = f"{url}/api/agents/{agent_name}/execute"

        req = urllib.request.Request(url_with_params, data=payload, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=600) as resp:
            if stream:
                # SSE 스트리밍
                console.print(f"[dim]▶ Streaming from {agent_name}...[/dim]")
                for raw_line in resp:
                    line = raw_line.decode().strip()
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            event = json.loads(data_str)
                            _render_sse_event(event)
                        except json.JSONDecodeError:
                            console.print(data_str, end="")
            else:
                data = json.loads(resp.read().decode())
                if async_mode:
                    _render_async_response(data)
                else:
                    _render_execute_response(data)

    except Exception as e:
        error_console.print(f"[red]✗ 실행 실패: {e}[/red]")
        sys.exit(1)


def _render_execute_response(data: dict[str, Any]) -> None:
    """동기 실행 결과 렌더링."""
    success = data.get("success", False)
    icon = "[green]✓[/green]" if success else "[red]✗[/red]"

    console.print()
    console.print(Panel(
        f"{icon} Agent: [bold]{data.get('agent', '?')}[/bold]\n"
        f"  execution_id: [dim]{data.get('execution_id', '?')}[/dim]\n"
        f"  session_id: [dim]{data.get('session_id', '?')}[/dim]\n"
        f"  tx_id: [dim]{data.get('tx_id', '?')}[/dim]\n"
        f"  model: {data.get('model', '?')}\n"
        f"  cost: ${data.get('cost_usd', 0):.4f}\n"
        f"  duration: {data.get('duration_ms', 0)}ms",
        title="⚡ Execution Result",
        border_style="green" if success else "red",
    ))

    if data.get("result"):
        console.print()
        console.print(Panel(data["result"], title="📝 Response", border_style="blue"))

    if data.get("error"):
        console.print()
        error_console.print(Panel(data["error"], title="❌ Error", border_style="red"))


def _render_async_response(data: dict[str, Any]) -> None:
    """비동기 실행 응답 렌더링."""
    console.print()
    console.print(Panel(
        f"execution_id: [bold]{data.get('execution_id', '?')}[/bold]\n"
        f"status: [yellow]{data.get('status', '?')}[/yellow]\n"
        f"poll_url: [cyan]{data.get('poll_url', '?')}[/cyan]",
        title="⏳ Async Execution Started",
        border_style="yellow",
    ))
    console.print(f"\n[dim]폴링 확인: aac poll {data.get('execution_id', '')}[/dim]")


def _render_sse_event(event: dict[str, Any]) -> None:
    """SSE 이벤트를 리치 출력."""
    event_type = event.get("type", "")
    content = event.get("content", "")

    if event_type == "text" and content:
        console.print(content, end="")
    elif event_type == "tool_call":
        tool_name = event.get("tool_name", "?")
        console.print(f"\n[yellow]🔧 Tool: {tool_name}[/yellow]")
    elif event_type == "error":
        error_console.print(f"\n[red]❌ Error: {content}[/red]")
    elif event_type == "done":
        meta = event.get("metadata", {})
        console.print(
            f"\n[green]✓ Done[/green] "
            f"({meta.get('duration_ms', 0)}ms, ${meta.get('cost_usd', 0):.4f})"
        )


# ─── aac poll ──────────────────────────────────────────


@cli.command()
@click.argument("execution_id")
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
@click.option("--watch", "-w", is_flag=True, help="완료될 때까지 반복 폴링")
@click.option("--interval", default=2.0, type=float, help="폴링 간격 (초)")
def poll(execution_id: str, url: str, watch: bool, interval: float) -> None:
    """🔍 비동기 실행 상태 폴링.

    \b
    예시:
      aac poll exec_a1b2c3d4
      aac poll exec_a1b2c3d4 --watch
    """
    import json
    import time
    import urllib.request

    while True:
        try:
            with urllib.request.urlopen(
                f"{url}/api/executions/{execution_id}", timeout=5
            ) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            error_console.print(f"[red]✗ 조회 실패: {e}[/red]")
            sys.exit(1)

        status_val = data.get("status", "unknown")
        if status_val == "running":
            status_display = "[yellow]● RUNNING[/yellow]"
        elif status_val == "completed":
            status_display = "[green]● COMPLETED[/green]"
        elif status_val == "error":
            status_display = "[red]● ERROR[/red]"
        elif status_val == "cancelled":
            status_display = "[dim]● CANCELLED[/dim]"
        else:
            status_display = f"[dim]● {status_val}[/dim]"

        if not watch or status_val != "running":
            # 최종 출력
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Key", style="bold")
            table.add_column("Value")
            table.add_row("Execution", data.get("execution_id", "?"))
            table.add_row("Agent", data.get("agent", "?"))
            table.add_row("Status", status_display)

            if data.get("result"):
                table.add_row("Result", data["result"][:200])
            if data.get("error"):
                table.add_row("Error", f"[red]{data['error']}[/red]")
            if data.get("cost_usd"):
                table.add_row("Cost", f"${data['cost_usd']:.4f}")
            if data.get("duration_ms"):
                table.add_row("Duration", f"{data['duration_ms']}ms")

            console.print()
            console.print(Panel(table, title="🔍 Execution Status", border_style="cyan"))
            break

        # 진행 중이면 간단 표시 후 대기
        console.print(f"  [dim]{status_display} {execution_id}... ({interval}s 후 재시도)[/dim]")
        time.sleep(interval)

    console.print()


# ─── aac cancel ────────────────────────────────────────


@cli.command()
@click.argument("execution_id")
@click.option("--url", default="http://127.0.0.1:8800", help="AAC 서버 URL")
def cancel(execution_id: str, url: str) -> None:
    """🛑 실행 취소.

    \b
    예시:
      aac cancel exec_a1b2c3d4
    """
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{url}/api/executions/{execution_id}",
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        status_val = data.get("status", "?")
        if status_val == "cancelled":
            console.print(f"[green]✓ 실행 취소됨: {execution_id}[/green]")
        else:
            console.print(f"[yellow]⚠ 취소 불가: {status_val}[/yellow]")
    except Exception as e:
        error_console.print(f"[red]✗ 취소 실패: {e}[/red]")
        sys.exit(1)


# ─── HTTP 유틸리티 ─────────────────────────────────────


def _fetch_and_display(url: str, resource_type: str) -> None:
    """HTTP GET으로 서버에서 데이터를 가져와 출력."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        error_console.print(f"[red]✗ 서버 연결 실패: {e}[/red]")
        error_console.print("[dim]  서버가 실행 중인지 확인하거나 --local 옵션 사용[/dim]")
        sys.exit(1)

    if resource_type == "agents":
        _render_agents_from_api(data)
    elif resource_type == "tools":
        _render_tools_from_api(data)
    elif resource_type == "skills":
        _render_skills_from_api(data)
    else:
        console.print_json(json.dumps(data, ensure_ascii=False, indent=2))


def _render_agents_from_api(data: list[dict[str, Any]]) -> None:
    """서버 API에서 가져온 Agent 목록 출력."""
    table = Table(title="🤖 Agents", show_header=True, header_style="bold magenta")
    table.add_column("이름", style="bold")
    table.add_column("Status", style="cyan")
    table.add_column("Runtime", style="yellow")
    table.add_column("Scope")
    table.add_column("Tools", justify="right")
    table.add_column("Skills", justify="right")
    table.add_column("Queries", justify="right")

    for agent in data:
        status_val = agent.get("status", "?")
        if status_val == "ready":
            status_display = "[green]●[/green] ready"
        elif status_val == "lazy":
            status_display = "[yellow]⏸[/yellow] lazy"
        elif status_val == "executing":
            status_display = "[blue]▶[/blue] exec"
        else:
            status_display = status_val

        table.add_row(
            agent.get("name", "?"),
            status_display,
            agent.get("runtime", "?"),
            agent.get("scope", "?"),
            str(agent.get("tools_loaded_count", 0)),
            str(len(agent.get("skills", []))),
            str(agent.get("query_count", 0)),
        )
    console.print(table)


def _render_tools_from_api(data: list[dict[str, Any]]) -> None:
    """서버 API에서 가져온 Tool 목록 출력."""
    table = Table(title="🔧 Tools", show_header=True, header_style="bold cyan")
    table.add_column("Bundle", style="bold")
    table.add_column("Tool", style="cyan")
    table.add_column("설명")

    for bundle in data:
        bundle_name = bundle.get("bundle", "?")
        items = bundle.get("items", [])
        for item in items:
            table.add_row(bundle_name, item.get("name", "?"), item.get("description", "—"))
    console.print(table)


def _render_skills_from_api(data: list[dict[str, Any]]) -> None:
    """서버 API에서 가져온 Skill 목록 출력."""
    table = Table(title="📋 Skills", show_header=True, header_style="bold green")
    table.add_column("이름", style="bold")
    table.add_column("Instruction", style="cyan")
    table.add_column("Required Tools", style="yellow")

    for skill in data:
        table.add_row(
            skill.get("name", "?"),
            skill.get("instruction_file", "—"),
            ", ".join(skill.get("required_tools", [])) or "—",
        )
    console.print(table)


# ─── 엔트리포인트 ──────────────────────────────────────

if __name__ == "__main__":
    cli()
