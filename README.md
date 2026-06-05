# swe-rl-forge-lite

`swe-rl-forge-lite` is a small, open-source framework for turning historical GitHub pull requests into reproducible software-engineering tasks for coding agents.

The project is intentionally modest: it is not a training system, a benchmark leaderboard, or an agent rollout engine. It is the first building block of a tiny hill-climbing machine for SWE agents: create a task from a real fix, verify that the fix matters, package the environment, run a binary reward, and report whether the task is usable.

## Project Flow

![From GitHub PRs to learning: building RL substrate for coding agents](Picture1.png)

## Why This Matters

LLM training and evaluation for software engineering need environments where success is grounded in executable evidence. A useful task should answer a few hard questions:

- Can we reconstruct the repository before the fix?
- Does the historical patch apply cleanly?
- Do tests fail before the patch?
- Do tests pass after the patch?
- Can another runner reproduce the reward without hidden state?

When those pieces are available, a pull request becomes more than an example diff. It becomes a verifiable environment: a prompt, a repository state, a known-good patch, a test command, a reward function, and a quality report. That is the unit this project is designed to produce.

## Features

- Ingest public GitHub pull requests without requiring a GitHub token.
- Clone and reconstruct the repository at the PR base commit.
- Save PR metadata and the ground-truth patch.
- Run pre-patch and post-patch tests in Docker.
- Package tasks into self-contained `taskpacks/<task_id>/` folders.
- Generate a standalone `reward.py` script with binary test-based reward.
- Print a task quality report with a recommended status.
- Explore public GitHub PRs and emit candidate task YAML for verification.
- Generate a local static dashboard for observing task status and verification results.

## Installation

Python 3.11+ and Docker are required for verification and reward execution.

```bash
pip install -e .
```

For local development:

```bash
pip install -e ".[dev]"
pytest
```

Check the CLI:

```bash
forge --help
```

The public GitHub API works without a token for light usage. For longer exploration sessions, set `GITHUB_TOKEN` or `GH_TOKEN` in your terminal to raise the rate limit. Tokens are optional and should not be committed.

## Quickstart

Edit `examples/tasks.yaml` or create your own config:

```yaml
tasks:
  - id: click-pr-001
    repo_url: "https://github.com/pallets/click.git"
    pr_number: 1
    base_ref: null
    test_command: "pytest"
    language: "python"
    timeout_seconds: 300
```

Fetch the task data:

```bash
forge fetch examples/tasks.yaml
```

Verify the historical patch:

```bash
forge verify click-pr-001
```

Package the task:

```bash
forge package click-pr-001
```

Run the reward:

```bash
forge reward taskpacks/click-pr-001
```

The reward command prints JSON:

```json
{
  "score": 0.0,
  "tests_passed": false,
  "error": "Test command exited with code 1"
}
```

Generate the quality report:

```bash
forge report click-pr-001
```

Generate a local dashboard:

```bash
forge dashboard
```

Open `.forge/dashboard/index.html` in a browser to inspect fetched, verified, packaged, usable, needs-review, and invalid tasks.

You can also run the local demo script:

```powershell
./scripts/demo.ps1
```

On macOS or Linux:

```bash
sh scripts/demo.sh
```

The current sample is useful for CLI smoke testing, but it may report `needs_review` rather than `usable` because historical dependency and test behavior can drift. Replace `examples/tasks.yaml` with a known-good PR task once one verifies cleanly.

## CLI Commands

### `forge explore`

Searches public GitHub pull requests for likely task candidates. The explorer is intentionally heuristic: it finds merged Python PRs with bug/fix/test signals, ranks them, and can write a YAML config for the normal `fetch -> verify -> package -> report` pipeline.

```bash
forge explore --limit 10 --output .forge/candidates.yaml
```

Useful options:

- `--query "is:pr is:merged language:Python pytest regression"` to control GitHub search.
- `--test-command "pytest"` to set the generated task command.
- `--timeout-seconds 300` to set the generated timeout.

Exploration does not prove a task is usable. It narrows the search space; `forge verify` remains the gate that checks patch application, failing pre-patch tests, passing post-patch tests, Docker build success, and deterministic reruns.

An LLM can be useful in this discovery loop, but it should stay advisory. Good uses include reading PR titles/bodies/diffs to prioritize candidates, spotting likely documentation-only changes, suggesting a narrower test command, and explaining why a candidate may fail verification. The source of truth remains executable verification: patch application, Docker build, tests before/after, and deterministic reruns. To keep the MVP local and reproducible, `forge explore` does not require an LLM or any cloud service.

#### Optional LLM-Assisted Candidate Review

`forge explore` can optionally ask an LLM to review and rerank candidates after the normal GitHub heuristic search. This is useful when exploration returns many plausible PRs and you want to spend Docker verification time on the best few.

The LLM is advisory only. It never decides reward, task validity, or pass/fail status. `forge verify` remains the source of truth.

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Configure exactly one provider.

For Azure OpenAI:

```bash
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_API_VERSION=2024-10-21
```

For Gemini:

```bash
GEMINI_API_KEY=your-api-key
GEMINI_MODEL=gemini-1.5-flash
```

3. Validate the local configuration without printing secrets:

```bash
forge llm-check --env-file .env
```

Use an explicit provider if needed:

```bash
forge llm-check --provider azure-openai --env-file .env
forge llm-check --provider gemini --env-file .env
```

4. Run exploration with LLM review:

```bash
forge explore \
  --query "is:pr is:merged language:Python pytest regression" \
  --limit 10 \
  --llm-review \
  --llm-provider auto \
  --env-file .env \
  --output .forge/candidates.yaml
```

Provider selection can be explicit:

```bash
forge explore --llm-review --llm-provider azure-openai --output .forge/candidates.yaml
forge explore --llm-review --llm-provider gemini --output .forge/candidates.yaml
```

5. Verify the candidates normally:

```bash
forge fetch .forge/candidates.yaml
forge verify <task_id>
forge report <task_id>
```

Keep `.env` local. It is already ignored by the repository template and should never be committed.

### `forge fetch <config.yaml>`

- Clones each repository into `.forge/repos/<task_id>`.
- Fetches pull request metadata from the public GitHub API.
- Identifies the base and head commits.
- Writes `.forge/tasks/<task_id>/metadata.json`.
- Writes `.forge/tasks/<task_id>/gold.patch`.

### `forge verify <task_id>`

- Checks out the base commit.
- Checks whether the gold patch applies cleanly.
- Runs the configured test command in Docker before the patch.
- Applies the gold patch.
- Runs the configured test command in Docker after the patch.
- Runs the post-patch command a second time as a deterministic rerun check.
- Writes `.forge/tasks/<task_id>/verification.json`.

### `forge package <task_id>`

Creates `taskpacks/<task_id>/` with:

- `task.json`
- `prompt.md`
- `gold.patch`
- `reward.py`
- `Dockerfile`
- `verification.json`
- `repo/` containing the base repository snapshot

The prompt includes the repository name, PR title, PR body, failing test command, and the instruction: "Modify the code so that the tests pass."

### `forge reward <taskpack_path>`

Runs the taskpack's standalone reward script and returns JSON:

```json
{
  "score": 0.0,
  "tests_passed": false,
  "error": null
}
```

For v1, the reward is binary:

- `1.0` if the configured test command exits with code `0`
- `0.0` otherwise

### `forge report <task_id>`

Prints a compact quality report:

- base commit found
- patch applies cleanly
- tests fail before patch
- tests pass after patch
- deterministic rerun status
- environment build status
- recommended status: `usable`, `needs_review`, or `invalid`

### `forge dashboard`

Generates a self-contained local HTML dashboard from `.forge/tasks/*` artifacts:

```bash
forge dashboard --output .forge/dashboard/index.html
```

The dashboard is observational only. It reads metadata, verification results, patch presence, and taskpack presence, then renders summary counts, filters, quality checks, and recorded errors. It does not run tests or mutate repositories.

## Docker Execution

Generated task Dockerfiles use `python:3.11-slim` and this install order:

1. `pip install -e .` if `pyproject.toml`, `setup.py`, or `setup.cfg` exists
2. `pip install -r requirements.txt` if `requirements.txt` exists
3. no install step if neither exists

The configured test command is run with a timeout. Docker is deliberately local-only; there is no cloud dependency.

## Current Limitations

- Python-only Dockerfile generation.
- No hidden tests or leakage detection.
- No agent rollout orchestration.
- No model training, GRPO, LoRA, or fine-tuning pipeline.
- Public GitHub API rate limits apply when unauthenticated.
- Historical repositories can have old dependency constraints that no longer install cleanly.
- Exploration is heuristic and can surface PRs that are documentation-only, flaky, or not reproducible.
- Test determinism is approximated by a second post-patch run in the same generated environment.

## Roadmap

- Add language-specific runners beyond Python.
- Add dependency lockfile capture for stronger reproducibility.
- Add taskpack manifests for dataset indexing.
- Add optional GitHub token support for higher API limits.
- Add richer quality scoring and flakiness diagnostics.
- Add batch task generation and filtering.
- Add deeper repository probing for explorer candidates before verification.
- Add optional LLM-assisted candidate review behind an explicit user-provided provider or local model.
- Add hooks for agent rollout systems while keeping reward execution independent.

## Design Principle

This project is not just a benchmark wrapper. The useful unit is a reproducible environment with task creation, verification, reward, quality reporting, and packaging. That unit can later plug into small-scale training loops, curriculum builders, or evaluation harnesses, but the MVP keeps the center of gravity on correctness and reproducibility.