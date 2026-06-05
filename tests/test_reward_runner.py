from __future__ import annotations

from pathlib import Path

from forge.reward_runner import RewardResult, format_reward, run_reward_script


def test_reward_output_format_for_success() -> None:
    result = format_reward(0)

    payload = result.model_dump()
    assert payload == {"score": 1.0, "tests_passed": True, "error": None}
    RewardResult.model_validate(payload)


def test_reward_output_format_for_failure() -> None:
    result = format_reward(2)

    payload = result.model_dump()
    assert payload["score"] == 0.0
    assert payload["tests_passed"] is False
    assert payload["error"] == "Test command exited with code 2"
    RewardResult.model_validate(payload)


def test_run_reward_script_accepts_relative_taskpack_path(tmp_path: Path, monkeypatch) -> None:
    taskpack = tmp_path / "taskpack"
    taskpack.mkdir()
    reward_script = taskpack / "reward.py"
    reward_script.write_text(
        "import json\nprint(json.dumps({'score': 1.0, 'tests_passed': True, 'error': None}))\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = run_reward_script(Path("taskpack"))

    assert result == RewardResult(score=1.0, tests_passed=True, error=None)