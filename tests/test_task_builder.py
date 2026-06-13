from __future__ import annotations

from pathlib import Path

import pytest

from forge import task_builder
from forge.docker_runner import DockerRunResult
from forge.task_builder import (
    _command_run,
    _prompt,
    gold_patch_path,
    load_metadata,
    metadata_path,
    package_task,
    verification_path,
    write_gold_patch,
)
from forge.task_schema import TaskMetadata


def _metadata(task_id: str = "demo-001") -> TaskMetadata:
    return TaskMetadata(
        id=task_id,
        repo_url="https://github.com/example/project.git",
        pr_number=42,
        repo_name="example/project",
        pr_title="Fix parser regression",
        pr_body="Restores handling of empty inputs.",
        base_commit="a" * 40,
        head_commit="b" * 40,
        test_command="pytest -q",
        language="python",
        timeout_seconds=120,
    )


def test_path_helpers_are_rooted(tmp_path: Path) -> None:
    assert metadata_path(tmp_path, "t1") == tmp_path / ".forge" / "tasks" / "t1" / "metadata.json"
    assert gold_patch_path(tmp_path, "t1") == tmp_path / ".forge" / "tasks" / "t1" / "gold.patch"
    assert verification_path(tmp_path, "t1") == tmp_path / ".forge" / "tasks" / "t1" / "verification.json"


def test_load_metadata_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_metadata(tmp_path, "missing")


def test_load_metadata_roundtrip(tmp_path: Path) -> None:
    metadata = _metadata()
    path = metadata_path(tmp_path, metadata.id)
    path.parent.mkdir(parents=True)
    metadata.write_json(path)

    loaded = load_metadata(tmp_path, metadata.id)

    assert loaded.id == metadata.id
    assert loaded.test_command == "pytest -q"


def test_prompt_includes_pr_title_and_test_command() -> None:
    prompt = _prompt(_metadata())

    assert "Fix parser regression" in prompt
    assert "pytest -q" in prompt
    assert "example/project" in prompt


def test_prompt_handles_blank_body() -> None:
    metadata = _metadata()
    metadata.pr_body = "   "

    prompt = _prompt(metadata)

    assert "No pull request body was provided." in prompt


def test_command_run_maps_docker_result_fields() -> None:
    result = DockerRunResult(
        command="pytest",
        exit_code=1,
        timed_out=False,
        stdout="out",
        stderr="err",
        error=None,
        docker_build_success=True,
        image_tag="forge-demo-before:latest",
        duration_seconds=3.5,
    )

    run = _command_run("before_patch", result)

    assert run.phase == "before_patch"
    assert run.exit_code == 1
    assert run.docker_build_success is True
    assert run.docker_image == "forge-demo-before:latest"
    assert run.duration_seconds == 3.5


def test_package_task_requires_gold_patch(tmp_path: Path) -> None:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))

    with pytest.raises(FileNotFoundError):
        package_task(metadata.id, root=tmp_path)


def test_package_task_requires_verification(tmp_path: Path) -> None:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))
    gold_patch_path(tmp_path, metadata.id).write_text("diff --git a b\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        package_task(metadata.id, root=tmp_path)


def test_package_task_builds_taskpack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))
    gold_patch_path(tmp_path, metadata.id).write_text("diff --git a b\n", encoding="utf-8")
    verification_path(tmp_path, metadata.id).write_text("{\"task_id\": \"demo-001\"}", encoding="utf-8")

    repo_dir = tmp_path / ".forge" / "repos" / metadata.id
    repo_dir.mkdir(parents=True)
    (repo_dir / "module.py").write_text("print('hi')\n", encoding="utf-8")
    (repo_dir / ".git").mkdir()
    (repo_dir / ".git" / "config").write_text("ignore me\n", encoding="utf-8")

    monkeypatch.setattr(task_builder, "checkout_clean", lambda *args, **kwargs: None)

    package_dir = package_task(metadata.id, root=tmp_path)

    assert (package_dir / "task.json").exists()
    assert (package_dir / "prompt.md").exists()
    assert (package_dir / "Dockerfile").exists()
    assert (package_dir / "reward.py").exists()
    assert (package_dir / "gold.patch").exists()
    assert (package_dir / "verification.json").exists()
    assert (package_dir / "repo" / "module.py").exists()
    assert not (package_dir / "repo" / ".git").exists()


def test_write_gold_patch_preserves_lf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "gold.patch"

    write_gold_patch(path, "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+line\n")

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"+line\n")


def test_package_task_strips_upstream_dockerignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))
    gold_patch_path(tmp_path, metadata.id).write_text("diff --git a b\n", encoding="utf-8")
    verification_path(tmp_path, metadata.id).write_text("{}", encoding="utf-8")

    repo_dir = tmp_path / ".forge" / "repos" / metadata.id
    repo_dir.mkdir(parents=True)
    (repo_dir / "module.py").write_text("value = 1\n", encoding="utf-8")
    (repo_dir / ".dockerignore").write_text("tests/\n", encoding="utf-8")

    monkeypatch.setattr(task_builder, "checkout_clean", lambda *args, **kwargs: None)

    package_dir = package_task(metadata.id, root=tmp_path)

    assert (package_dir / "repo" / "module.py").exists()
    assert not (package_dir / "repo" / ".dockerignore").exists()


def test_package_task_overwrites_existing_taskpack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))
    gold_patch_path(tmp_path, metadata.id).write_text("diff --git a b\n", encoding="utf-8")
    verification_path(tmp_path, metadata.id).write_text("{}", encoding="utf-8")

    repo_dir = tmp_path / ".forge" / "repos" / metadata.id
    repo_dir.mkdir(parents=True)
    (repo_dir / "module.py").write_text("print('hi')\n", encoding="utf-8")

    package_dir = tmp_path / "taskpacks" / metadata.id
    package_dir.mkdir(parents=True)
    stale = package_dir / "stale.txt"
    stale.write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(task_builder, "checkout_clean", lambda *args, **kwargs: None)

    package_task(metadata.id, root=tmp_path)

    assert not stale.exists()
