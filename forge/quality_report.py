from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from forge.task_builder import load_metadata, verification_path
from forge.task_schema import VerificationResult


def recommend_status(verification: VerificationResult) -> str:
    if not verification.base_commit_found or not verification.patch_applies or not verification.docker_build_success:
        return "invalid"
    if (
        verification.tests_fail_before_patch
        and verification.tests_pass_after_patch
        and verification.deterministic_rerun_success
    ):
        return "usable"
    return "needs_review"


def build_quality_report(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    metadata = load_metadata(root, task_id)
    path = verification_path(root, task_id)
    if not path.exists():
        raise FileNotFoundError(f"Verification not found. Run `forge verify {task_id}` first: {path}")
    verification = VerificationResult.read_json(path)
    return {
        "task_id": task_id,
        "repo_name": metadata.repo_name,
        "pr_number": metadata.pr_number,
        "base_commit_found": verification.base_commit_found,
        "patch_applies_cleanly": verification.patch_applies,
        "tests_fail_before_patch": verification.tests_fail_before_patch,
        "tests_pass_after_patch": verification.tests_pass_after_patch,
        "deterministic_rerun_success": verification.deterministic_rerun_success,
        "environment_build_status": verification.docker_build_success,
        "recommended_status": recommend_status(verification),
        "errors": verification.errors,
    }


def render_quality_report(console: Console, report: dict[str, Any]) -> None:
    table = Table(title=f"Task Quality Report: {report['task_id']}")
    table.add_column("Check")
    table.add_column("Status")

    checks = [
        ("Repository", f"{report['repo_name']}#{report['pr_number']}"),
        ("Base commit found", _status(report["base_commit_found"])),
        ("Patch applies cleanly", _status(report["patch_applies_cleanly"])),
        ("Tests fail before patch", _status(report["tests_fail_before_patch"])),
        ("Tests pass after patch", _status(report["tests_pass_after_patch"])),
        ("Deterministic rerun", _status(report["deterministic_rerun_success"])),
        ("Environment build", _status(report["environment_build_status"])),
        ("Recommended status", report["recommended_status"]),
    ]
    for name, status in checks:
        table.add_row(name, status)
    console.print(table)

    if report["errors"]:
        console.print("[bold yellow]Verification errors[/bold yellow]")
        for error in report["errors"]:
            console.print(f"- {error}")


def _status(value: bool) -> str:
    return "[green]yes[/green]" if value else "[red]no[/red]"