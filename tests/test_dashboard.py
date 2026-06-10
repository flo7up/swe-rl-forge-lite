from __future__ import annotations

from pathlib import Path

from forge.dashboard import collect_dashboard_tasks, render_dashboard_html, write_dashboard
from forge.task_schema import CommandRun, TaskMetadata, VerificationResult


def test_collect_dashboard_tasks_reads_local_artifacts(tmp_path: Path) -> None:
    task_id = "demo-001"
    task_dir = tmp_path / ".forge" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    metadata = TaskMetadata(
        id=task_id,
        repo_url="https://github.com/example/project.git",
        pr_number=3,
        repo_name="example/project",
        pr_title="Fix parser regression",
        pr_body="This fixes package metadata parsing.",
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
    taskpack_dir = tmp_path / "taskpacks" / task_id
    taskpack_dir.mkdir(parents=True)
    (taskpack_dir / "task.json").write_text("{}", encoding="utf-8")
    (taskpack_dir / "repo").mkdir()
    (taskpack_dir / "repo" / "setup.py").write_text("", encoding="utf-8")

    tasks = collect_dashboard_tasks(tmp_path)

    assert len(tasks) == 1
    assert tasks[0].recommended_status == "usable"
    assert tasks[0].lifecycle_stage == "packaged"
    assert tasks[0].pr_body == "This fixes package metadata parsing."
    assert tasks[0].repo_kind == "Python package"
    assert tasks[0].changed_files == ["x"]
    assert tasks[0].patch_additions == 0
    assert tasks[0].patch_deletions == 0
    assert tasks[0].patch_line_count == 1
    assert tasks[0].taskpack_path == f"taskpacks/{task_id}"
    assert tasks[0].taskpack_files == ["repo/", "task.json"]
    assert tasks[0].taskpack_repo_file_count == 1
    assert tasks[0].checks["test_environment_success"] is True
    assert tasks[0].checks["tests_pass_after_patch"] is True


def test_render_dashboard_html_contains_theme_and_data(tmp_path: Path) -> None:
    html = render_dashboard_html([])

    assert "clawpilotTheme" in html
    assert "--cp-bg: #f7f4ef;" in html
    assert "Local task forge observer" in html
    assert "test env" in html

    output = write_dashboard(tmp_path / "dashboard.html", root=tmp_path)
    assert output.exists()
    assert "dashboard-data" in output.read_text(encoding="utf-8")