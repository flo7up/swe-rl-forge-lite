from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from forge import cli

runner = CliRunner()


def test_verify_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    result_obj = SimpleNamespace(
        patch_applies=True,
        tests_fail_before_patch=True,
        tests_pass_after_patch=True,
        deterministic_rerun_success=True,
    )
    monkeypatch.setattr(cli, "verify_task", lambda task_id, log=None: result_obj)

    result = runner.invoke(cli.app, ["verify", "demo-001"])

    assert result.exit_code == 0
    assert "tests_pass_after_patch=True" in result.stdout


def test_verify_command_reports_error_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(task_id: str, log=None):  # noqa: ANN001, ANN202 - test stub
        raise RuntimeError("verification blew up")

    monkeypatch.setattr(cli, "verify_task", boom)

    result = runner.invoke(cli.app, ["verify", "demo-001"])

    assert result.exit_code == 1
    assert "verification blew up" in result.output


def test_package_command_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package_dir = tmp_path / "taskpacks" / "demo-001"
    monkeypatch.setattr(cli, "package_task", lambda task_id, log=None: package_dir)

    result = runner.invoke(cli.app, ["package", "demo-001"])

    assert result.exit_code == 0
    assert "Created taskpack" in result.stdout


def test_package_command_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(task_id: str, log=None):  # noqa: ANN001, ANN202 - test stub
        raise FileNotFoundError("missing verification")

    monkeypatch.setattr(cli, "package_task", boom)

    result = runner.invoke(cli.app, ["package", "demo-001"])

    assert result.exit_code == 1
    assert "missing verification" in result.output


def test_report_command_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "build_quality_report", lambda task_id: {"task_id": task_id})

    def fake_render(console, report) -> None:  # noqa: ANN001 - test stub
        captured["report"] = report

    monkeypatch.setattr(cli, "render_quality_report", fake_render)

    result = runner.invoke(cli.app, ["report", "demo-001"])

    assert result.exit_code == 0
    assert captured["report"] == {"task_id": "demo-001"}


def test_report_command_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(task_id: str):  # noqa: ANN202 - test stub
        raise FileNotFoundError("no metadata")

    monkeypatch.setattr(cli, "build_quality_report", boom)

    result = runner.invoke(cli.app, ["report", "demo-001"])

    assert result.exit_code == 1
    assert "no metadata" in result.output


def test_app_no_args_shows_help() -> None:
    result = runner.invoke(cli.app, [])

    assert "fetch" in result.output
    assert "verify" in result.output
