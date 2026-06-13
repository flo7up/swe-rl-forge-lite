from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskMetadata(BaseModel):
    """Normalized metadata for a forged SWE task."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    repo_url: str = Field(min_length=1)
    pr_number: int = Field(gt=0)
    repo_name: str = Field(min_length=1)
    pr_title: str
    pr_body: str
    base_commit: str = Field(min_length=7)
    head_commit: str = Field(min_length=7)
    test_command: str = Field(min_length=1)
    language: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("test_command")
    @classmethod
    def command_must_not_be_blank(cls, value: str) -> str:
        command = value.strip()
        if not command:
            raise ValueError("test_command must not be blank")
        return command

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def read_json(cls, path: Path) -> "TaskMetadata":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class CommandRun(BaseModel):
    """Captured command execution details for verification."""

    model_config = ConfigDict(extra="forbid")

    phase: str
    command: str
    exit_code: int | None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    docker_build_success: bool = False
    docker_image: str | None = None
    duration_seconds: float = 0.0


def detect_test_infrastructure_failure(run: CommandRun) -> str | None:
    output = f"{run.stderr}\n{run.stdout}\n{run.error or ''}".lower()
    if not run.docker_build_success:
        return run.error or "Docker image build failed"
    if run.exit_code == 127 or "command not found" in output:
        return f"test command could not be found: {run.command}"
    if "not recognized as an internal or external command" in output:
        return f"test command could not be found: {run.command}"
    if "no module named pytest" in output or "no module named 'pytest'" in output:
        return "pytest is not installed in the test environment"
    return None


class VerificationResult(BaseModel):
    """Quality and verification result for a forged task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    base_commit_found: bool = False
    patch_applies: bool = False
    tests_fail_before_patch: bool = False
    tests_pass_after_patch: bool = False
    docker_build_success: bool = False
    test_environment_success: bool = True
    deterministic_rerun_success: bool = False
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    before_patch: CommandRun | None = None
    after_patch: CommandRun | None = None
    deterministic_rerun: CommandRun | None = None
    errors: list[str] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def derive_test_environment_status(self) -> "VerificationResult":
        runs = [run for run in (self.before_patch, self.after_patch, self.deterministic_rerun) if run is not None]
        infrastructure_failures = [
            f"Test infrastructure failure: {run.phase}: {reason}"
            for run in runs
            if (reason := detect_test_infrastructure_failure(run)) is not None
        ]
        if infrastructure_failures:
            self.test_environment_success = False
            self.tests_fail_before_patch = False
            self.tests_pass_after_patch = False
            self.deterministic_rerun_success = False
            for failure in infrastructure_failures:
                if failure not in self.errors:
                    self.errors.append(failure)
        return self

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def read_json(cls, path: Path) -> "VerificationResult":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))