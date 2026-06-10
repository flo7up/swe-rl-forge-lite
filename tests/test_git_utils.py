from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.git_utils import (
    GitCommandError,
    checkout_clean,
    repo_has_commit,
    resolve_ref,
    run_git,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git executable not available")


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, ["init", "--quiet"])
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(path, ["add", "README.md"])
    run_git(
        path,
        [
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test User",
            "commit",
            "--quiet",
            "-m",
            "initial commit",
        ],
    )
    return resolve_ref(path, "HEAD")


def test_git_command_error_message_includes_command_and_code() -> None:
    error = GitCommandError(["git", "status"], 128, "out", "boom\n")

    assert error.returncode == 128
    assert "git status" in str(error)
    assert "boom" in str(error)


def test_run_git_check_false_returns_nonzero_without_raising(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    completed = run_git(tmp_path, ["rev-parse", "does-not-exist"], check=False)

    assert completed.returncode != 0


def test_run_git_check_true_raises_on_failure(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    with pytest.raises(GitCommandError):
        run_git(tmp_path, ["rev-parse", "does-not-exist"])


def test_repo_has_commit_distinguishes_known_and_unknown(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)

    assert repo_has_commit(tmp_path, head) is True
    assert repo_has_commit(tmp_path, "0" * 40) is False


def test_resolve_ref_returns_full_sha(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)

    resolved = resolve_ref(tmp_path, "HEAD")

    assert resolved == head
    assert len(resolved) == 40


def test_checkout_clean_removes_untracked_files(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    untracked = tmp_path / "scratch.txt"
    untracked.write_text("temp\n", encoding="utf-8")

    checkout_clean(tmp_path, head)

    assert not untracked.exists()
