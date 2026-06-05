from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatchResult:
    success: bool
    stdout: str
    stderr: str


def _git_apply(repo_path: Path, patch_path: Path, *, check_only: bool) -> PatchResult:
    args = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    args.append(str(patch_path))
    completed = subprocess.run(
        args,
        cwd=repo_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
    )
    return PatchResult(
        success=completed.returncode == 0,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def check_patch(repo_path: Path, patch_path: Path) -> PatchResult:
    """Return detailed information about whether a patch applies."""

    return _git_apply(repo_path, patch_path, check_only=True)


def check_patch_applies(repo_path: Path, patch_path: Path) -> bool:
    """Return True when git can apply the patch cleanly."""

    return check_patch(repo_path, patch_path).success


def apply_patch_file(repo_path: Path, patch_path: Path) -> PatchResult:
    """Apply a patch to a repository working tree."""

    return _git_apply(repo_path, patch_path, check_only=False)