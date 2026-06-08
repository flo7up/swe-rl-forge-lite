# Contributing

Thanks for helping make `swe-rl-forge-lite` more useful for reproducible SWE agent evaluation.

## Local Setup

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Docker is required for `forge verify`, `forge package`, and `forge reward` workflows.

## Project Orientation

The product is the reproducible task pipeline: `fetch -> verify -> package -> reward -> report -> dashboard`. Keep changes aligned with that path.

- CLI commands live in `forge/cli.py`.
- Task metadata and validation live in `forge/task_schema.py`.
- PR ingestion and task construction live in `forge/task_builder.py`.
- Docker execution lives in `forge/docker_runner.py`.
- Verification and reward execution live in `forge/reward_runner.py`.
- Quality labels live in `forge/quality_report.py`.
- Static and live dashboards live in `forge/dashboard.py`, `forge/dashboard_live.py`, and `frontend/`.

When adding a feature, update every user-facing surface that needs to know about it: CLI help, README examples, dashboard copy, tests, and configuration docs. A new flag or behavior is incomplete if users can only discover it by reading implementation code.

## Good First Contributions

- Add a public PR task that verifies as `usable`.
- Improve Dockerfile generation for common Python project layouts.
- Add focused tests for task packaging and dashboard rendering.
- Improve exploration heuristics without making an LLM or cloud provider required.
- Document reproducibility failure modes for historical repositories.

## Task Candidate Guidelines

High-quality task candidates should have:

- a public GitHub repository
- a merged pull request with a small, focused patch
- dependencies that still install in `python:3.11-slim`
- a test command that fails before the patch and passes after the patch
- deterministic rerun success
- no required cloud services or private credentials

Use:

```bash
forge explore --limit 20 --output .forge/candidates.yaml
forge fetch .forge/candidates.yaml
forge verify <task_id>
forge report <task_id>
```

If GitHub API rate limits interrupt exploration, set `GITHUB_TOKEN` or `GH_TOKEN` in your terminal. Tokens are optional and should never be committed.

## Pull Request Checklist

- Run `python -m pytest`.
- For frontend changes, run `npm --prefix frontend run build`.
- For launcher changes, parse `start.ps1` with PowerShell before opening a PR.
- Keep generated `.forge/` and `taskpacks/` artifacts out of commits.
- Update the README when commands or workflow expectations change.
- Prefer small, reviewable changes over broad refactors.