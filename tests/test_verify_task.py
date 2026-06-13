"""Branch-matrix coverage for the verification core (forge.task_builder.verify_task).

verify_task is the project's "only source of truth", yet its collaborators (git,
Docker, patch apply) are all side-effecting. These tests inject fakes for those
collaborators and assert the derived verification booleans + recommended status
for each meaningful branch, so a regression that silently flips reward labels
fails a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge import task_builder
from forge.docker_runner import DockerRunResult
from forge.patch_utils import PatchResult
from forge.quality_report import recommend_status
from forge.task_builder import gold_patch_path, metadata_path, verification_path, verify_task
from forge.task_schema import TaskMetadata
from forge.test_report import REPORT_END, REPORT_START


def _junit(cases: dict[str, str]) -> str:
    parts = []
    for key, status in cases.items():
        classname, name = key.split("::")
        body = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>", "error": "<error/>"}[status]
        parts.append(f'<testcase classname="{classname}" name="{name}">{body}</testcase>')
    return "<testsuites><testsuite>" + "".join(parts) + "</testsuite></testsuites>"


def _docker_report(exit_code: int, cases: dict[str, str], *, build: bool = True) -> DockerRunResult:
    stdout = f"collected\n{REPORT_START}\n{_junit(cases)}\n{REPORT_END}\n"
    return DockerRunResult(
        command="pytest", exit_code=exit_code, timed_out=False, stdout=stdout, stderr="",
        error=None, docker_build_success=build, image_tag="img", duration_seconds=1.0,
    )


def _metadata(task_id: str = "ver-001") -> TaskMetadata:
    return TaskMetadata(
        id=task_id,
        repo_url="https://github.com/example/project.git",
        pr_number=7,
        repo_name="example/project",
        pr_title="Fix regression",
        pr_body="body",
        base_commit="a" * 40,
        head_commit="b" * 40,
        test_command="pytest",
        language="python",
        timeout_seconds=120,
    )


def _docker(exit_code: int | None, *, build: bool = True, error: str | None = None, timed_out: bool = False) -> DockerRunResult:
    return DockerRunResult(
        command="pytest",
        exit_code=exit_code,
        timed_out=timed_out,
        stdout="",
        stderr="",
        error=error,
        docker_build_success=build,
        image_tag="img-1234",
        duration_seconds=1.0,
    )


def _setup_task(tmp_path: Path, task_id: str = "ver-001") -> TaskMetadata:
    metadata = _metadata(task_id)
    metadata.write_json(metadata_path(tmp_path, task_id))
    gold_patch_path(tmp_path, task_id).write_bytes(b"diff --git a/x b/x\n")
    return metadata


def _patch_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_found: bool = True,
    base_raises: bool = False,
    patch_ok: bool = True,
    apply_ok: bool = True,
    runner=None,
) -> None:
    def ensure(*_args, **_kwargs):
        if base_raises:
            raise RuntimeError("commit unavailable")

    monkeypatch.setattr(task_builder, "ensure_commit_available", ensure)
    monkeypatch.setattr(task_builder, "repo_has_commit", lambda *a, **k: base_found)
    monkeypatch.setattr(task_builder, "checkout_clean", lambda *a, **k: None)
    monkeypatch.setattr(task_builder, "check_patch", lambda *a, **k: PatchResult(success=patch_ok, stdout="", stderr="" if patch_ok else "does not apply"))
    monkeypatch.setattr(task_builder, "apply_patch_file", lambda *a, **k: PatchResult(success=apply_ok, stdout="", stderr="" if apply_ok else "apply failed"))
    if runner is None:
        runner = lambda *a, **k: _docker(0)  # noqa: E731 - test stub
    monkeypatch.setattr(task_builder, "run_tests_in_docker", runner)


def _phase_runner(results: dict[str, DockerRunResult]):
    """Dispatch run_tests_in_docker results by the phase encoded in image_tag_prefix."""

    def runner(repo_path, test_command, *, timeout_seconds, image_tag_prefix, dockerfile_path=None, exec_command=None):
        phase = image_tag_prefix.rsplit("-", 1)[-1]
        return results[phase]

    return runner


def test_verify_happy_path_is_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    _patch_collaborators(
        monkeypatch,
        runner=_phase_runner({"before": _docker(1), "after": _docker(0), "rerun": _docker(0)}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.base_commit_found is True
    assert result.patch_applies is True
    assert result.tests_fail_before_patch is True
    assert result.tests_pass_after_patch is True
    assert result.deterministic_rerun_success is True
    assert result.docker_build_success is True
    assert result.test_environment_success is True
    assert result.errors == []
    assert recommend_status(result) == "usable"
    assert verification_path(tmp_path, "ver-001").exists()


def test_verify_base_commit_missing_is_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)

    def runner(*_a, **_k):
        raise AssertionError("docker must not run when the base commit is missing")

    _patch_collaborators(monkeypatch, base_raises=True, base_found=False, runner=runner)

    result = verify_task("ver-001", root=tmp_path)

    assert result.base_commit_found is False
    assert result.before_patch is None
    assert result.after_patch is None
    assert result.deterministic_rerun is None
    assert result.docker_build_success is False
    assert result.tests_fail_before_patch is False
    assert result.tests_pass_after_patch is False
    assert any("Base commit not available" in e for e in result.errors)
    assert recommend_status(result) == "invalid"


def test_verify_patch_does_not_apply_runs_before_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    _patch_collaborators(
        monkeypatch,
        patch_ok=False,
        runner=_phase_runner({"before": _docker(1)}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.patch_applies is False
    assert result.before_patch is not None
    assert result.after_patch is None
    assert result.deterministic_rerun is None
    assert result.tests_pass_after_patch is False
    assert any("Patch did not apply cleanly" in e for e in result.errors)
    assert recommend_status(result) == "invalid"


def test_verify_infrastructure_failure_zeroes_test_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    # exit 127 == "command not found" -> infrastructure failure, not a product signal.
    _patch_collaborators(
        monkeypatch,
        runner=_phase_runner({"before": _docker(127), "after": _docker(0), "rerun": _docker(0)}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.test_environment_success is False
    assert result.tests_fail_before_patch is False
    assert result.tests_pass_after_patch is False
    assert result.deterministic_rerun_success is False
    assert any("infrastructure failure" in e.lower() for e in result.errors)
    assert recommend_status(result) == "invalid"


def test_verify_nondeterministic_rerun_is_needs_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    _patch_collaborators(
        monkeypatch,
        runner=_phase_runner({"before": _docker(1), "after": _docker(0), "rerun": _docker(1)}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.tests_fail_before_patch is True
    assert result.tests_pass_after_patch is True
    assert result.deterministic_rerun_success is False
    assert result.docker_build_success is True
    assert result.test_environment_success is True
    assert recommend_status(result) == "needs_review"


def test_verify_derives_fail_to_pass_and_pass_to_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)  # default test_command is "pytest" -> reports are collected
    before = _docker_report(1, {"pkg::test_keep": "passed", "pkg::test_fix": "failed"})
    after = _docker_report(0, {"pkg::test_keep": "passed", "pkg::test_fix": "passed"})
    rerun = _docker_report(0, {"pkg::test_keep": "passed", "pkg::test_fix": "passed"})
    _patch_collaborators(monkeypatch, runner=_phase_runner({"before": before, "after": after, "rerun": rerun}))

    result = verify_task("ver-001", root=tmp_path)

    assert result.fail_to_pass == ["pkg::test_fix"]
    assert result.pass_to_pass == ["pkg::test_keep"]
    assert recommend_status(result) == "usable"


def test_verify_leaves_targeted_sets_empty_without_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    _patch_collaborators(
        monkeypatch,
        runner=_phase_runner({"before": _docker(1), "after": _docker(0), "rerun": _docker(0)}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.fail_to_pass == []
    assert result.pass_to_pass == []


def test_verify_docker_build_failure_is_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_task(tmp_path)
    failed = _docker(None, build=False, error="Docker build failed with exit code 1")
    _patch_collaborators(
        monkeypatch,
        runner=_phase_runner({"before": failed, "after": failed, "rerun": failed}),
    )

    result = verify_task("ver-001", root=tmp_path)

    assert result.docker_build_success is False
    assert result.test_environment_success is False
    assert result.tests_fail_before_patch is False
    assert result.tests_pass_after_patch is False
    assert recommend_status(result) == "invalid"
