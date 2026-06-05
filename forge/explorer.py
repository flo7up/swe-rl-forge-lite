from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from forge.github_pr import GITHUB_API_ROOT, GitHubAPIError, _github_get


DEFAULT_EXPLORE_QUERY = "is:pr is:merged language:Python pytest fix"


class ExplorationCandidate(BaseModel):
    """A GitHub pull request that may become a forge task after verification."""

    model_config = ConfigDict(extra="forbid")

    id: str
    repo_name: str
    repo_url: str
    pr_number: int = Field(gt=0)
    pr_title: str
    html_url: str
    merged_at: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    pr_body_excerpt: str = ""
    llm_score: float | None = Field(default=None, ge=0.0, le=1.0)
    llm_rationale: str | None = None
    llm_risk: str | None = None
    llm_suggested_test_command: str | None = None
    test_command: str = "pytest"
    language: str = "python"
    timeout_seconds: int = 300

    def to_task_config(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repo_url": self.repo_url,
            "pr_number": self.pr_number,
            "base_ref": None,
            "test_command": self.llm_suggested_test_command or self.test_command,
            "language": self.language,
            "timeout_seconds": self.timeout_seconds,
        }


def explore_github_prs(
    *,
    query: str | None = None,
    limit: int = 10,
    test_command: str = "pytest",
    language: str = "python",
    timeout_seconds: int = 300,
) -> list[ExplorationCandidate]:
    """Search public GitHub PRs and return ranked task candidates."""

    if limit < 1:
        raise ValueError("limit must be at least 1")

    search_query = query or DEFAULT_EXPLORE_QUERY
    per_page = min(max(limit * 4, 10), 100)
    search_url = f"{GITHUB_API_ROOT}/search/issues"
    search = _github_get(search_url, params={"q": search_query, "sort": "updated", "order": "desc", "per_page": per_page}).json()

    candidates: list[ExplorationCandidate] = []
    for item in search.get("items", []):
        pull_request = item.get("pull_request") or {}
        pr_api_url = pull_request.get("url")
        if not pr_api_url:
            continue
        try:
            pr_data = _github_get(pr_api_url).json()
        except GitHubAPIError:
            continue

        candidate = candidate_from_pr_data(
            pr_data,
            test_command=test_command,
            language=language,
            timeout_seconds=timeout_seconds,
        )
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:limit]


def candidate_from_pr_data(
    pr_data: dict[str, Any],
    *,
    test_command: str = "pytest",
    language: str = "python",
    timeout_seconds: int = 300,
) -> ExplorationCandidate | None:
    """Convert GitHub PR API data into a ranked candidate."""

    base = pr_data.get("base") or {}
    repo = base.get("repo") or {}
    repo_name = repo.get("full_name")
    clone_url = repo.get("clone_url")
    pr_number = pr_data.get("number")
    title = pr_data.get("title") or ""
    html_url = pr_data.get("html_url") or ""

    if not repo_name or not clone_url or not pr_number:
        return None

    score, reasons = score_pr_candidate(pr_data)
    return ExplorationCandidate(
        id=_candidate_id(repo_name, int(pr_number)),
        repo_name=repo_name,
        repo_url=clone_url,
        pr_number=int(pr_number),
        pr_title=title,
        html_url=html_url,
        merged_at=pr_data.get("merged_at"),
        additions=int(pr_data.get("additions") or 0),
        deletions=int(pr_data.get("deletions") or 0),
        changed_files=int(pr_data.get("changed_files") or 0),
        score=score,
        reasons=reasons,
        pr_body_excerpt=_excerpt(pr_data.get("body") or ""),
        test_command=test_command,
        language=language.lower(),
        timeout_seconds=timeout_seconds,
    )


def score_pr_candidate(pr_data: dict[str, Any]) -> tuple[float, list[str]]:
    """Heuristically score whether a PR is worth verifying as a SWE task."""

    title = pr_data.get("title") or ""
    body = pr_data.get("body") or ""
    text = f"{title}\n{body}".lower()
    additions = int(pr_data.get("additions") or 0)
    deletions = int(pr_data.get("deletions") or 0)
    changed_files = int(pr_data.get("changed_files") or 0)
    merged_at = pr_data.get("merged_at")

    points = 0.0
    reasons: list[str] = []

    if merged_at:
        points += 0.15
        reasons.append("merged PR")

    if any(word in text for word in ("bug", "fix", "regression", "failure", "failing", "broken")):
        points += 0.25
        reasons.append("bug/fix language")

    if any(word in text for word in ("test", "pytest", "unit test", "regression test")):
        points += 0.2
        reasons.append("test signal")

    if 1 <= changed_files <= 8:
        points += 0.15
        reasons.append("small changed-file count")
    elif changed_files > 20:
        points -= 0.15
        reasons.append("large changed-file count")

    churn = additions + deletions
    if 1 <= churn <= 500:
        points += 0.15
        reasons.append("moderate patch size")
    elif churn > 1500:
        points -= 0.2
        reasons.append("large patch size")

    if any(word in text for word in ("docs", "documentation", "typo", "readme")):
        points -= 0.15
        reasons.append("documentation-only risk")

    score = max(0.0, min(1.0, round(points, 3)))
    return score, reasons


def candidates_to_yaml(candidates: list[ExplorationCandidate]) -> str:
    """Render exploration candidates as a forge tasks YAML document."""

    payload = {"tasks": [candidate.to_task_config() for candidate in candidates]}
    return yaml.safe_dump(payload, sort_keys=False)


def write_candidates_yaml(candidates: list[ExplorationCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(candidates_to_yaml(candidates), encoding="utf-8")


def _candidate_id(repo_name: str, pr_number: int) -> str:
    return f"{repo_name.replace('/', '-')}-pr-{pr_number}"


def _excerpt(value: str, *, limit: int = 500) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."