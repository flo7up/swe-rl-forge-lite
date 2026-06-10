from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from forge.config import ForgeConfig, TaskConfig, load_config


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_task_config_normalizes_language_and_strips_command() -> None:
    task = TaskConfig(
        id="task-001",
        repo_url="https://github.com/example/project.git",
        pr_number=7,
        test_command="  pytest -q  ",
        language="  Python  ",
    )

    assert task.language == "python"
    assert task.test_command == "pytest -q"
    assert task.timeout_seconds == 300


def test_task_config_rejects_non_github_repo() -> None:
    with pytest.raises(ValidationError):
        TaskConfig(
            id="task-001",
            repo_url="https://gitlab.com/example/project.git",
            pr_number=1,
            test_command="pytest",
        )


def test_task_config_rejects_blank_test_command() -> None:
    with pytest.raises(ValidationError):
        TaskConfig(
            id="task-001",
            repo_url="https://github.com/example/project.git",
            pr_number=1,
            test_command="   ",
        )


def test_task_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskConfig(
            id="task-001",
            repo_url="https://github.com/example/project.git",
            pr_number=1,
            test_command="pytest",
            unexpected="nope",
        )


def test_forge_config_rejects_duplicate_task_ids() -> None:
    task_kwargs = {
        "repo_url": "https://github.com/example/project.git",
        "pr_number": 1,
        "test_command": "pytest",
    }
    with pytest.raises(ValidationError):
        ForgeConfig(
            tasks=[
                TaskConfig(id="dup", **task_kwargs),
                TaskConfig(id="dup", **task_kwargs),
            ]
        )


def test_forge_config_requires_at_least_one_task() -> None:
    with pytest.raises(ValidationError):
        ForgeConfig(tasks=[])


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_load_config_empty_file(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "empty.yaml", "")
    with pytest.raises(ValueError):
        load_config(config_path)


def test_load_config_top_level_must_be_mapping(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "list.yaml", "- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_config(config_path)


def test_load_config_parses_valid_file(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "tasks.yaml",
        """
tasks:
  - id: example-001
    repo_url: "https://github.com/example/project.git"
    pr_number: 12
    test_command: "pytest"
""",
    )

    config = load_config(config_path)

    assert len(config.tasks) == 1
    assert config.tasks[0].id == "example-001"
    assert config.tasks[0].language == "python"
