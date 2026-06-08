from __future__ import annotations

from pathlib import Path

from forge.dashboard_live import DashboardControlRunner, build_dashboard_snapshot
from forge.task_schema import CommandRun, TaskMetadata, VerificationResult


def test_build_dashboard_snapshot_includes_summary_and_tasks(tmp_path: Path) -> None:
    task_id = "live-001"
    task_dir = tmp_path / ".forge" / "tasks" / task_id
    task_dir.mkdir(parents=True)

    metadata = TaskMetadata(
        id=task_id,
        repo_url="https://github.com/example/project.git",
        pr_number=11,
        repo_name="example/project",
        pr_title="Fix timeout edge case",
        pr_body="",
        base_commit="abcdef123456",
        head_commit="123456abcdef",
        test_command="pytest",
        language="python",
    )
    metadata.write_json(task_dir / "metadata.json")
    (task_dir / "gold.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")

    verification = VerificationResult(
        task_id=task_id,
        base_commit_found=True,
        patch_applies=True,
        tests_fail_before_patch=True,
        tests_pass_after_patch=True,
        docker_build_success=True,
        deterministic_rerun_success=True,
        before_patch=CommandRun(phase="before_patch", command="pytest", exit_code=1, docker_build_success=True),
        after_patch=CommandRun(phase="after_patch", command="pytest", exit_code=0, docker_build_success=True),
        deterministic_rerun=CommandRun(phase="deterministic_rerun", command="pytest", exit_code=0, docker_build_success=True),
    )
    verification.write_json(task_dir / "verification.json")

    snapshot = build_dashboard_snapshot(tmp_path)

    assert snapshot["summary"]["total"] == 1
    assert snapshot["summary"]["usable"] == 1
    assert len(snapshot["tasks"]) == 1
    assert snapshot["tasks"][0]["id"] == task_id
    assert "generated_at" in snapshot


def _verification(task_id: str, *, valid: bool = True) -> VerificationResult:
    return VerificationResult(
        task_id=task_id,
        base_commit_found=valid,
        patch_applies=valid,
        tests_fail_before_patch=valid,
        tests_pass_after_patch=valid,
        docker_build_success=valid,
        test_environment_success=valid,
        deterministic_rerun_success=valid,
        before_patch=CommandRun(phase="before_patch", command="pytest", exit_code=1 if valid else 127, docker_build_success=valid),
        after_patch=CommandRun(phase="after_patch", command="pytest", exit_code=0 if valid else 127, docker_build_success=valid),
        deterministic_rerun=CommandRun(phase="deterministic_rerun", command="pytest", exit_code=0 if valid else 127, docker_build_success=valid),
    )


def test_control_runner_manual_verifies_and_packages(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_verify(task_id: str, *, root: Path | None = None, log=None) -> VerificationResult:
        calls.append(("verify", task_id))
        if log is not None:
            log("verification log")
        return _verification(task_id)

    def fake_package(task_id: str, *, root: Path | None = None, log=None) -> Path:
        calls.append(("package", task_id))
        return tmp_path / "taskpacks" / task_id

    monkeypatch.setattr("forge.dashboard_live.verify_task", fake_verify)
    monkeypatch.setattr("forge.dashboard_live.package_task", fake_package)

    runner = DashboardControlRunner(tmp_path)
    job = runner.start_manual_run("live-001")

    assert job["mode"] == "manual"
    assert runner.wait_for_current_job(timeout_seconds=2)

    snapshot = runner.snapshot()
    assert snapshot["job"]["status"] == "succeeded"
    assert calls == [("verify", "live-001"), ("package", "live-001")]
    assert any("verification log" in entry for entry in snapshot["job"]["logs"])


def test_control_runner_skips_package_for_invalid_task(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_verify(task_id: str, *, root: Path | None = None, log=None) -> VerificationResult:
        calls.append(("verify", task_id))
        return _verification(task_id, valid=False)

    def fake_package(task_id: str, *, root: Path | None = None, log=None) -> Path:
        calls.append(("package", task_id))
        return tmp_path / "taskpacks" / task_id

    monkeypatch.setattr("forge.dashboard_live.verify_task", fake_verify)
    monkeypatch.setattr("forge.dashboard_live.package_task", fake_package)

    runner = DashboardControlRunner(tmp_path)
    runner.start_manual_run("live-001")

    assert runner.wait_for_current_job(timeout_seconds=2)

    snapshot = runner.snapshot()
    assert snapshot["job"]["status"] == "succeeded"
    assert calls == [("verify", "live-001")]
    assert any("Skipping package" in entry for entry in snapshot["job"]["logs"])


def test_control_runner_auto_fetches_then_runs_each_task(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "tasks.yaml"
    config_path.write_text("tasks: []\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    class Metadata:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    def fake_fetch(path: Path, *, root: Path | None = None, log=None) -> list[Metadata]:
        calls.append(("fetch", path.name))
        return [Metadata("one"), Metadata("two")]

    def fake_verify(task_id: str, *, root: Path | None = None, log=None) -> VerificationResult:
        calls.append(("verify", task_id))
        return _verification(task_id)

    def fake_package(task_id: str, *, root: Path | None = None, log=None) -> Path:
        calls.append(("package", task_id))
        return tmp_path / "taskpacks" / task_id

    monkeypatch.setattr("forge.dashboard_live.fetch_from_config", fake_fetch)
    monkeypatch.setattr("forge.dashboard_live.verify_task", fake_verify)
    monkeypatch.setattr("forge.dashboard_live.package_task", fake_package)

    runner = DashboardControlRunner(tmp_path)
    job = runner.start_auto_run("tasks.yaml")

    assert job["mode"] == "auto"
    assert job["config_path"] == "tasks.yaml"
    assert runner.wait_for_current_job(timeout_seconds=2)

    snapshot = runner.snapshot()
    assert snapshot["job"]["status"] == "succeeded"
    assert calls == [
        ("fetch", "tasks.yaml"),
        ("verify", "one"),
        ("package", "one"),
        ("verify", "two"),
        ("package", "two"),
    ]
