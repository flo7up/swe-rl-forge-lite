# Contributing

Thanks for helping make `swe-rl-forge-lite` more useful for reproducible SWE agent evaluation.

## Local Setup

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Docker is required for `forge verify`, `forge package`, and `forge reward` workflows.

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
- Keep generated `.forge/` and `taskpacks/` artifacts out of commits.
- Update the README when commands or workflow expectations change.
- Prefer small, reviewable changes over broad refactors.