from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from forge.config import TaskConfig, load_config
from forge.docker_runner import DockerRunResult, generate_python_dockerfile, run_tests_in_docker
from forge.git_utils import GitCommandError, checkout_clean, clone_or_update, ensure_commit_available, fetch_ref, repo_has_commit, resolve_ref
from forge.github_pr import fetch_pr_diff, fetch_pull_request
from forge.patch_utils import apply_patch_file, check_patch
from forge.task_schema import CommandRun, TaskMetadata, VerificationResult, detect_test_infrastructure_failure
from forge.test_report import derive_test_deltas, extract_report, parse_junit_xml, wrap_pytest_command


FORGE_DIR = Path(".forge")
REPOS_DIR = FORGE_DIR / "repos"
TASKS_DIR = FORGE_DIR / "tasks"
TASKPACKS_DIR = Path("taskpacks")

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    return None


def write_gold_patch(path: Path, diff: str) -> None:
    """Persist a unified diff verbatim as UTF-8 bytes.

    ``Path.write_text`` uses text mode, which on Windows rewrites every ``\\n`` to
    ``\\r\\n``. ``git apply --check`` then rejects the otherwise-valid patch, so a
    correct historical fix is silently recorded as non-applying. Writing bytes
    keeps the diff's original line endings intact on every platform.
    """

    path.write_bytes(diff.encode("utf-8"))


def _repo_dir(root: Path, task_id: str) -> Path:
    return root / REPOS_DIR / task_id


def _task_dir(root: Path, task_id: str) -> Path:
    return root / TASKS_DIR / task_id


def _taskpack_dir(root: Path, task_id: str) -> Path:
    return root / TASKPACKS_DIR / task_id


def metadata_path(root: Path, task_id: str) -> Path:
    return _task_dir(root, task_id) / "metadata.json"


def gold_patch_path(root: Path, task_id: str) -> Path:
    return _task_dir(root, task_id) / "gold.patch"


def verification_path(root: Path, task_id: str) -> Path:
    return _task_dir(root, task_id) / "verification.json"


def load_metadata(root: Path, task_id: str) -> TaskMetadata:
    path = metadata_path(root, task_id)
    if not path.exists():
        raise FileNotFoundError(f"Task metadata not found. Run `forge fetch` first: {path}")
    return TaskMetadata.read_json(path)


def fetch_from_config(config_path: Path, *, root: Path | None = None, log: LogFn = _noop_log) -> list[TaskMetadata]:
    root = root or Path.cwd()
    config = load_config(config_path)
    return [fetch_task(task, root=root, log=log) for task in config.tasks]


def fetch_task(task: TaskConfig, *, root: Path | None = None, log: LogFn = _noop_log) -> TaskMetadata:
    root = root or Path.cwd()
    repo_dir = _repo_dir(root, task.id)
    task_dir = _task_dir(root, task.id)
    task_dir.mkdir(parents=True, exist_ok=True)

    log(f"Cloning or updating {task.repo_url} into {repo_dir}")
    clone_or_update(task.repo_url, repo_dir)

    log(f"Fetching GitHub PR metadata for #{task.pr_number}")
    pr = fetch_pull_request(task.repo_url, task.pr_number)

    if task.base_ref:
        log(f"Resolving configured base_ref {task.base_ref}")
        try:
            fetch_ref(repo_dir, task.base_ref)
        except GitCommandError:
            pass
        base_commit = resolve_ref(repo_dir, task.base_ref)
    else:
        base_commit = pr.base_commit

    ensure_commit_available(repo_dir, base_commit)

    log("Downloading PR diff")
    diff = fetch_pr_diff(task.repo_url, task.pr_number)
    write_gold_patch(gold_patch_path(root, task.id), diff)

    metadata = TaskMetadata(
        id=task.id,
        repo_url=task.repo_url,
        pr_number=task.pr_number,
        repo_name=pr.repo_name,
        pr_title=pr.title,
        pr_body=pr.body,
        base_commit=base_commit,
        head_commit=pr.head_commit,
        test_command=task.test_command,
        language=task.language,
        timeout_seconds=task.timeout_seconds,
    )
    metadata.write_json(metadata_path(root, task.id))
    return metadata


def _command_run(phase: str, result: DockerRunResult) -> CommandRun:
    return CommandRun(
        phase=phase,
        command=result.command,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
        docker_build_success=result.docker_build_success,
        docker_image=result.image_tag,
        duration_seconds=result.duration_seconds,
    )


def verify_task(task_id: str, *, root: Path | None = None, log: LogFn = _noop_log) -> VerificationResult:
    root = root or Path.cwd()
    metadata = load_metadata(root, task_id)
    repo_dir = _repo_dir(root, task_id)
    patch_path = gold_patch_path(root, task_id)
    errors: list[str] = []

    base_commit_found = False
    patch_applies = False
    before_patch: CommandRun | None = None
    after_patch: CommandRun | None = None
    deterministic_rerun: CommandRun | None = None
    before_results: dict[str, str] = {}
    after_results: dict[str, str] = {}

    # Wrap pytest commands to emit a per-test JUnit report; None for non-pytest
    # commands, which fall back to whole-suite exit-code scoring.
    exec_command = wrap_pytest_command(metadata.test_command)

    try:
        ensure_commit_available(repo_dir, metadata.base_commit)
        base_commit_found = repo_has_commit(repo_dir, metadata.base_commit)
    except Exception as exc:  # noqa: BLE001 - verification records operational failures.
        errors.append(f"Base commit not available: {exc}")

    if not patch_path.exists():
        errors.append(f"Gold patch not found: {patch_path}")

    try:
        if base_commit_found:
            log(f"Checking out base commit {metadata.base_commit}")
            checkout_clean(repo_dir, metadata.base_commit)

        if base_commit_found and patch_path.exists():
            log("Checking whether the gold patch applies")
            patch_check = check_patch(repo_dir, patch_path)
            patch_applies = patch_check.success
            if not patch_check.success:
                errors.append(f"Patch did not apply cleanly: {patch_check.stderr.strip()}")

        if base_commit_found:
            log("Running tests before applying the patch")
            before = run_tests_in_docker(
                repo_dir,
                metadata.test_command,
                timeout_seconds=metadata.timeout_seconds,
                image_tag_prefix=f"forge-{task_id}-before",
                exec_command=exec_command,
            )
            before_patch = _command_run("before_patch", before)
            before_results = parse_junit_xml(extract_report(before.stdout))

        if base_commit_found and patch_applies:
            log("Applying the gold patch")
            applied = apply_patch_file(repo_dir, patch_path)
            if not applied.success:
                errors.append(f"Patch apply failed after check: {applied.stderr.strip()}")
            else:
                log("Running tests after applying the patch")
                after = run_tests_in_docker(
                    repo_dir,
                    metadata.test_command,
                    timeout_seconds=metadata.timeout_seconds,
                    image_tag_prefix=f"forge-{task_id}-after",
                    exec_command=exec_command,
                )
                after_patch = _command_run("after_patch", after)
                after_results = parse_junit_xml(extract_report(after.stdout))

                # Attests post-patch idempotence (same patched tree, freshly rebuilt
                # image, run twice). It does NOT re-check the pre-patch baseline, so a
                # flaky *before* state is not caught here.
                log("Running deterministic post-patch rerun")
                rerun = run_tests_in_docker(
                    repo_dir,
                    metadata.test_command,
                    timeout_seconds=metadata.timeout_seconds,
                    image_tag_prefix=f"forge-{task_id}-rerun",
                    exec_command=exec_command,
                )
                deterministic_rerun = _command_run("deterministic_rerun", rerun)
    finally:
        if base_commit_found:
            try:
                checkout_clean(repo_dir, metadata.base_commit)
            except Exception as exc:  # noqa: BLE001 - cleanup failures belong in verification output.
                errors.append(f"Could not restore base checkout: {exc}")

    run_records = [run for run in (before_patch, after_patch, deterministic_rerun) if run is not None]
    docker_build_success = bool(run_records) and all(run.docker_build_success for run in run_records)
    infrastructure_failures = [
        f"{run.phase}: {reason}"
        for run in run_records
        if (reason := detect_test_infrastructure_failure(run)) is not None
    ]
    for failure in infrastructure_failures:
        errors.append(f"Test infrastructure failure: {failure}")
    test_environment_success = not infrastructure_failures
    tests_fail_before_patch = (
        before_patch is not None
        and test_environment_success
        and before_patch.docker_build_success
        and before_patch.exit_code not in (None, 0)
    )
    tests_pass_after_patch = after_patch is not None and test_environment_success and after_patch.docker_build_success and after_patch.exit_code == 0
    deterministic_rerun_success = deterministic_rerun is not None and test_environment_success and deterministic_rerun.docker_build_success and deterministic_rerun.exit_code == 0

    # Derive the targeted test sets only when both runs produced a parseable report;
    # otherwise leave them empty so reward falls back to whole-suite scoring.
    if before_results and after_results and test_environment_success:
        fail_to_pass, pass_to_pass = derive_test_deltas(before_results, after_results)
    else:
        fail_to_pass, pass_to_pass = [], []

    verification = VerificationResult(
        task_id=task_id,
        base_commit_found=base_commit_found,
        patch_applies=patch_applies,
        tests_fail_before_patch=tests_fail_before_patch,
        tests_pass_after_patch=tests_pass_after_patch,
        docker_build_success=docker_build_success,
        test_environment_success=test_environment_success,
        deterministic_rerun_success=deterministic_rerun_success,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        before_patch=before_patch,
        after_patch=after_patch,
        deterministic_rerun=deterministic_rerun,
        errors=errors,
    )
    verification.write_json(verification_path(root, task_id))
    return verification


def _prompt(metadata: TaskMetadata) -> str:
    body = metadata.pr_body.strip() or "No pull request body was provided."
    return f"""# Task: {metadata.id}

Repository: {metadata.repo_name}
Pull request: #{metadata.pr_number} - {metadata.pr_title}

## Pull Request Body

{body}

## Failing Test Command

```bash
{metadata.test_command}
```

Modify the code so that the tests pass.
"""


def _reward_script() -> str:
    return r'''#!/usr/bin/env python3
"""Standalone, self-contained reward for this task.

Usage:
  python reward.py                     score the base repo snapshot as-is
  python reward.py --patch fix.diff    apply a candidate fix, then score

The candidate patch is applied (git apply) onto a throwaway copy of repo/, so the
base snapshot is never mutated and reruns stay independent. Emits one JSON line:
{"score": 0.0|1.0, "tests_passed": bool, "error": str|null}. Set FORGE_DOCKER_BIN
to use a different container engine (e.g. podman).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# Sentinels for embedding a JUnit report in container stdout (kept in sync with
# forge.test_report). Targeted scoring uses the report; otherwise we fall back to
# the test command's exit code.
REPORT_PATH = "/tmp/forge-report.xml"
REPORT_START = "<<<FORGE_JUNIT_START>>>"
REPORT_END = "<<<FORGE_JUNIT_END>>>"
_PYTEST_RE = re.compile(r"(^|\s)(pytest|py\.test)(\s|$)|python[0-9.]*\s+-m\s+pytest")


def emit(score: float, tests_passed: bool, error: str | None) -> int:
    print(json.dumps({"score": score, "tests_passed": tests_passed, "error": error}))
    return 0


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.").lower()
    return normalized or "task"


def is_pytest_command(test_command: str) -> bool:
    return bool(_PYTEST_RE.search(test_command or ""))


def wrap_pytest_command(test_command: str) -> str:
    inner = f"{test_command} --junitxml={REPORT_PATH}"
    return (
        f"{inner}; __forge_code=$?; "
        f"echo '{REPORT_START}'; cat {REPORT_PATH} 2>/dev/null; echo '{REPORT_END}'; "
        f"exit $__forge_code"
    )


def extract_report(stdout: str):
    if not stdout or REPORT_START not in stdout or REPORT_END not in stdout:
        return None
    start = stdout.index(REPORT_START) + len(REPORT_START)
    end = stdout.index(REPORT_END, start)
    xml = stdout[start:end].strip()
    return xml or None


def parse_junit_xml(xml_text):
    results = {}
    if not xml_text:
        return results
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        nodeid = f"{classname}::{name}" if classname else name
        status = "passed"
        for child in case:
            tag = child.tag.lower()
            if tag in ("failure", "error", "skipped"):
                status = "failed" if tag == "failure" else tag
                break
        results[nodeid] = status
    return results


def score_targeted(results, fail_to_pass, pass_to_pass):
    ok = {nodeid for nodeid, status in results.items() if status == "passed"}
    return all(t in ok for t in fail_to_pass) and all(t in ok for t in pass_to_pass)


def stage_repo_with_patch(repo_dir: Path, patch_file: Path) -> tuple[Path | None, str | None]:
    """Copy repo_dir into a temp dir and apply the candidate patch. Returns (work_dir, error)."""

    if not patch_file.exists():
        return None, f"Candidate patch not found: {patch_file}"
    if shutil.which("git") is None:
        return None, "git executable not found on PATH (required to apply --patch)"
    stage_root = Path(tempfile.mkdtemp(prefix="forge-reward-stage-"))
    work = stage_root / "repo"
    shutil.copytree(repo_dir, work)
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", str(patch_file)],
        cwd=work,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if applied.returncode != 0:
        shutil.rmtree(stage_root, ignore_errors=True)
        detail = (applied.stderr or applied.stdout).strip()[-500:]
        return None, f"Candidate patch did not apply: {detail}"
    return work, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a candidate fix against this task's tests.")
    parser.add_argument("--patch", type=Path, default=None, help="Unified diff with the candidate fix to apply before testing.")
    args = parser.parse_args()

    package_dir = Path(__file__).resolve().parent
    task_path = package_dir / "task.json"
    repo_dir = package_dir / "repo"
    dockerfile = package_dir / "Dockerfile"

    if not task_path.exists():
        return emit(0.0, False, f"Missing task.json: {task_path}")
    if not repo_dir.exists():
        return emit(0.0, False, f"Missing repository directory: {repo_dir}")
    if not dockerfile.exists():
        return emit(0.0, False, f"Missing Dockerfile: {dockerfile}")

    build_context = repo_dir
    stage_root: Path | None = None
    if args.patch is not None:
        staged, error = stage_repo_with_patch(repo_dir, args.patch.resolve())
        if error is not None:
            return emit(0.0, False, error)
        build_context = staged
        stage_root = staged.parent

    try:
        docker_bin = os.environ.get("FORGE_DOCKER_BIN", "docker")
        if shutil.which(docker_bin) is None:
            return emit(0.0, False, f"Docker executable not found on PATH: {docker_bin}")

        task = json.loads(task_path.read_text(encoding="utf-8"))
        test_command = task["test_command"]
        timeout_seconds = int(task.get("timeout_seconds", 300))
        image_tag = f"forge-reward-{slug(task['id'])}-{uuid.uuid4().hex[:12]}"
        container_name = f"{image_tag}-run"

        eval_spec = task.get("eval") or {}
        fail_to_pass = list(eval_spec.get("fail_to_pass") or [])
        pass_to_pass = list(eval_spec.get("pass_to_pass") or [])
        scoped = bool(fail_to_pass) and is_pytest_command(test_command)
        exec_command = wrap_pytest_command(test_command) if scoped else test_command

        started_at = time.monotonic()
        try:
            build = subprocess.run(
                [docker_bin, "build", "--file", str(dockerfile), "--tag", image_tag, str(build_context)],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=max(120, timeout_seconds * 2),
            )
        except subprocess.TimeoutExpired:
            return emit(0.0, False, "Docker build timed out")

        if build.returncode != 0:
            detail = (build.stderr or build.stdout).strip()[-1000:]
            return emit(0.0, False, f"Docker build failed with code {build.returncode}: {detail}")

        try:
            run = subprocess.run(
                [
                    docker_bin, "run", "--rm", "--name", container_name,
                    "--network", "none", "--memory", "4g", "--cpus", "2",
                    "--pids-limit", "512", "--security-opt", "no-new-privileges",
                    image_tag, "sh", "-lc", exec_command,
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )
            if scoped:
                results = parse_junit_xml(extract_report(run.stdout))
                if results:
                    if score_targeted(results, fail_to_pass, pass_to_pass):
                        return emit(1.0, True, None)
                    missing = [t for t in fail_to_pass if results.get(t) != "passed"]
                    regressed = [t for t in pass_to_pass if results.get(t) != "passed"]
                    return emit(0.0, False, f"Targeted tests not satisfied (fail_to_pass missing: {len(missing)}, pass_to_pass regressed: {len(regressed)})")
                # No parseable report (e.g. collection error): fall back to exit code.
            if run.returncode == 0:
                return emit(1.0, True, None)
            return emit(0.0, False, f"Test command exited with code {run.returncode}")
        except subprocess.TimeoutExpired:
            subprocess.run([docker_bin, "rm", "-f", container_name], text=True, encoding="utf-8", errors="replace", capture_output=True)
            elapsed = round(time.monotonic() - started_at, 2)
            return emit(0.0, False, f"Test command timed out after {timeout_seconds} seconds ({elapsed}s elapsed)")
        finally:
            subprocess.run([docker_bin, "image", "rm", "-f", image_tag], text=True, encoding="utf-8", errors="replace", capture_output=True)
    finally:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _eval_spec(verification_file: Path) -> dict[str, list[str]]:
    """Read the targeted test sets from verification.json, robust to partial files."""

    try:
        data = json.loads(verification_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "fail_to_pass": list(data.get("fail_to_pass") or []),
        "pass_to_pass": list(data.get("pass_to_pass") or []),
    }


def package_task(task_id: str, *, root: Path | None = None, log: LogFn = _noop_log) -> Path:
    root = root or Path.cwd()
    metadata = load_metadata(root, task_id)
    repo_dir = _repo_dir(root, task_id)
    patch_path = gold_patch_path(root, task_id)
    verification_file = verification_path(root, task_id)

    if not patch_path.exists():
        raise FileNotFoundError(f"Gold patch not found. Run `forge fetch` first: {patch_path}")
    if not verification_file.exists():
        raise FileNotFoundError(f"Verification not found. Run `forge verify {task_id}` first: {verification_file}")

    package_dir = _taskpack_dir(root, task_id)
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    log(f"Checking out base commit {metadata.base_commit} for package snapshot")
    checkout_clean(repo_dir, metadata.base_commit)

    # The packed task.json is a superset of the metadata: it also carries the eval
    # spec (targeted test sets) so the standalone reward.py can score without forge.
    task_payload = json.loads(metadata.model_dump_json())
    task_payload["eval"] = _eval_spec(verification_file)
    (package_dir / "task.json").write_text(json.dumps(task_payload, indent=2), encoding="utf-8")
    (package_dir / "prompt.md").write_text(_prompt(metadata), encoding="utf-8")
    (package_dir / "Dockerfile").write_text(generate_python_dockerfile(), encoding="utf-8")
    (package_dir / "reward.py").write_text(_reward_script(), encoding="utf-8")
    shutil.copy2(patch_path, package_dir / "gold.patch")
    shutil.copy2(verification_file, package_dir / "verification.json")

    log("Copying base repository snapshot into taskpack")
    shutil.copytree(
        repo_dir,
        package_dir / "repo",
        ignore=shutil.ignore_patterns(
            ".git",
            # Forge builds with its own generated Dockerfile (COPY . /workspace); an
            # upstream .dockerignore would silently drop files the tests need, so the
            # reward image would test a different tree than this snapshot. Strip it.
            ".dockerignore",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "build",
            "dist",
            "*.egg-info",
        ),
    )
    return package_dir