from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from forge.config import load_config
from forge.task_schema import TaskMetadata


def test_yaml_config_parsing(tmp_path: Path) -> None:
    config_path = tmp_path / "tasks.yaml"
    config_path.write_text(
        """
tasks:
  - id: example-001
    repo_url: "https://github.com/example/project.git"
    pr_number: 12
    base_ref: null
    test_command: "pytest"
    language: "python"
    timeout_seconds: 30
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert len(config.tasks) == 1
    assert config.tasks[0].id == "example-001"
    assert config.tasks[0].timeout_seconds == 30


def test_task_schema_validation() -> None:
    metadata = TaskMetadata(
        id="task-001",
        repo_url="https://github.com/example/project.git",
        pr_number=1,
        repo_name="example/project",
        pr_title="Fix bug",
        pr_body="Adds a regression test.",
        base_commit="abcdef1234567890",
        head_commit="1234567890abcdef",
        test_command="pytest",
        language="Python",
        timeout_seconds=60,
    )

    assert metadata.language == "python"
    assert metadata.created_at.tzinfo is not None


def test_task_schema_rejects_blank_command() -> None:
    with pytest.raises(ValidationError):
        TaskMetadata(
            id="task-001",
            repo_url="https://github.com/example/project.git",
            pr_number=1,
            repo_name="example/project",
            pr_title="Fix bug",
            pr_body="",
            base_commit="abcdef1234567890",
            head_commit="1234567890abcdef",
            test_command="   ",
            language="python",
        )