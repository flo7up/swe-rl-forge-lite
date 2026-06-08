from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge.quality_report import recommend_status
from forge.task_builder import TASKPACKS_DIR, TASKS_DIR, gold_patch_path, verification_path
from forge.task_schema import TaskMetadata, VerificationResult


DEFAULT_DASHBOARD_PATH = Path(".forge") / "dashboard" / "index.html"


class DashboardTask(BaseModel):
    """A task row for the local observer dashboard."""

    model_config = ConfigDict(extra="forbid")

    id: str
    repo_name: str
    repo_url: str
    pr_number: int
    pr_title: str
    test_command: str
    language: str
    timeout_seconds: int
    base_commit: str
    head_commit: str
    created_at: str
    has_patch: bool
    has_verification: bool
    has_taskpack: bool
    lifecycle_stage: str
    recommended_status: str
    checks: dict[str, bool | None] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    run_durations: dict[str, float | None] = Field(default_factory=dict)


def collect_dashboard_tasks(root: Path | None = None) -> list[DashboardTask]:
    """Collect local forge task artifacts for dashboard rendering."""

    root = root or Path.cwd()
    tasks_root = root / TASKS_DIR
    if not tasks_root.exists():
        return []

    tasks: list[DashboardTask] = []
    for metadata_file in sorted(tasks_root.glob("*/metadata.json")):
        try:
            metadata = TaskMetadata.read_json(metadata_file)
        except ValueError:
            continue
        verification = _read_verification(root, metadata.id)
        tasks.append(_dashboard_task(root, metadata, verification))
    return tasks


def write_dashboard(output_path: Path | None = None, *, root: Path | None = None) -> Path:
    """Write a self-contained HTML dashboard and return its path."""

    root = root or Path.cwd()
    output_path = output_path or root / DEFAULT_DASHBOARD_PATH
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_dashboard_html(collect_dashboard_tasks(root)), encoding="utf-8")
    return output_path


def render_dashboard_html(tasks: list[DashboardTask]) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps([task.model_dump(mode="json") for task in tasks], ensure_ascii=False)
    escaped_payload = html.escape(payload, quote=False)
    total = len(tasks)
    usable = sum(1 for task in tasks if task.recommended_status == "usable")
    needs_review = sum(1 for task in tasks if task.recommended_status == "needs_review")
    invalid = sum(1 for task in tasks if task.recommended_status == "invalid")
    unverified = sum(1 for task in tasks if task.recommended_status == "unverified")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>swe-rl-forge-lite dashboard</title>
  <script>
  (() => {{
    const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
    const theme =
      param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  }})();
  </script>
  <style>
:root {{
  color-scheme: light;
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #919191;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-hover: #9a1a41;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
  --cp-success: #16a34a;
  --cp-danger: #dc2626;
  --cp-warning: #f59e0b;
  --cp-link: #0078d4;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
  --cp-overlay: rgba(255, 255, 255, 0.8);
  --cp-panel: rgba(255, 255, 255, 0.86);
  --cp-panel-strong: rgba(255, 255, 255, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.55);
  --cp-highlight: rgba(177, 31, 75, 0.12);
}}
html[data-theme="dark"] {{
  color-scheme: dark;
  --cp-bg: #3d3b3a;
  --cp-bg-elevated: #343231;
  --cp-surface: #292929;
  --cp-surface-soft: #2e2e2e;
  --cp-border: #474747;
  --cp-border-strong: #5f5f5f;
  --cp-text: #dedede;
  --cp-text-muted: #919191;
  --cp-text-soft: #b0b0b0;
  --cp-accent: #fd8ea1;
  --cp-accent-hover: #fb7b91;
  --cp-accent-soft: rgba(253, 142, 161, 0.14);
  --cp-accent-fg: #1a1a1a;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #4da6ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
  --cp-overlay: rgba(41, 41, 41, 0.88);
  --cp-panel: rgba(41, 41, 41, 0.72);
  --cp-panel-strong: rgba(41, 41, 41, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(253, 142, 161, 0.12);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  min-height: 100vh;
  background: linear-gradient(var(--cp-bg), var(--cp-bg-elevated));
  color: var(--cp-text);
  font-family: "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif;
}}
main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 48px; }}
header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 24px; }}
h1 {{ margin: 0; font-size: 2.5rem; line-height: 1; letter-spacing: 0; }}
.subtle {{ color: var(--cp-text-muted); }}
.mono {{ font-family: Consolas, "Courier New", Courier, monospace; }}
.summary {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
.metric {{ background: var(--cp-surface); border: 1px solid var(--cp-border); border-radius: 16px; padding: 16px; box-shadow: var(--cp-shadow); }}
.metric span {{ display: block; color: var(--cp-text-muted); font-size: 0.82rem; }}
.metric strong {{ display: block; margin-top: 8px; font-size: 1.7rem; }}
.toolbar {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin: 20px 0; }}
input, select {{ background: var(--cp-surface); color: var(--cp-text); border: 1px solid var(--cp-border); border-radius: 0.625rem; padding: 10px 12px; font: inherit; min-height: 42px; }}
input {{ flex: 1 1 280px; }}
select {{ flex: 0 1 200px; }}
.grid {{ display: grid; gap: 14px; }}
.task {{ background: var(--cp-surface); border: 1px solid var(--cp-border); border-radius: 16px; padding: 18px; box-shadow: var(--cp-shadow); }}
.task-head {{ display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: start; }}
.task h2 {{ margin: 0 0 8px; font-size: 1.05rem; letter-spacing: 0; }}
.meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--cp-text-muted); font-size: 0.9rem; }}
.badge {{ display: inline-flex; align-items: center; border: 1px solid var(--cp-border); border-radius: 0.625rem; padding: 4px 8px; background: var(--cp-surface-soft); color: var(--cp-text); font-size: 0.82rem; }}
.usable {{ color: var(--cp-success); border-color: var(--cp-success); }}
.needs_review, .unverified {{ color: var(--cp-warning); border-color: var(--cp-warning); }}
.invalid {{ color: var(--cp-danger); border-color: var(--cp-danger); }}
.checks {{ display: grid; grid-template-columns: repeat(7, minmax(120px, 1fr)); gap: 8px; margin-top: 14px; }}
.check {{ border: 1px solid var(--cp-border); border-radius: 0.625rem; padding: 10px; background: var(--cp-surface-soft); }}
.check b {{ display: block; font-size: 0.78rem; color: var(--cp-text-muted); margin-bottom: 6px; }}
.yes {{ color: var(--cp-success); }}
.no {{ color: var(--cp-danger); }}
.unknown {{ color: var(--cp-text-muted); }}
details {{ margin-top: 12px; }}
summary {{ cursor: pointer; color: var(--cp-link); }}
pre {{ overflow: auto; white-space: pre-wrap; border: 1px solid var(--cp-border); border-radius: 0.625rem; padding: 12px; background: var(--cp-surface-soft); color: var(--cp-text); font-family: Consolas, "Courier New", Courier, monospace; }}
.empty {{ padding: 28px; border: 1px solid var(--cp-border); border-radius: 16px; background: var(--cp-surface); }}
@media (max-width: 860px) {{
  header, .task-head {{ grid-template-columns: 1fr; display: grid; }}
  .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .checks {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 520px) {{
  main {{ width: min(100% - 20px, 1180px); padding-top: 20px; }}
  h1 {{ font-size: 2rem; }}
  .summary, .checks {{ grid-template-columns: 1fr; }}
}}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>swe-rl-forge-lite</h1>
        <div class="subtle">Local task forge observer</div>
      </div>
      <div class="subtle mono">generated {html.escape(generated_at)}</div>
    </header>
    <section class="summary" aria-label="Task summary">
      <div class="metric"><span>Total</span><strong>{total}</strong></div>
      <div class="metric"><span>Usable</span><strong>{usable}</strong></div>
      <div class="metric"><span>Needs review</span><strong>{needs_review}</strong></div>
      <div class="metric"><span>Invalid</span><strong>{invalid}</strong></div>
      <div class="metric"><span>Unverified</span><strong>{unverified}</strong></div>
    </section>
    <section class="toolbar" aria-label="Task filters">
      <input id="search" type="search" placeholder="Search task, repository, title, command" aria-label="Search tasks">
      <select id="status" aria-label="Filter by status">
        <option value="all">All statuses</option>
        <option value="usable">Usable</option>
        <option value="needs_review">Needs review</option>
        <option value="invalid">Invalid</option>
        <option value="unverified">Unverified</option>
      </select>
    </section>
    <section id="tasks" class="grid" aria-live="polite"></section>
  </main>
  <script id="dashboard-data" type="application/json">{escaped_payload}</script>
  <script>
    const tasks = JSON.parse(document.getElementById("dashboard-data").textContent);
    const container = document.getElementById("tasks");
    const search = document.getElementById("search");
    const status = document.getElementById("status");

    function text(value) {{
      return String(value ?? "").replace(/[&<>\"]/g, char => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}}[char]));
    }}

    function check(label, value) {{
      const rendered = value === true ? "yes" : value === false ? "no" : "unknown";
      return `<div class="check"><b>${{text(label)}}</b><span class="${{rendered}}">${{rendered}}</span></div>`;
    }}

    function card(task) {{
      const checks = task.checks || {{}};
      const errors = (task.errors || []).map(error => `- ${{text(error)}}`).join("\n") || "No recorded errors.";
      return `<article class="task" data-status="${{text(task.recommended_status)}}">
        <div class="task-head">
          <div>
            <h2>${{text(task.id)}} <span class="subtle">${{text(task.repo_name)}}#${{text(task.pr_number)}}</span></h2>
            <div>${{text(task.pr_title)}}</div>
            <div class="meta">
              <span class="mono">${{text(task.test_command)}}</span>
              <span>${{text(task.language)}}</span>
              <span>${{text(task.lifecycle_stage)}}</span>
            </div>
          </div>
          <div class="badge ${{text(task.recommended_status)}}">${{text(task.recommended_status)}}</div>
        </div>
        <div class="checks">
          ${{check("base commit", checks.base_commit_found)}}
          ${{check("patch", checks.patch_applies)}}
          ${{check("fails before", checks.tests_fail_before_patch)}}
          ${{check("passes after", checks.tests_pass_after_patch)}}
          ${{check("rerun", checks.deterministic_rerun_success)}}
          ${{check("docker", checks.docker_build_success)}}
          ${{check("test env", checks.test_environment_success)}}
        </div>
        <details>
          <summary>Details</summary>
          <pre>base: ${{text(task.base_commit)}}\nhead: ${{text(task.head_commit)}}\npatch: ${{text(task.has_patch)}}\ntaskpack: ${{text(task.has_taskpack)}}\n\n${{errors}}</pre>
        </details>
      </article>`;
    }}

    function render() {{
      const term = search.value.trim().toLowerCase();
      const selected = status.value;
      const filtered = tasks.filter(task => {{
        const matchesStatus = selected === "all" || task.recommended_status === selected;
        const haystack = `${{task.id}} ${{task.repo_name}} ${{task.pr_title}} ${{task.test_command}}`.toLowerCase();
        return matchesStatus && (!term || haystack.includes(term));
      }});
      container.innerHTML = filtered.length ? filtered.map(card).join("") : `<div class="empty">No matching task artifacts.</div>`;
    }}

    search.addEventListener("input", render);
    status.addEventListener("change", render);
    render();
  </script>
</body>
</html>
"""


def _read_verification(root: Path, task_id: str) -> VerificationResult | None:
    path = verification_path(root, task_id)
    if not path.exists():
        return None
    try:
        return VerificationResult.read_json(path)
    except ValueError:
        return None


def _dashboard_task(root: Path, metadata: TaskMetadata, verification: VerificationResult | None) -> DashboardTask:
    has_verification = verification is not None
    has_taskpack = (root / TASKPACKS_DIR / metadata.id).exists()
    lifecycle_stage = "packaged" if has_taskpack else "verified" if has_verification else "fetched"
    status = recommend_status(verification) if verification else "unverified"
    checks: dict[str, bool | None] = {
        "base_commit_found": None,
        "patch_applies": None,
        "tests_fail_before_patch": None,
        "tests_pass_after_patch": None,
        "docker_build_success": None,
        "test_environment_success": None,
        "deterministic_rerun_success": None,
    }
    errors: list[str] = []
    durations: dict[str, float | None] = {
        "before_patch": None,
        "after_patch": None,
        "deterministic_rerun": None,
    }
    if verification:
        checks = {
            "base_commit_found": verification.base_commit_found,
            "patch_applies": verification.patch_applies,
            "tests_fail_before_patch": verification.tests_fail_before_patch,
            "tests_pass_after_patch": verification.tests_pass_after_patch,
            "docker_build_success": verification.docker_build_success,
            "test_environment_success": verification.test_environment_success,
            "deterministic_rerun_success": verification.deterministic_rerun_success,
        }
        errors = verification.errors
        durations = {
            "before_patch": verification.before_patch.duration_seconds if verification.before_patch else None,
            "after_patch": verification.after_patch.duration_seconds if verification.after_patch else None,
            "deterministic_rerun": verification.deterministic_rerun.duration_seconds if verification.deterministic_rerun else None,
        }

    return DashboardTask(
        id=metadata.id,
        repo_name=metadata.repo_name,
        repo_url=metadata.repo_url,
        pr_number=metadata.pr_number,
        pr_title=metadata.pr_title,
        test_command=metadata.test_command,
        language=metadata.language,
        timeout_seconds=metadata.timeout_seconds,
        base_commit=metadata.base_commit,
        head_commit=metadata.head_commit,
        created_at=metadata.created_at.isoformat(),
        has_patch=gold_patch_path(root, metadata.id).exists(),
        has_verification=has_verification,
        has_taskpack=has_taskpack,
        lifecycle_stage=lifecycle_stage,
        recommended_status=status,
        checks=checks,
        errors=errors,
        run_durations=durations,
    )