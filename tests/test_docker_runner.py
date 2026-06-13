from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from forge import docker_runner
from forge.docker_runner import (
    CONTAINER_RUN_FLAGS,
    _without_repo_dockerignore,
    generate_python_dockerfile,
    run_tests_in_docker,
)


def test_generated_python_dockerfile_installs_pytest() -> None:
    dockerfile = generate_python_dockerfile()

    assert "python -m pip install pytest" in dockerfile


def test_run_flags_isolate_network_and_privileges() -> None:
    assert "--network" in CONTAINER_RUN_FLAGS
    assert CONTAINER_RUN_FLAGS[CONTAINER_RUN_FLAGS.index("--network") + 1] == "none"
    assert "no-new-privileges" in CONTAINER_RUN_FLAGS


def test_without_repo_dockerignore_hides_then_restores(tmp_path: Path) -> None:
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text("tests/\n", encoding="utf-8")

    with _without_repo_dockerignore(tmp_path):
        assert not dockerignore.exists()
        assert (tmp_path / ".dockerignore.forge-disabled").exists()

    assert dockerignore.read_text(encoding="utf-8") == "tests/\n"
    assert not (tmp_path / ".dockerignore.forge-disabled").exists()


def test_without_repo_dockerignore_is_noop_when_absent(tmp_path: Path) -> None:
    with _without_repo_dockerignore(tmp_path):
        assert not (tmp_path / ".dockerignore.forge-disabled").exists()


class _FakeDocker:
    """Stand-in for subprocess.run that classifies docker build/run/cleanup calls."""

    def __init__(self, *, build_rc: int = 0, run_rc: int = 0, build_timeout: bool = False, run_timeout: bool = False) -> None:
        self.build_rc = build_rc
        self.run_rc = run_rc
        self.build_timeout = build_timeout
        self.run_timeout = run_timeout
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "build":
            if self.build_timeout:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return SimpleNamespace(returncode=self.build_rc, stdout="build-out", stderr="build-err")
        if sub == "run":
            if self.run_timeout:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return SimpleNamespace(returncode=self.run_rc, stdout="run-out", stderr="run-err")
        # image rm / container rm cleanup commands
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def docker_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda name: "/usr/bin/docker")


def test_reports_missing_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker_runner.shutil, "which", lambda name: None)

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.docker_build_success is False
    assert result.exit_code is None
    assert "Docker executable not found" in (result.error or "")


def test_reports_missing_repo(docker_present, tmp_path: Path) -> None:
    result = run_tests_in_docker(tmp_path / "nope", "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.docker_build_success is False
    assert "does not exist" in (result.error or "")


def test_build_failure_is_reported_and_run_skipped(docker_present, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeDocker(build_rc=1)
    monkeypatch.setattr(docker_runner.subprocess, "run", fake)

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.docker_build_success is False
    assert "Docker build failed" in (result.error or "")
    assert not any(cmd[1] == "run" for cmd in fake.calls)


def test_build_timeout_is_reported(docker_present, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker_runner.subprocess, "run", _FakeDocker(build_timeout=True))

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.timed_out is True
    assert result.docker_build_success is False
    assert "build timed out" in (result.error or "").lower()


def test_successful_run_records_exit_code_and_isolation_flags(docker_present, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeDocker(run_rc=0)
    monkeypatch.setattr(docker_runner.subprocess, "run", fake)

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.docker_build_success is True
    assert result.exit_code == 0
    assert result.error is None
    run_cmd = next(cmd for cmd in fake.calls if cmd[1] == "run")
    assert "--network" in run_cmd and "none" in run_cmd
    assert "no-new-privileges" in run_cmd


def test_failing_run_is_product_signal_not_infra(docker_present, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(docker_runner.subprocess, "run", _FakeDocker(run_rc=1))

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=10, image_tag_prefix="t")

    assert result.docker_build_success is True
    assert result.exit_code == 1
    assert result.error is None


def test_run_timeout_is_reported_and_build_marked_success(docker_present, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeDocker(run_timeout=True)
    monkeypatch.setattr(docker_runner.subprocess, "run", fake)

    result = run_tests_in_docker(tmp_path, "pytest", timeout_seconds=5, image_tag_prefix="t")

    assert result.timed_out is True
    assert result.docker_build_success is True
    assert "timed out after 5 seconds" in (result.error or "")
    # the run-timeout path force-removes the container
    assert any(cmd[:3] == ["docker", "rm", "-f"] for cmd in fake.calls)
