from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from urllib.parse import urlparse

import requests


GITHUB_API_ROOT = "https://api.github.com"
USER_AGENT = "swe-rl-forge-lite/0.1"


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PullRequestInfo:
    repo_name: str
    title: str
    body: str
    base_commit: str
    head_commit: str
    base_ref: str
    head_ref: str
    diff_url: str


class GitHubAPIError(RuntimeError):
    """Raised when the public GitHub API cannot satisfy a PR request."""


def parse_github_repo(repo_url: str) -> GitHubRepo:
    """Parse owner and repo from HTTPS or SSH GitHub URLs."""

    if repo_url.startswith("git@github.com:"):
        path = repo_url.removeprefix("git@github.com:")
    else:
        parsed = urlparse(repo_url)
        if parsed.netloc.lower() != "github.com":
            raise ValueError(f"Unsupported GitHub URL: {repo_url}")
        path = parsed.path.lstrip("/")

    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"Could not parse owner/repo from URL: {repo_url}")
    return GitHubRepo(owner=parts[0], name=parts[1])


def _github_get(url: str, *, accept: str = "application/vnd.github+json", params: dict[str, object] | None = None) -> requests.Response:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        raise GitHubAPIError(f"GitHub request failed for {url}: {exc}") from exc

    if response.status_code == 403 and "rate limit" in response.text.lower():
        reset_at = _rate_limit_reset_at(response)
        suffix = f" Reset: {reset_at}." if reset_at else ""
        raise GitHubAPIError(
            "GitHub API rate limit exceeded. Public repositories do not require a token, "
            f"but long exploration sessions can set GITHUB_TOKEN or GH_TOKEN for a higher limit.{suffix}"
        )
    if response.status_code >= 400:
        raise GitHubAPIError(f"GitHub request failed with HTTP {response.status_code}: {response.text[:500]}")
    return response


def _rate_limit_reset_at(response: requests.Response) -> str | None:
    reset = response.headers.get("X-RateLimit-Reset")
    if not reset:
        return None
    try:
        return datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
    except ValueError:
        return None


def fetch_pull_request(repo_url: str, pr_number: int) -> PullRequestInfo:
    """Fetch public pull request metadata from GitHub."""

    repo = parse_github_repo(repo_url)
    url = f"{GITHUB_API_ROOT}/repos/{repo.owner}/{repo.name}/pulls/{pr_number}"
    data = _github_get(url).json()

    base = data.get("base") or {}
    head = data.get("head") or {}
    base_repo = base.get("repo") or {}
    repo_name = base_repo.get("full_name") or repo.full_name

    try:
        return PullRequestInfo(
            repo_name=repo_name,
            title=data["title"] or "",
            body=data.get("body") or "",
            base_commit=base["sha"],
            head_commit=head["sha"],
            base_ref=base.get("ref") or "",
            head_ref=head.get("ref") or "",
            diff_url=data.get("diff_url") or f"https://github.com/{repo.full_name}/pull/{pr_number}.diff",
        )
    except KeyError as exc:
        raise GitHubAPIError(f"GitHub PR response is missing expected field: {exc}") from exc


def fetch_pr_diff(repo_url: str, pr_number: int) -> str:
    """Fetch a PR diff in git-apply-compatible format."""

    repo = parse_github_repo(repo_url)
    url = f"{GITHUB_API_ROOT}/repos/{repo.owner}/{repo.name}/pulls/{pr_number}"
    response = _github_get(url, accept="application/vnd.github.v3.diff")
    diff = response.text
    if not diff.strip():
        raise GitHubAPIError(f"Pull request diff is empty for {repo.full_name}#{pr_number}")
    return diff