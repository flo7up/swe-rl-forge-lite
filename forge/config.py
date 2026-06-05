from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator


class TaskConfig(BaseModel):
    """User-provided task configuration for a GitHub pull request."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    repo_url: str = Field(min_length=1)
    pr_number: PositiveInt
    base_ref: str | None = None
    test_command: str = Field(min_length=1)
    language: str = Field(default="python", min_length=1)
    timeout_seconds: int = Field(default=300, ge=1)

    @field_validator("repo_url")
    @classmethod
    def repo_url_must_be_github(cls, value: str) -> str:
        if "github.com" not in value:
            raise ValueError("repo_url must point to a GitHub repository")
        return value

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("test_command")
    @classmethod
    def test_command_must_not_be_blank(cls, value: str) -> str:
        command = value.strip()
        if not command:
            raise ValueError("test_command must not be blank")
        return command


class ForgeConfig(BaseModel):
    """Top-level YAML configuration."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskConfig] = Field(min_length=1)

    @field_validator("tasks")
    @classmethod
    def task_ids_must_be_unique(cls, value: list[TaskConfig]) -> list[TaskConfig]:
        ids = [task.id for task in value]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        return value


def load_config(path: Path) -> ForgeConfig:
    """Load and validate a forge YAML config file."""

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Config file is empty: {path}")
    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping at the top level")
    return ForgeConfig.model_validate(raw)