from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field

from forge.explorer import ExplorationCandidate


DEFAULT_ENV_FILE = Path(".env")


class LLMReviewError(RuntimeError):
    """Raised when optional LLM candidate review cannot run."""


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    api_key: str
    endpoint: str | None = None
    deployment: str | None = None
    model: str | None = None
    api_version: str = "2024-10-21"


def describe_llm_config(config: LLMConfig) -> str:
    """Return a safe, non-secret provider description for CLI output."""

    if config.provider == "azure-openai":
        return f"Azure OpenAI deployment {config.deployment} at {config.endpoint}"
    return f"Gemini model {config.model}"


class CandidateLLMReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    risk: str = "unknown"
    suggested_test_command: str | None = None


def load_env_values(env_file: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Load environment variables, overlaying process env over a local .env file."""

    values: dict[str, str] = {}
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = _clean_env_value(value)
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def resolve_llm_config(provider: str = "auto", env_file: Path = DEFAULT_ENV_FILE) -> LLMConfig:
    """Resolve an optional LLM provider from .env/process environment."""

    provider = provider.strip().lower()
    values = load_env_values(env_file)
    if provider not in {"auto", "azure-openai", "gemini"}:
        raise LLMReviewError("llm provider must be one of: auto, azure-openai, gemini")

    if provider in {"auto", "azure-openai"}:
        endpoint = values.get("AZURE_OPENAI_ENDPOINT")
        deployment = values.get("AZURE_OPENAI_DEPLOYMENT")
        api_key = values.get("AZURE_OPENAI_API_KEY")
        if endpoint and deployment and api_key:
            return LLMConfig(
                provider="azure-openai",
                endpoint=endpoint,
                deployment=deployment,
                api_key=api_key,
                api_version=values.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        if provider == "azure-openai":
            raise LLMReviewError(
                "Azure OpenAI review requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, and AZURE_OPENAI_API_KEY"
            )

    if provider in {"auto", "gemini"}:
        api_key = values.get("GEMINI_API_KEY")
        if api_key:
            return LLMConfig(
                provider="gemini",
                api_key=api_key,
                model=values.get("GEMINI_MODEL", "gemini-1.5-flash"),
            )
        if provider == "gemini":
            raise LLMReviewError("Gemini review requires GEMINI_API_KEY")

    raise LLMReviewError(
        "No LLM provider configured. Add Azure OpenAI or Gemini variables to .env, or run without --llm-review."
    )


def review_exploration_candidates(
    candidates: list[ExplorationCandidate],
    *,
    provider: str = "auto",
    env_file: Path = DEFAULT_ENV_FILE,
    max_candidates: int = 10,
) -> list[ExplorationCandidate]:
    """Review and rerank candidates with an optional LLM provider."""

    if not candidates:
        return []
    config = resolve_llm_config(provider, env_file)
    reviewed = candidates[:max_candidates]
    prompt = _review_prompt(reviewed)
    if config.provider == "azure-openai":
        response_text = _call_azure_openai(config, prompt)
    elif config.provider == "gemini":
        response_text = _call_gemini(config, prompt)
    else:
        raise LLMReviewError(f"Unsupported provider: {config.provider}")

    reviews = {review.candidate_id: review for review in parse_llm_reviews(response_text)}
    updated: list[ExplorationCandidate] = []
    for candidate in candidates:
        review = reviews.get(candidate.id)
        if review:
            update = {
                "llm_score": review.score,
                "llm_rationale": review.rationale,
                "llm_risk": review.risk,
                "llm_suggested_test_command": review.suggested_test_command,
            }
            if review.suggested_test_command:
                update["test_command"] = review.suggested_test_command
            updated.append(
                candidate.model_copy(
                    update=update
                )
            )
        else:
            updated.append(candidate)
    return sorted(updated, key=lambda item: (item.llm_score is not None, item.llm_score or 0.0, item.score), reverse=True)


def parse_llm_reviews(response_text: str) -> list[CandidateLLMReview]:
    """Parse a strict or fenced JSON review response from an LLM."""

    payload = json.loads(_extract_json(response_text))
    raw_reviews = payload.get("reviews") if isinstance(payload, dict) else payload
    if not isinstance(raw_reviews, list):
        raise LLMReviewError("LLM response must contain a reviews list")

    reviews: list[CandidateLLMReview] = []
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            continue
        if "candidate_id" not in raw and "id" in raw:
            raw = {**raw, "candidate_id": raw["id"]}
        reviews.append(CandidateLLMReview.model_validate(raw))
    return reviews


def _call_azure_openai(config: LLMConfig, prompt: str) -> str:
    endpoint = (config.endpoint or "").rstrip("/")
    deployment = config.deployment or ""
    if endpoint.endswith("/openai/v1"):
        url = f"{endpoint}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "api-key": config.api_key,
            "Content-Type": "application/json",
        }
        params: dict[str, str] = {}
        payload = {
            "model": deployment,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 1200,
        }
    else:
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions"
        headers = {"api-key": config.api_key, "Content-Type": "application/json"}
        params = {"api-version": config.api_version}
        payload = {
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 1200,
        }

    response = requests.post(
        url,
        params=params,
        headers=headers,
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        raise LLMReviewError(f"Azure OpenAI review failed with HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMReviewError("Azure OpenAI response did not contain chat completion content") from exc


def _call_gemini(config: LLMConfig, prompt: str) -> str:
    model = config.model or "gemini-1.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        url,
        params={"key": config.api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{_system_prompt()}\n\n{prompt}"}],
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 1200},
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise LLMReviewError(f"Gemini review failed with HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMReviewError("Gemini response did not contain generated text") from exc


def _review_prompt(candidates: list[ExplorationCandidate]) -> str:
    payload = [
        {
            "id": candidate.id,
            "repo": candidate.repo_name,
            "pr_number": candidate.pr_number,
            "title": candidate.pr_title,
            "body_excerpt": candidate.pr_body_excerpt,
            "patch_stats": {
                "additions": candidate.additions,
                "deletions": candidate.deletions,
                "changed_files": candidate.changed_files,
            },
            "heuristic_score": candidate.score,
            "heuristic_reasons": candidate.reasons,
            "default_test_command": candidate.test_command,
        }
        for candidate in candidates
    ]
    return (
        "Review these GitHub pull request candidates for building reproducible SWE evaluation tasks. "
        "Prefer PRs likely to be real bug fixes with executable tests, small blast radius, and low environment risk. "
        "Return JSON only in this shape: "
        "{\"reviews\":[{\"candidate_id\":\"...\",\"score\":0.0,\"rationale\":\"...\","
        "\"risk\":\"low|medium|high\",\"suggested_test_command\":null}]}.\n\n"
        f"Candidates:\n{json.dumps(payload, indent=2)}"
    )


def _system_prompt() -> str:
    return (
        "You are reviewing GitHub pull requests as candidate software-engineering RL/evaluation tasks. "
        "You are advisory only: executable verification remains the source of truth. "
        "Score candidates from 0 to 1 based on likely task quality. Return JSON only."
    )


def _extract_json(value: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    starts = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
    if not starts:
        raise LLMReviewError("LLM response did not contain JSON")
    start = min(starts)
    end = max(stripped.rfind("}"), stripped.rfind("]"))
    if end <= start:
        raise LLMReviewError("LLM response did not contain complete JSON")
    return stripped[start : end + 1]


def _clean_env_value(value: str) -> str:
    clean = value.strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {"'", '"'}:
        return clean[1:-1]
    return clean