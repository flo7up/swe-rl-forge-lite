from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from forge.dashboard import DEFAULT_DASHBOARD_PATH, write_dashboard
from forge.dashboard_live import DEFAULT_CONTROL_CONFIG, DEFAULT_FRONTEND_DIST, DEFAULT_LIVE_HOST, DEFAULT_LIVE_PORT, serve_live_dashboard
from forge.explorer import DEFAULT_EXPLORE_QUERY, explore_github_prs, write_candidates_yaml
from forge.llm_review import DEFAULT_ENV_FILE, describe_llm_config, resolve_llm_config, review_exploration_candidates
from forge.quality_report import build_quality_report, render_quality_report
from forge.reward_runner import run_reward_script
from forge.task_builder import fetch_from_config, package_task, verify_task


app = typer.Typer(
    help="Forge reproducible SWE RL/evaluation tasks from historical GitHub pull requests.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


def _log(message: str) -> None:
    console.print(f"[cyan]forge[/cyan] {message}")


def _die(exc: Exception) -> None:
    error_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)


@app.command("fetch")
def fetch_command(
    config_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="YAML config describing GitHub PR tasks."),
) -> None:
    """Fetch GitHub PR metadata, clone repos, and save gold patches."""

    try:
        metadata_items = fetch_from_config(config_path, log=_log)
    except Exception as exc:  # noqa: BLE001 - CLI should show helpful user-facing errors.
        _die(exc)

    table = Table(title="Fetched Tasks")
    table.add_column("Task ID")
    table.add_column("Repository")
    table.add_column("PR")
    table.add_column("Base Commit")
    table.add_column("Head Commit")
    for metadata in metadata_items:
        table.add_row(
            metadata.id,
            metadata.repo_name,
            str(metadata.pr_number),
            metadata.base_commit[:12],
            metadata.head_commit[:12],
        )
    console.print(table)


@app.command("explore")
def explore_command(
    query: str | None = typer.Option(None, "--query", "-q", help="GitHub issue-search query for merged pull requests."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50, help="Maximum number of candidates to return."),
    output: Path | None = typer.Option(None, "--output", "-o", dir_okay=False, writable=True, help="Write candidate tasks YAML to this path."),
    test_command: str = typer.Option("pytest", help="Default test command for generated YAML."),
    language: str = typer.Option("python", help="Default task language for generated YAML."),
    timeout_seconds: int = typer.Option(300, min=1, help="Default timeout for generated YAML tasks."),
    llm_review: bool = typer.Option(False, "--llm-review", help="Use optional Azure OpenAI or Gemini review from .env to rerank candidates."),
    llm_provider: str = typer.Option("auto", "--llm-provider", help="LLM provider: auto, azure-openai, or gemini."),
    env_file: Path = typer.Option(DEFAULT_ENV_FILE, "--env-file", help="Path to .env with optional LLM provider settings."),
) -> None:
    """Search GitHub for likely PR task candidates and optionally write YAML."""

    try:
        candidates = explore_github_prs(
            query=query,
            limit=limit,
            test_command=test_command,
            language=language,
            timeout_seconds=timeout_seconds,
        )
        if llm_review:
            candidates = review_exploration_candidates(candidates, provider=llm_provider, env_file=env_file)
    except Exception as exc:  # noqa: BLE001
        _die(exc)

    if not candidates:
        console.print("No candidates found. Try a broader --query.")
        return

    table = Table(title="Exploration Candidates")
    table.add_column("Score")
    table.add_column("Task ID")
    table.add_column("Repository")
    table.add_column("PR")
    table.add_column("Patch")
    table.add_column("LLM")
    table.add_column("Reasons")
    for candidate in candidates:
        llm_cell = "-" if candidate.llm_score is None else f"{candidate.llm_score:.2f} {candidate.llm_risk or ''}".strip()
        reasons = ", ".join(candidate.reasons)
        if candidate.llm_rationale:
            reasons = f"{reasons}\nLLM: {candidate.llm_rationale}"
        table.add_row(
            f"{candidate.score:.2f}",
            candidate.id,
            candidate.repo_name,
            str(candidate.pr_number),
            f"+{candidate.additions}/-{candidate.deletions} files:{candidate.changed_files}",
            llm_cell,
            reasons,
        )
    console.print(table)

    if output is not None:
        write_candidates_yaml(candidates, output)
        console.print(f"Wrote candidate task config: [green]{output}[/green]")
    else:
        console.print(f"Default query: [dim]{query or DEFAULT_EXPLORE_QUERY}[/dim]")
        console.print("Use [bold]--output .forge/candidates.yaml[/bold] to save these as forge tasks.")


@app.command("llm-check")
def llm_check_command(
    provider: str = typer.Option("auto", "--provider", help="LLM provider: auto, azure-openai, or gemini."),
    env_file: Path = typer.Option(DEFAULT_ENV_FILE, "--env-file", dir_okay=False, readable=True, help="Path to .env with optional LLM provider settings."),
) -> None:
    """Validate optional LLM review configuration without printing secrets."""

    try:
        config = resolve_llm_config(provider, env_file)
    except Exception as exc:  # noqa: BLE001
        _die(exc)
    console.print(f"LLM review configured: [green]{describe_llm_config(config)}[/green]")


@app.command("verify")
def verify_command(task_id: str = typer.Argument(..., help="Task id from the YAML config.")) -> None:
    """Run pre/post patch verification in Docker and write verification.json."""

    try:
        result = verify_task(task_id, log=_log)
    except Exception as exc:  # noqa: BLE001
        _die(exc)

    console.print(f"Verification written for [bold]{task_id}[/bold]")
    console.print(f"patch_applies={result.patch_applies}")
    console.print(f"tests_fail_before_patch={result.tests_fail_before_patch}")
    console.print(f"tests_pass_after_patch={result.tests_pass_after_patch}")
    console.print(f"deterministic_rerun_success={result.deterministic_rerun_success}")


@app.command("package")
def package_command(task_id: str = typer.Argument(..., help="Task id to package.")) -> None:
    """Create a self-contained taskpack for a verified task."""

    try:
        package_dir = package_task(task_id, log=_log)
    except Exception as exc:  # noqa: BLE001
        _die(exc)
    console.print(f"Created taskpack: [green]{package_dir}[/green]")


@app.command("reward")
def reward_command(
    taskpack_path: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, help="Path to a taskpack directory."),
    patch: Path | None = typer.Option(
        None,
        "--patch",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Candidate fix (unified diff) to apply onto a copy of repo/ before scoring.",
    ),
) -> None:
    """Run a taskpack reward script and print JSON."""

    result = run_reward_script(taskpack_path, patch_path=patch)
    print(json.dumps(result.model_dump(), indent=2))


@app.command("report")
def report_command(task_id: str = typer.Argument(..., help="Task id to report on.")) -> None:
    """Print a compact task quality report."""

    try:
        report = build_quality_report(task_id)
    except Exception as exc:  # noqa: BLE001
        _die(exc)
    render_quality_report(console, report)


@app.command("dashboard")
def dashboard_command(
    output: Path = typer.Option(DEFAULT_DASHBOARD_PATH, "--output", "-o", dir_okay=False, writable=True, help="Path for the generated dashboard HTML."),
) -> None:
    """Generate a local static HTML dashboard for forge task artifacts."""

    try:
        dashboard_path = write_dashboard(output)
    except Exception as exc:  # noqa: BLE001
        _die(exc)
    console.print(f"Generated dashboard: [green]{dashboard_path}[/green]")


@app.command("dashboard-live")
def dashboard_live_command(
    host: str = typer.Option(DEFAULT_LIVE_HOST, "--host", help="Host interface for the live dashboard API/server."),
    port: int = typer.Option(DEFAULT_LIVE_PORT, "--port", min=1, max=65535, help="Port for the live dashboard API/server."),
    static_dir: Path = typer.Option(
        DEFAULT_FRONTEND_DIST,
        "--static-dir",
        help="Directory to serve static frontend assets from (typically frontend/dist).",
    ),
    open_browser: bool = typer.Option(False, "--open", help="Open the live dashboard in your browser after server start."),
    enable_controls: bool = typer.Option(False, "--enable-controls", help="Enable local dashboard buttons that run verify/package jobs."),
) -> None:
    """Serve a live JSON API and optional static frontend for real-time task observation."""

    try:
        console.print(f"Starting live dashboard at [green]http://{host}:{port}[/green]")
        console.print("API endpoint: [cyan]/api/tasks[/cyan]")
        if enable_controls:
            console.print(f"Controls enabled. Auto mode defaults to [cyan]{DEFAULT_CONTROL_CONFIG}[/cyan].")
        serve_live_dashboard(host=host, port=port, static_dir=static_dir, open_browser=open_browser, controls_enabled=enable_controls)
    except Exception as exc:  # noqa: BLE001
        _die(exc)


if __name__ == "__main__":
    app()