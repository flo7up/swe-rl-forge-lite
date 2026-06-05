from __future__ import annotations

from pathlib import Path

from forge.explorer import candidate_from_pr_data
from forge.llm_review import parse_llm_reviews, resolve_llm_config, review_exploration_candidates


class FakeResponse:
    status_code = 200
    text = "ok"

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"reviews":[{"candidate_id":"example-project-pr-42","score":0.93,"rationale":"Small bug fix with tests.","risk":"low","suggested_test_command":"pytest tests"}]}'
                    }
                }
            ]
        }


def _candidate():
    candidate = candidate_from_pr_data(
        {
            "number": 42,
            "title": "Fix parser regression",
            "body": "Adds a pytest regression test for a broken parser edge case.",
            "html_url": "https://github.com/example/project/pull/42",
            "merged_at": "2026-01-01T00:00:00Z",
            "additions": 30,
            "deletions": 2,
            "changed_files": 2,
            "base": {"repo": {"full_name": "example/project", "clone_url": "https://github.com/example/project.git"}},
        }
    )
    assert candidate is not None
    return candidate


def test_resolve_llm_config_from_env_file(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com\n"
        "AZURE_OPENAI_DEPLOYMENT=test-deployment\n"
        "AZURE_OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    config = resolve_llm_config("azure-openai", env_file)

    assert config.provider == "azure-openai"
    assert config.deployment == "test-deployment"


def test_parse_llm_reviews_accepts_fenced_json() -> None:
    reviews = parse_llm_reviews(
        '```json\n{"reviews":[{"candidate_id":"task-1","score":0.5,"rationale":"Maybe useful","risk":"medium"}]}\n```'
    )

    assert reviews[0].candidate_id == "task-1"
    assert reviews[0].score == 0.5


def test_review_exploration_candidates_with_mocked_azure(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_post(*args, **kwargs):
        captured["url"] = args[0]
        captured["json"] = kwargs["json"]
        return FakeResponse()

    env_file = tmp_path / ".env"
    env_file.write_text(
        "AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com/openai/v1\n"
        "AZURE_OPENAI_DEPLOYMENT=test-deployment\n"
        "AZURE_OPENAI_API_KEY=test-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("forge.llm_review.requests.post", fake_post)

    reviewed = review_exploration_candidates([_candidate()], provider="azure-openai", env_file=env_file)

    assert reviewed[0].llm_score == 0.93
    assert reviewed[0].llm_risk == "low"
    assert reviewed[0].llm_suggested_test_command == "pytest tests"
    assert captured["url"] == "https://example.openai.azure.com/openai/v1/chat/completions"
    assert captured["json"]["model"] == "test-deployment"