from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DockerRunResult:
    command: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    error: str | None
    docker_build_success: bool
    image_tag: str | None
    duration_seconds: float


def generate_python_dockerfile() -> str:
    """Generate the default Python task Dockerfile."""

    return """FROM python:3.11-slim
WORKDIR /workspace
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY . /workspace
RUN python -m pip install --upgrade pip
RUN python -m pip install pytest
RUN if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; then \
      pip install -e .; \
    elif [ -f requirements.txt ]; then \
      pip install -r requirements.txt; \
    else \
      echo "No Python package metadata or requirements.txt found; skipping install"; \
    fi
"""


def write_python_dockerfile(path: Path) -> None:
    path.write_text(generate_python_dockerfile(), encoding="utf-8")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.").lower()
    return slug or "task"


def run_tests_in_docker(
    repo_path: Path,
    test_command: str,
    *,
    timeout_seconds: int,
    image_tag_prefix: str,
    dockerfile_path: Path | None = None,
) -> DockerRunResult:
    """Build a Docker image from repo_path and run the configured test command."""

    started_at = time.monotonic()
    if shutil.which("docker") is None:
        return DockerRunResult(
            command=test_command,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            error="Docker executable not found on PATH",
            docker_build_success=False,
            image_tag=None,
            duration_seconds=0.0,
        )

    if not repo_path.exists():
        return DockerRunResult(
            command=test_command,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            error=f"Repository path does not exist: {repo_path}",
            docker_build_success=False,
            image_tag=None,
            duration_seconds=0.0,
        )

    image_tag = f"{_slug(image_tag_prefix)}-{uuid.uuid4().hex[:12]}"
    container_name = f"{image_tag}-run"

    with tempfile.TemporaryDirectory(prefix="forge-docker-") as temp_dir:
        dockerfile = dockerfile_path or Path(temp_dir) / "Dockerfile"
        if dockerfile_path is None:
            write_python_dockerfile(dockerfile)

        build_command = ["docker", "build", "--file", str(dockerfile), "--tag", image_tag, str(repo_path)]
        try:
            build = subprocess.run(
                build_command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=max(120, timeout_seconds * 2),
            )
        except subprocess.TimeoutExpired:
            return DockerRunResult(
                command=test_command,
                exit_code=None,
                timed_out=True,
                stdout="",
                stderr="",
                error="Docker build timed out",
                docker_build_success=False,
                image_tag=image_tag,
                duration_seconds=time.monotonic() - started_at,
            )

        if build.returncode != 0:
            return DockerRunResult(
                command=test_command,
                exit_code=None,
                timed_out=False,
                stdout=build.stdout,
                stderr=build.stderr,
                error=f"Docker build failed with exit code {build.returncode}",
                docker_build_success=False,
                image_tag=image_tag,
                duration_seconds=time.monotonic() - started_at,
            )

        run_command = ["docker", "run", "--rm", "--name", container_name, image_tag, "sh", "-lc", test_command]
        try:
            run = subprocess.run(
                run_command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )
            return DockerRunResult(
                command=test_command,
                exit_code=run.returncode,
                timed_out=False,
                stdout=run.stdout,
                stderr=run.stderr,
                error=None,
                docker_build_success=True,
                image_tag=image_tag,
                duration_seconds=time.monotonic() - started_at,
            )
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", container_name], text=True, encoding="utf-8", errors="replace", capture_output=True)
            return DockerRunResult(
                command=test_command,
                exit_code=None,
                timed_out=True,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
                error=f"Test command timed out after {timeout_seconds} seconds",
                docker_build_success=True,
                image_tag=image_tag,
                duration_seconds=time.monotonic() - started_at,
            )
        finally:
            subprocess.run(["docker", "image", "rm", "-f", image_tag], text=True, encoding="utf-8", errors="replace", capture_output=True)