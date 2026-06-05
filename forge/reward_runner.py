from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RewardResult(BaseModel):
    """Binary reward payload returned by reward execution."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    tests_passed: bool
    error: str | None = None


def format_reward(exit_code: int | None, error: str | None = None) -> RewardResult:
    tests_passed = exit_code == 0 and error is None
    return RewardResult(
        score=1.0 if tests_passed else 0.0,
        tests_passed=tests_passed,
        error=None if tests_passed else error or f"Test command exited with code {exit_code}",
    )


def run_reward_script(taskpack_path: Path) -> RewardResult:
    """Run a taskpack's standalone reward.py and parse its JSON output."""

    taskpack_path = taskpack_path.resolve()
    reward_script = taskpack_path / "reward.py"
    if not reward_script.exists():
        return RewardResult(score=0.0, tests_passed=False, error=f"Missing reward script: {reward_script}")

    try:
        completed = subprocess.run(
            [sys.executable, str(reward_script)],
            cwd=taskpack_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=None,
        )
    except OSError as exc:
        return RewardResult(score=0.0, tests_passed=False, error=f"Could not run reward script: {exc}")

    stdout = completed.stdout.strip()
    if not stdout:
        error = completed.stderr.strip() or f"Reward script exited with code {completed.returncode} and no JSON output"
        return RewardResult(score=0.0, tests_passed=False, error=error)

    last_line = stdout.splitlines()[-1]
    try:
        payload = json.loads(last_line)
        return RewardResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        return RewardResult(score=0.0, tests_passed=False, error=f"Invalid reward JSON: {exc}")