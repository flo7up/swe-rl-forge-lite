from __future__ import annotations

import subprocess
from pathlib import Path


class GitCommandError(RuntimeError):
    """Raised when a git command fails."""

    def __init__(self, command: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"git command failed ({returncode}): {' '.join(command)}\n{stderr.strip()}")


def run_git(repo_path: Path, args: list[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command in a repository."""

    command = ["git", *args]
    completed = subprocess.run(
        command,
        cwd=repo_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise GitCommandError(command, completed.returncode, completed.stdout, completed.stderr)
    return completed


def clone_or_update(repo_url: str, repo_path: Path) -> None:
    """Clone a repository, or update it if it already exists."""

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    if (repo_path / ".git").exists():
        run_git(repo_path, ["fetch", "--all", "--prune", "--tags"], timeout=300)
        return

    if repo_path.exists() and any(repo_path.iterdir()):
        raise GitCommandError(["git", "clone", repo_url, str(repo_path)], 1, "", f"Target path is not empty: {repo_path}")

    completed = subprocess.run(
        ["git", "clone", repo_url, str(repo_path)],
        cwd=repo_path.parent,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise GitCommandError(["git", "clone", repo_url, str(repo_path)], completed.returncode, completed.stdout, completed.stderr)


def repo_has_commit(repo_path: Path, commit: str) -> bool:
    completed = run_git(repo_path, ["cat-file", "-e", f"{commit}^{{commit}}"], check=False)
    return completed.returncode == 0


def ensure_commit_available(repo_path: Path, commit: str) -> None:
    """Ensure a commit object is available locally, fetching by SHA if needed."""

    if repo_has_commit(repo_path, commit):
        return
    run_git(repo_path, ["fetch", "origin", commit], timeout=300)
    if not repo_has_commit(repo_path, commit):
        raise GitCommandError(["git", "cat-file", "-e", commit], 1, "", f"Commit not found after fetch: {commit}")


def fetch_ref(repo_path: Path, ref: str) -> None:
    run_git(repo_path, ["fetch", "origin", ref], timeout=300)


def resolve_ref(repo_path: Path, ref: str) -> str:
    completed = run_git(repo_path, ["rev-parse", f"{ref}^{{commit}}"])
    return completed.stdout.strip()


def checkout_clean(repo_path: Path, commit: str) -> None:
    """Check out a commit and remove untracked files in the managed clone."""

    ensure_commit_available(repo_path, commit)
    run_git(repo_path, ["checkout", "--force", commit], timeout=300)
    run_git(repo_path, ["reset", "--hard", commit], timeout=300)
    run_git(repo_path, ["clean", "-fdx"], timeout=300)