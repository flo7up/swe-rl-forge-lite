from __future__ import annotations

import yaml

from forge.explorer import candidate_from_pr_data, candidates_to_yaml, score_pr_candidate


def _pr_data(**overrides):
    data = {
        "number": 42,
        "title": "Fix regression in parser tests",
        "body": "Adds a pytest regression test for a broken parser edge case.",
        "html_url": "https://github.com/example/project/pull/42",
        "merged_at": "2026-01-01T00:00:00Z",
        "additions": 80,
        "deletions": 12,
        "changed_files": 3,
        "base": {
            "repo": {
                "full_name": "example/project",
                "clone_url": "https://github.com/example/project.git",
            }
        },
    }
    data.update(overrides)
    return data


def test_score_pr_candidate_prefers_small_bugfix_with_tests() -> None:
    score, reasons = score_pr_candidate(_pr_data())

    assert score > 0.8
    assert "bug/fix language" in reasons
    assert "test signal" in reasons


def test_candidate_from_pr_data_creates_yaml_ready_task() -> None:
    candidate = candidate_from_pr_data(_pr_data(), test_command="pytest tests", timeout_seconds=120)

    assert candidate is not None
    assert candidate.id == "example-project-pr-42"
    assert candidate.to_task_config() == {
        "id": "example-project-pr-42",
        "repo_url": "https://github.com/example/project.git",
        "pr_number": 42,
        "base_ref": None,
        "test_command": "pytest tests",
        "language": "python",
        "timeout_seconds": 120,
    }


def test_candidates_to_yaml_outputs_forge_config() -> None:
    candidate = candidate_from_pr_data(_pr_data())
    assert candidate is not None

    payload = yaml.safe_load(candidates_to_yaml([candidate]))

    assert payload["tasks"][0]["id"] == "example-project-pr-42"
    assert payload["tasks"][0]["repo_url"] == "https://github.com/example/project.git"