from __future__ import annotations

import json
from pathlib import Path

from forge.quality_report import build_quality_report
from forge.task_schema import TaskMetadata


def test_quality_report_reclassifies_legacy_command_not_found_failure(tmp_path: Path) -> None:
    task_id = "demo-001"
    task_dir = tmp_path / ".forge" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    metadata = TaskMetadata(
        id=task_id,
        repo_url="https://github.com/example/project.git",
        pr_number=3,
        repo_name="example/project",
        pr_title="Fix parser regression",
        pr_body="",
        base_commit="abcdef123456",
        head_commit="123456abcdef",
        test_command="pytest",
        language="python",
    )
    metadata.write_json(task_dir / "metadata.json")
    (task_dir / "verification.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "base_commit_found": True,
                "patch_applies": True,
                "tests_fail_before_patch": True,
                "tests_pass_after_patch": False,
                "docker_build_success": True,
                "deterministic_rerun_success": False,
                "before_patch": {
                    "phase": "before_patch",
                    "command": "pytest",
                    "exit_code": 127,
                    "stderr": "sh: 1: pytest: not found\n",
                    "docker_build_success": True,
                },
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    report = build_quality_report(task_id, root=tmp_path)

    assert report["tests_fail_before_patch"] is False
    assert report["test_environment_status"] is False
    assert report["recommended_status"] == "invalid"
    assert report["errors"] == ["Test infrastructure failure: before_patch: test command could not be found: pytest"]