"""Coverage for the candidate-patch rollout hook in the generated reward.py.

The reward script is a self-contained template (no `forge` import), so these
tests run the *generated* script as a subprocess. Docker is forced absent via a
bogus FORGE_DOCKER_BIN, which lets us prove patch-staging behavior without a
container engine: a good patch applies and the flow reaches the docker check,
while a bad patch is rejected before it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from forge import task_builder
from forge.reward_runner import RewardResult, run_reward_script
from forge.task_builder import (
    _reward_script,
    gold_patch_path,
    metadata_path,
    package_task,
    verification_path,
)
from forge.task_schema import TaskMetadata

GOOD_PATCH = (
    b"diff --git a/m.py b/m.py\n"
    b"--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
)
BAD_PATCH = (
    b"diff --git a/m.py b/m.py\n"
    b"--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def g():\n-    return 1\n+    return 2\n"
)

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git required to apply candidate patches")


def _metadata() -> TaskMetadata:
    return TaskMetadata(
        id="rollout-001",
        repo_url="https://github.com/example/project.git",
        pr_number=3,
        repo_name="example/project",
        pr_title="Fix f",
        pr_body="body",
        base_commit="a" * 40,
        head_commit="b" * 40,
        test_command="pytest",
        language="python",
        timeout_seconds=60,
    )


def _build_taskpack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    metadata = _metadata()
    metadata.write_json(metadata_path(tmp_path, metadata.id))
    gold_patch_path(tmp_path, metadata.id).write_bytes(b"diff --git a/m.py b/m.py\n")
    verification_path(tmp_path, metadata.id).write_text("{}", encoding="utf-8")

    repo_dir = tmp_path / ".forge" / "repos" / metadata.id
    repo_dir.mkdir(parents=True)
    (repo_dir / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(task_builder, "checkout_clean", lambda *a, **k: None)
    return package_task(metadata.id, root=tmp_path)


def _run_reward(package_dir: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["FORGE_DOCKER_BIN"] = "forge-no-such-docker-bin"  # force the docker-absent branch
    completed = subprocess.run(
        [sys.executable, str(package_dir / "reward.py"), *args],
        cwd=package_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_generated_reward_script_is_valid_and_supports_patch() -> None:
    src = _reward_script()
    compile(src, "reward.py", "exec")  # syntactically valid
    assert "--patch" in src
    assert "stage_repo_with_patch" in src


def test_reward_template_declares_scoped_scoring() -> None:
    src = _reward_script()
    assert "score_targeted" in src
    assert "FORGE_JUNIT_START" in src
    assert 'task.get("eval")' in src


def test_reward_template_scoping_helpers_match_forge() -> None:
    from forge import test_report as tr

    ns: dict = {}
    exec(_reward_script(), ns)  # defines helpers; main() only runs under __main__

    xml = '<testsuites><testsuite>' \
          '<testcase classname="pkg" name="a"></testcase>' \
          '<testcase classname="pkg" name="b"><failure/></testcase>' \
          '</testsuite></testsuites>'

    assert ns["parse_junit_xml"](xml) == tr.parse_junit_xml(xml)
    assert ns["wrap_pytest_command"]("pytest -q") == tr.wrap_pytest_command("pytest -q")

    stdout = f'{ns["REPORT_START"]}{xml}{ns["REPORT_END"]}'
    assert ns["extract_report"](stdout) == tr.extract_report(stdout)

    # targeted scoring: pass only when every fail_to_pass + pass_to_pass passed
    assert ns["score_targeted"]({"pkg::a": "passed", "pkg::b": "passed"}, ["pkg::a"], ["pkg::b"]) is True
    assert ns["score_targeted"]({"pkg::a": "passed", "pkg::b": "failed"}, ["pkg::a"], ["pkg::b"]) is False


@requires_git
def test_good_candidate_patch_applies_then_reaches_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_dir = _build_taskpack(tmp_path, monkeypatch)
    patch = tmp_path / "good.patch"
    patch.write_bytes(GOOD_PATCH)

    payload = _run_reward(package_dir, "--patch", str(patch))

    # Patch applied cleanly, so the failure is the (forced) missing docker, not the patch.
    assert payload["tests_passed"] is False
    assert "Docker executable not found" in payload["error"]
    # The base snapshot must remain unmodified.
    assert (package_dir / "repo" / "m.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"


@requires_git
def test_bad_candidate_patch_is_rejected_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_dir = _build_taskpack(tmp_path, monkeypatch)
    patch = tmp_path / "bad.patch"
    patch.write_bytes(BAD_PATCH)

    payload = _run_reward(package_dir, "--patch", str(patch))

    assert payload["score"] == 0.0
    assert payload["tests_passed"] is False
    assert "Candidate patch did not apply" in payload["error"]


def test_missing_candidate_patch_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_dir = _build_taskpack(tmp_path, monkeypatch)

    payload = _run_reward(package_dir, "--patch", str(tmp_path / "nope.patch"))

    assert payload["score"] == 0.0
    assert "Candidate patch not found" in payload["error"]


def test_no_patch_preserves_base_scoring_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_dir = _build_taskpack(tmp_path, monkeypatch)

    payload = _run_reward(package_dir)

    # Without --patch the script scores the base repo directly; here it only reaches
    # the forced-absent docker check, proving the base path is intact.
    assert payload["tests_passed"] is False
    assert "Docker executable not found" in payload["error"]


def test_run_reward_script_passes_patch_through(tmp_path: Path) -> None:
    taskpack = tmp_path / "taskpack"
    taskpack.mkdir()
    (taskpack / "reward.py").write_text(
        "import sys, json\nprint(json.dumps({'score': 0.0, 'tests_passed': False, 'error': ' '.join(sys.argv[1:])}))\n",
        encoding="utf-8",
    )

    result = run_reward_script(taskpack, patch_path=Path("candidate.patch"))

    assert isinstance(result, RewardResult)
    assert "--patch" in (result.error or "")
    assert (result.error or "").endswith("candidate.patch")
