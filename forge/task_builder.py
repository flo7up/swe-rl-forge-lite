from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from forge.config import TaskConfig, load_config
from forge.docker_runner import DockerRunResult, generate_python_dockerfile, run_tests_in_docker
from forge.git_utils import GitCommandError, checkout_clean, clone_or_update, ensure_commit_available, fetch_ref, repo_has_commit, resolve_ref
from forge.github_pr import fetch_pr_diff, fetch_pull_request
from forge.patch_utils import apply_patch_file, check_patch
from forge.task_schema import CommandRun, TaskMetadata, VerificationResult


FORGE_DIR = Path(".forge")
REPOS_DIR = FORGE_DIR / "repos"
TASKS_DIR = FORGE_DIR / "tasks"
TASKPACKS_DIR = Path("taskpacks")

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    return None


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
    gold_patch_path(root, task.id).write_text(diff, encoding="utf-8")

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
            )
            before_patch = _command_run("before_patch", before)

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
                )
                after_patch = _command_run("after_patch", after)

                log("Running deterministic post-patch rerun")
                rerun = run_tests_in_docker(
                    repo_dir,
                    metadata.test_command,
                    timeout_seconds=metadata.timeout_seconds,
                    image_tag_prefix=f"forge-{task_id}-rerun",
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
    tests_fail_before_patch = before_patch is not None and before_patch.docker_build_success and before_patch.exit_code not in (None, 0)
    tests_pass_after_patch = after_patch is not None and after_patch.docker_build_success and after_patch.exit_code == 0
    deterministic_rerun_success = deterministic_rerun is not None and deterministic_rerun.docker_build_success and deterministic_rerun.exit_code == 0

    verification = VerificationResult(
        task_id=task_id,
        base_commit_found=base_commit_found,
        patch_applies=patch_applies,
        tests_fail_before_patch=tests_fail_before_patch,
        tests_pass_after_patch=tests_pass_after_patch,
        docker_build_success=docker_build_success,
        deterministic_rerun_success=deterministic_rerun_success,
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
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path


def emit(score: float, tests_passed: bool, error: str | None) -> int:
    print(json.dumps({"score": score, "tests_passed": tests_passed, "error": error}))
    return 0


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.").lower()
    return normalized or "task"


def main() -> int:
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
    if shutil.which("docker") is None:
        return emit(0.0, False, "Docker executable not found on PATH")

    task = json.loads(task_path.read_text(encoding="utf-8"))
    test_command = task["test_command"]
    timeout_seconds = int(task.get("timeout_seconds", 300))
    image_tag = f"forge-reward-{slug(task['id'])}-{uuid.uuid4().hex[:12]}"
    container_name = f"{image_tag}-run"

    started_at = time.monotonic()
    try:
        build = subprocess.run(
            ["docker", "build", "--file", str(dockerfile), "--tag", image_tag, str(repo_dir)],
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
            ["docker", "run", "--rm", "--name", container_name, image_tag, "sh", "-lc", test_command],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
        if run.returncode == 0:
            return emit(1.0, True, None)
        return emit(0.0, False, f"Test command exited with code {run.returncode}")
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", container_name], text=True, encoding="utf-8", errors="replace", capture_output=True)
        elapsed = round(time.monotonic() - started_at, 2)
        return emit(0.0, False, f"Test command timed out after {timeout_seconds} seconds ({elapsed}s elapsed)")
    finally:
        subprocess.run(["docker", "image", "rm", "-f", image_tag], text=True, encoding="utf-8", errors="replace", capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
'''


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

    (package_dir / "task.json").write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
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