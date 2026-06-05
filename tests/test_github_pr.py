from __future__ import annotations

import pytest

from forge.github_pr import GitHubAPIError, _github_get


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def test_github_get_uses_optional_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: dict[str, str] = {}

    def fake_get(url, *, headers, params, timeout):
        captured_headers.update(headers)
        return FakeResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr("forge.github_pr.requests.get", fake_get)

    _github_get("https://api.github.com/repos/example/project/pulls/1")

    assert captured_headers["Authorization"] == "Bearer test-token"


def test_github_get_rate_limit_message_mentions_optional_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, *, headers, params, timeout):
        return FakeResponse(
            status_code=403,
            text="API rate limit exceeded",
            headers={"X-RateLimit-Reset": "1780686301"},
        )

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("forge.github_pr.requests.get", fake_get)

    with pytest.raises(GitHubAPIError, match="GITHUB_TOKEN or GH_TOKEN"):
        _github_get("https://api.github.com/repos/example/project/pulls/1")