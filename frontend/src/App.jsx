import { useEffect, useMemo, useState } from "react";

const POLL_MS = 2000;
const DEFAULT_CONFIG_PATH = "examples/tasks.yaml";

const STATUS_ORDER = ["usable", "needs_review", "invalid", "unverified"];

const STATUS_HELP = {
  usable: "Verified successfully and packaged into a taskpack that can be used by coding-agent training or evaluation workflows.",
  needs_review: "The task has enough artifacts to inspect, but at least one quality signal needs human review before using it for training.",
  invalid: "A quality gate failed, such as Docker, test environment, patch application, or expected before/after behavior.",
  unverified: "Fetched task metadata exists, but verification has not been run yet.",
};

const CHECK_HELP = {
  base: "Whether Forge could find and check out the historical base commit for this task.",
  patch: "Whether the PR patch applies cleanly on top of the base commit.",
  "fails before": "The test should fail before the patch; that proves the task captures a real regression or missing fix.",
  "passes after": "The same test should pass after applying the gold patch.",
  rerun: "The post-patch test is run again to catch flaky or nondeterministic tasks.",
  docker: "Whether Docker built the isolated test image used for verification.",
  "test env": "Whether failures look like meaningful test failures rather than missing tools or broken infrastructure.",
};

const METRIC_HELP = {
  total: "All task artifacts currently visible to the dashboard.",
  usable: STATUS_HELP.usable,
  "needs review": STATUS_HELP.needs_review,
  invalid: STATUS_HELP.invalid,
  unverified: STATUS_HELP.unverified,
};

const PANEL_HELP = {
  Repository: "What kind of source project this task came from, inferred from files like setup.py, pyproject.toml, or package.json.",
  "Change Context": "The historical pull request, commits, and files that define the code change being turned into a task.",
  "Training Package": "The emitted taskpack directory. It contains the prompt, base repo snapshot, gold patch, verification record, Dockerfile, and reward script used downstream.",
};

const PROCESS_STEPS = [
  {
    name: "Fetch",
    help: "Clone or update the source repository, download PR metadata, and save the gold patch under .forge/tasks.",
  },
  {
    name: "Verify",
    help: "Run the configured test in Docker before the patch, after the patch, and once more after the patch to confirm deterministic behavior.",
  },
  {
    name: "Package",
    help: "Create a taskpack containing the base repository snapshot, prompt, task metadata, Dockerfile, reward script, verification record, and gold patch.",
  },
];

const DEFAULT_TASKPACK_FILES = ["Dockerfile", "gold.patch", "prompt.md", "repo/", "reward.py", "task.json", "verification.json"];

function toLabel(value) {
  return value.replaceAll("_", " ");
}

function boolLabel(value) {
  if (value === true) {
    return "yes";
  }
  if (value === false) {
    return "no";
  }
  return "unknown";
}

function statusColor(status) {
  switch (status) {
    case "usable":
      return "text-emerald-300 border-emerald-400/40 bg-emerald-400/10";
    case "needs_review":
      return "text-amber-300 border-amber-300/40 bg-amber-300/10";
    case "invalid":
      return "text-rose-300 border-rose-300/40 bg-rose-300/10";
    default:
      return "text-slate-300 border-slate-300/30 bg-slate-300/10";
  }
}

function shortSha(value) {
  return value ? value.slice(0, 12) : "unknown";
}

function compactText(value, fallback = "None provided") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function taskpackPath(task) {
  return task.taskpack_path || (task.has_taskpack ? `taskpacks/${task.id}` : "");
}

function taskpackFiles(task) {
  const files = task.taskpack_files || [];
  return files.length ? files : task.has_taskpack ? DEFAULT_TASKPACK_FILES : [];
}

function jobStatusColor(status) {
  switch (status) {
    case "running":
      return "text-cyan-200 border-cyan-300/40 bg-cyan-400/10";
    case "succeeded":
      return "text-emerald-200 border-emerald-300/40 bg-emerald-400/10";
    case "failed":
      return "text-rose-200 border-rose-300/40 bg-rose-400/10";
    default:
      return "text-slate-200 border-slate-300/30 bg-slate-400/10";
  }
}

function queueStatusColor(status) {
  switch (status) {
    case "running":
      return "border-cyan-300/30 bg-cyan-400/10 text-cyan-100";
    case "packaged":
      return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
    case "skipped":
      return "border-amber-300/30 bg-amber-400/10 text-amber-100";
    case "failed":
      return "border-rose-300/30 bg-rose-400/10 text-rose-100";
    default:
      return "border-slate-300/20 bg-slate-400/10 text-slate-200";
  }
}

function taskOptionLabel(task) {
  const pr = task.pr_number ? `#${task.pr_number}` : "";
  const repo = task.repo_name ? ` · ${task.repo_name}${pr}` : "";
  const title = task.pr_title ? ` · ${task.pr_title}` : "";
  return `${task.id}${repo}${title}`;
}

function verificationSeconds(task) {
  return Object.values(task.run_durations || {}).reduce((total, value) => total + (typeof value === "number" ? value : 0), 0);
}

function formatSeconds(value) {
  if (!Number.isFinite(value) || value <= 0) {
    return "-";
  }
  return `${Math.round(value)}s`;
}

function average(values) {
  const usableValues = values.filter((value) => Number.isFinite(value) && value > 0);
  if (!usableValues.length) {
    return 0;
  }
  return usableValues.reduce((total, value) => total + value, 0) / usableValues.length;
}

function complexityLevel(task) {
  const changedFileCount = (task.changed_files || []).length;
  const patchLines = task.patch_line_count || 0;
  const repoFiles = task.taskpack_repo_file_count || 0;
  const duration = verificationSeconds(task);
  const score = changedFileCount * 2 + patchLines / 80 + repoFiles / 1200 + duration / 90;
  if (score >= 10) {
    return "high";
  }
  if (score >= 4) {
    return "medium";
  }
  return "low";
}

function complexityColor(level) {
  switch (level) {
    case "high":
      return "border-rose-300/30 bg-rose-400/10 text-rose-100";
    case "medium":
      return "border-amber-300/30 bg-amber-400/10 text-amber-100";
    default:
      return "border-emerald-300/30 bg-emerald-400/10 text-emerald-100";
  }
}

async function readJsonResponse(response, name) {
  const contentType = response.headers.get("content-type") || "";
  const body = await response.text();
  if (!contentType.includes("application/json")) {
    throw new Error(`${name} returned ${contentType || "non-JSON content"}. Check that the frontend is pointed at a current dashboard-live API server.`);
  }
  const data = JSON.parse(body);
  if (!response.ok) {
    throw new Error(data.error || `${name} returned ${response.status}`);
  }
  return data;
}

function CheckPill({ help, name, value }) {
  const rendered = boolLabel(value);
  const color = rendered === "yes" ? "text-emerald-300" : rendered === "no" ? "text-rose-300" : "text-slate-300";
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-slate-400">
        <HelpLabel help={help}>{name}</HelpLabel>
      </div>
      <div className={`font-mono text-sm ${color}`}>{rendered}</div>
    </div>
  );
}

function InfoTip({ text }) {
  return (
    <span className="group relative inline-flex align-middle">
      <span
        aria-label={text}
        className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-cyan-300/40 bg-cyan-300/10 text-[0.65rem] font-bold leading-none text-cyan-100 outline-none ring-cyan-300 transition hover:bg-cyan-300/20 focus:ring-2"
        role="img"
        tabIndex={0}
      >
        i
      </span>
      <span className="pointer-events-none absolute left-1/2 top-6 z-50 hidden w-64 -translate-x-1/2 rounded-lg border border-cyan-200/20 bg-slate-950/95 px-3 py-2 text-left text-xs normal-case leading-5 tracking-normal text-slate-100 shadow-2xl shadow-black/40 group-hover:block group-focus-within:block">
        {text}
      </span>
    </span>
  );
}

function ProcessGuide() {
  return (
    <section className="mb-6 rounded-2xl border border-white/10 bg-black/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <HelpLabel className="mr-1 text-xs font-semibold uppercase tracking-widest text-slate-400" help="The high-level Forge pipeline for turning a historical PR into a taskpack.">Process</HelpLabel>
        {PROCESS_STEPS.map((step) => (
          <div className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-black/25 px-3 py-1 text-xs font-semibold text-slate-200" key={step.name}>
            <span>{step.name}</span>
            <InfoTip text={step.help} />
          </div>
        ))}
      </div>
    </section>
  );
}

function HelpLabel({ children, className = "", help }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <span>{children}</span>
      {help ? <InfoTip text={help} /> : null}
    </span>
  );
}

export default function App() {
  const [snapshot, setSnapshot] = useState({ summary: {}, tasks: [], generated_at: null });
  const [control, setControl] = useState({ enabled: false, job: null });
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [error, setError] = useState("");
  const [controlError, setControlError] = useState("");
  const [manualTaskId, setManualTaskId] = useState("");
  const [configPath, setConfigPath] = useState(DEFAULT_CONFIG_PATH);
  const [controlPending, setControlPending] = useState("");
  const [packageSearch, setPackageSearch] = useState("");
  const [packageStatus, setPackageStatus] = useState("all");
  const [lastTick, setLastTick] = useState(Date.now());

  useEffect(() => {
    let mounted = true;

    async function pull() {
      try {
        const response = await fetch("/api/tasks", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`API returned ${response.status}`);
        }
        const data = await readJsonResponse(response, "Task API");
        if (!mounted) {
          return;
        }
        setSnapshot(data);
        setError("");
        setLastTick(Date.now());
      } catch (exc) {
        if (!mounted) {
          return;
        }
        setError(String(exc));
      }

      try {
        const response = await fetch("/api/control/status", { cache: "no-store" });
        const data = await readJsonResponse(response, "Control API");
        if (!mounted) {
          return;
        }
        setControl(data);
        setControlError("");
      } catch (exc) {
        if (!mounted) {
          return;
        }
        setControl({ enabled: false, job: null });
        setControlError(`Control API: ${String(exc)}`);
      }
    }

    pull();
    const id = setInterval(pull, POLL_MS);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (snapshot.tasks || []).filter((task) => {
      if (status !== "all" && task.recommended_status !== status) {
        return false;
      }
      if (!term) {
        return true;
      }
      const haystack = `${task.id} ${task.repo_name} ${task.pr_title} ${task.pr_body || ""} ${task.repo_kind || ""} ${task.test_command} ${taskpackPath(task)} ${(task.changed_files || []).join(" ")} ${taskpackFiles(task).join(" ")}`.toLowerCase();
      return haystack.includes(term);
    });
  }, [snapshot.tasks, search, status]);

  const selectedTaskId = (snapshot.tasks || []).some((task) => task.id === manualTaskId) ? manualTaskId : snapshot.tasks?.[0]?.id || "";
  const jobRunning = control.job?.status === "running";

  async function postControl(path, payload, pendingName) {
    setControlPending(pendingName);
    setControlError("");
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(response, "Control API");
      setControl(data);
    } catch (exc) {
      setControlError(String(exc));
    } finally {
      setControlPending("");
    }
  }

  function startManualRun() {
    if (!selectedTaskId) {
      setControlError("No task is available for a manual run.");
      return;
    }
    postControl("/api/control/manual", { task_id: selectedTaskId }, "manual");
  }

  function startAutoRun() {
    postControl("/api/control/auto", { config_path: configPath }, "auto");
  }

  const generatedAt = snapshot.generated_at ? new Date(snapshot.generated_at).toLocaleTimeString() : "-";
  const lastSeenAge = Math.max(0, Math.round((Date.now() - lastTick) / 1000));

  return (
    <div className="min-h-screen bg-forge-gradient text-slate-100">
      <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <header className="mb-8 animate-riseIn rounded-3xl border border-white/10 bg-black/25 p-6 backdrop-blur-md">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-cyan-300/40 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-cyan-200">
                <span className="inline-block h-2 w-2 animate-pulseDot rounded-full bg-cyan-300" />
                Live observer
              </div>
              <h1 className="font-display text-4xl font-semibold leading-tight sm:text-5xl">Forge Process Monitor</h1>
              <p className="mt-2 max-w-2xl text-sm text-slate-300 sm:text-base">
                Real-time view of fetched, verified, and packaged tasks while your forge pipeline runs.
              </p>
            </div>
            <div className="rounded-2xl border border-white/10 bg-black/25 px-4 py-3 text-right text-xs text-slate-300">
              <div className="font-mono">snapshot {generatedAt}</div>
              <div className="font-mono">last poll {lastSeenAge}s ago</div>
            </div>
          </div>
        </header>

        <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <MetricCard help={METRIC_HELP.total} label="total" value={snapshot.summary?.total ?? 0} tone="slate" />
          <MetricCard help={METRIC_HELP.usable} label="usable" value={snapshot.summary?.usable ?? 0} tone="emerald" />
          <MetricCard help={METRIC_HELP["needs review"]} label="needs review" value={snapshot.summary?.needs_review ?? 0} tone="amber" />
          <MetricCard help={METRIC_HELP.invalid} label="invalid" value={snapshot.summary?.invalid ?? 0} tone="rose" />
          <MetricCard help={METRIC_HELP.unverified} label="unverified" value={snapshot.summary?.unverified ?? 0} tone="sky" />
        </section>

        <ProcessGuide />

        <ControlPanel
          control={control}
          controlError={controlError}
          controlPending={controlPending}
          configPath={configPath}
          jobRunning={jobRunning}
          onConfigPathChange={setConfigPath}
          onManualTaskChange={setManualTaskId}
          onStartAuto={startAutoRun}
          onStartManual={startManualRun}
          selectedTaskId={selectedTaskId}
          tasks={snapshot.tasks || []}
        />

        <PackageInventory
          packageSearch={packageSearch}
          packageStatus={packageStatus}
          onPackageSearchChange={setPackageSearch}
          onPackageStatusChange={setPackageStatus}
          tasks={snapshot.tasks || []}
        />

        <section className="mb-6 flex flex-wrap gap-3 rounded-2xl border border-white/10 bg-black/20 p-3">
          <input
            className="min-w-[16rem] flex-1 rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none ring-cyan-300 transition focus:ring-2"
            placeholder="Search by task, repository, title, command"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none ring-cyan-300 transition focus:ring-2"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">all statuses</option>
            {STATUS_ORDER.map((item) => (
              <option value={item} key={item}>
                {toLabel(item)}
              </option>
            ))}
          </select>
        </section>

        {error ? (
          <div className="mb-4 rounded-xl border border-rose-400/40 bg-rose-500/10 p-3 text-sm text-rose-200">{error}</div>
        ) : null}

        <section className="grid gap-4">
          {filtered.length === 0 ? (
            <div className="rounded-2xl border border-white/10 bg-black/20 p-6 text-center text-slate-300">
              No matching task artifacts.
            </div>
          ) : (
            filtered.map((task, index) => (
              <article
                key={task.id}
                className="animate-riseIn rounded-2xl border border-white/10 bg-black/25 p-4 backdrop-blur-sm"
                style={{ animationDelay: `${Math.min(index * 30, 240)}ms` }}
              >
                <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="font-display text-xl">{task.id}</h2>
                    <div className="text-sm text-slate-300">
                      {task.repo_name}#{task.pr_number} - {task.pr_title}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-300">
                      <span className="rounded-full border border-white/10 bg-black/20 px-2 py-1 font-mono">{task.test_command}</span>
                      <span className="rounded-full border border-white/10 bg-black/20 px-2 py-1">{task.language}</span>
                      <span className="rounded-full border border-white/10 bg-black/20 px-2 py-1">{task.lifecycle_stage}</span>
                    </div>
                  </div>
                  <div className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${statusColor(task.recommended_status)}`}>
                    <HelpLabel help={STATUS_HELP[task.recommended_status]}>{toLabel(task.recommended_status)}</HelpLabel>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                  <CheckPill help={CHECK_HELP.base} name="base" value={task.checks?.base_commit_found} />
                  <CheckPill help={CHECK_HELP.patch} name="patch" value={task.checks?.patch_applies} />
                  <CheckPill help={CHECK_HELP["fails before"]} name="fails before" value={task.checks?.tests_fail_before_patch} />
                  <CheckPill help={CHECK_HELP["passes after"]} name="passes after" value={task.checks?.tests_pass_after_patch} />
                  <CheckPill help={CHECK_HELP.rerun} name="rerun" value={task.checks?.deterministic_rerun_success} />
                  <CheckPill help={CHECK_HELP.docker} name="docker" value={task.checks?.docker_build_success} />
                  <CheckPill help={CHECK_HELP["test env"]} name="test env" value={task.checks?.test_environment_success} />
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-3">
                  <InfoPanel help={PANEL_HELP.Repository} title="Repository">
                    <MetadataValue help="The inferred project type. For this task, setup.py marks the Click repository as a Python package." label="kind" value={task.repo_kind || task.language} />
                    <MetadataValue help="The upstream repository Forge fetched from." label="source" value={task.repo_url} mono />
                    <MetadataValue help="The language configured for this task." label="language" value={task.language} />
                  </InfoPanel>

                  <InfoPanel help={PANEL_HELP["Change Context"]} title="Change Context">
                    <MetadataValue help="The historical PR that supplied the task patch and metadata." label="pull request" value={`#${task.pr_number} - ${task.pr_title}`} />
                    <MetadataValue help="The commit checked out before applying the PR patch. The failing test should fail here." label="base commit" value={shortSha(task.base_commit)} mono />
                    <MetadataValue help="The PR head commit when it is available locally. Some fetched repos may only have the base commit and patch." label="head commit" value={`${shortSha(task.head_commit)}${task.head_commit_subject ? ` - ${task.head_commit_subject}` : ""}`} mono />
                    <div className="mt-2 flex flex-wrap gap-2">
                      {(task.changed_files || []).length ? (
                        task.changed_files.map((file) => (
                          <span className="rounded-full border border-white/10 bg-black/20 px-2 py-1 font-mono text-xs text-slate-300" key={file}>{file}</span>
                        ))
                      ) : (
                        <span className="text-xs text-slate-400">{task.has_patch ? "Patch is present; changed-file details require the refreshed API payload." : "No changed files recorded."}</span>
                      )}
                    </div>
                    <details className="mt-2 text-xs text-slate-300">
                      <summary className="cursor-pointer text-slate-200">PR and commit description</summary>
                      <div className="mt-2 space-y-2 whitespace-pre-wrap break-words text-slate-300">
                        <p>{compactText(task.pr_body || task.head_commit_subject, "No PR body or commit message captured in local artifacts.")}</p>
                        {task.head_commit_body ? <p>{task.head_commit_body}</p> : null}
                      </div>
                    </details>
                  </InfoPanel>

                  <InfoPanel help={PANEL_HELP["Training Package"]} title="Training Package">
                    {task.has_taskpack ? (
                      <>
                        <MetadataValue help="Local directory produced by the package step. This is the bundle downstream agent-training code can consume." label="path" value={taskpackPath(task)} mono />
                        <div className="mt-2 flex flex-wrap gap-2">
                          {taskpackFiles(task).map((file) => (
                            <span className="rounded-full border border-emerald-300/20 bg-emerald-400/10 px-2 py-1 font-mono text-xs text-emerald-100" key={file}>{file}</span>
                          ))}
                        </div>
                      </>
                    ) : (
                      <p className="text-xs text-slate-400">No taskpack emitted yet. Run a successful manual or auto job to package this task.</p>
                    )}
                  </InfoPanel>
                </div>

                {task.errors?.length ? (
                  <details className="mt-4 rounded-xl border border-rose-300/30 bg-rose-500/10 p-3 text-sm text-rose-100">
                    <summary className="cursor-pointer font-semibold">errors ({task.errors.length})</summary>
                    <ul className="mt-2 list-disc pl-5">
                      {task.errors.map((entry) => (
                        <li key={entry}>{entry}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </article>
            ))
          )}
        </section>
      </div>
    </div>
  );
}

function PackageInventory({ onPackageSearchChange, onPackageStatusChange, packageSearch, packageStatus, tasks }) {
  const packages = useMemo(() => tasks.filter((task) => task.has_taskpack), [tasks]);
  const filteredPackages = useMemo(() => {
    const term = packageSearch.trim().toLowerCase();
    return packages.filter((task) => {
      if (packageStatus !== "all" && task.recommended_status !== packageStatus) {
        return false;
      }
      if (!term) {
        return true;
      }
      const haystack = `${task.id} ${task.repo_name} ${task.pr_title} ${taskpackPath(task)} ${task.repo_kind || ""} ${(task.changed_files || []).join(" ")}`.toLowerCase();
      return haystack.includes(term);
    });
  }, [packageSearch, packageStatus, packages]);

  const usablePackages = packages.filter((task) => task.recommended_status === "usable").length;
  const reviewPackages = packages.filter((task) => task.recommended_status === "needs_review").length;
  const invalidPackages = packages.filter((task) => task.recommended_status === "invalid").length;
  const avgPatchLines = average(packages.map((task) => task.patch_line_count || 0));
  const avgVerifySeconds = average(packages.map(verificationSeconds));

  return (
    <section className="mb-6 rounded-2xl border border-white/10 bg-black/20 p-4 backdrop-blur-sm">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <HelpLabel className="font-display text-xl font-semibold" help="A taskpack is the produced training/evaluation package: prompt, base repo snapshot, gold patch, Dockerfile, reward script, metadata, and verification record.">Training package inventory</HelpLabel>
          <p className="mt-1 text-sm text-slate-300">Track emitted packages, their quality gate result, and rough complexity before handing them to downstream agent training or evaluation.</p>
        </div>
        <div className="rounded-full border border-white/10 bg-black/25 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-slate-300">
          showing {filteredPackages.length} of {packages.length}
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-5">
        <PackageMetric help="Number of taskpack directories currently present under taskpacks/." label="produced" value={packages.length} />
        <PackageMetric help="Packages whose verification checks make them ready for downstream use." label="usable" value={usablePackages} tone="emerald" />
        <PackageMetric help="Packages that exist but should be inspected or excluded before training." label="review/invalid" value={reviewPackages + invalidPackages} tone={reviewPackages + invalidPackages ? "amber" : "slate"} />
        <PackageMetric help="Average number of lines in the gold patch diff for produced packages." label="avg patch" value={avgPatchLines ? `${Math.round(avgPatchLines)} lines` : "-"} />
        <PackageMetric help="Average total Docker verification time across before, after, and rerun phases." label="avg verify" value={formatSeconds(avgVerifySeconds)} />
      </div>

      <div className="mb-3 flex flex-wrap gap-3">
        <input
          className="min-w-[16rem] flex-1 rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none ring-cyan-300 transition focus:ring-2"
          placeholder="Search packages by id, repo, title, path, changed file"
          value={packageSearch}
          onChange={(event) => onPackageSearchChange(event.target.value)}
        />
        <select
          className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none ring-cyan-300 transition focus:ring-2"
          value={packageStatus}
          onChange={(event) => onPackageStatusChange(event.target.value)}
        >
          <option value="all">all package statuses</option>
          {STATUS_ORDER.map((item) => (
            <option value={item} key={item}>{toLabel(item)}</option>
          ))}
        </select>
      </div>

      {packages.length ? (
        <div className="max-h-[28rem] overflow-auto rounded-xl border border-white/10">
          <div className="grid min-w-[58rem] grid-cols-[1.2fr_1.4fr_0.8fr_0.9fr_1fr_1.2fr] gap-3 border-b border-white/10 bg-black/40 px-3 py-2 text-xs font-semibold uppercase tracking-widest text-slate-400">
            <HelpLabel help="Local taskpack directory and task id.">package</HelpLabel>
            <HelpLabel help="Source repository and historical PR title.">source</HelpLabel>
            <HelpLabel help="Quality recommendation from verification checks.">quality</HelpLabel>
            <HelpLabel help="Rough complexity from changed files, patch size, repo snapshot size, and Docker verification time.">complexity</HelpLabel>
            <HelpLabel help="Patch and repository size signals for estimating task difficulty.">stats</HelpLabel>
            <HelpLabel help="Top-level files emitted in the taskpack.">artifacts</HelpLabel>
          </div>
          <div className="grid min-w-[58rem] divide-y divide-white/10">
            {filteredPackages.map((task) => {
              const level = complexityLevel(task);
              const changedFileCount = (task.changed_files || []).length;
              return (
                <div className="grid grid-cols-[1.2fr_1.4fr_0.8fr_0.9fr_1fr_1.2fr] gap-3 px-3 py-3 text-xs text-slate-300" key={task.id}>
                  <div>
                    <div className="font-mono text-slate-100">{task.id}</div>
                    <div className="mt-1 break-words font-mono text-slate-400">{taskpackPath(task)}</div>
                  </div>
                  <div>
                    <div className="text-slate-100">{task.repo_name}#{task.pr_number}</div>
                    <div className="mt-1 break-words text-slate-400">{task.pr_title}</div>
                  </div>
                  <div>
                    <span className={`inline-flex rounded-full border px-2 py-1 font-semibold uppercase tracking-wider ${statusColor(task.recommended_status)}`}>{toLabel(task.recommended_status)}</span>
                  </div>
                  <div>
                    <span className={`inline-flex rounded-full border px-2 py-1 font-semibold uppercase tracking-wider ${complexityColor(level)}`}>{level}</span>
                    <div className="mt-1 text-slate-400">verify {formatSeconds(verificationSeconds(task))}</div>
                  </div>
                  <div className="space-y-1 font-mono text-slate-300">
                    <div>{changedFileCount || "?"} files</div>
                    <div>+{task.patch_additions || 0}/-{task.patch_deletions || 0}</div>
                    <div>{task.taskpack_repo_file_count ?? "?"} repo files</div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {taskpackFiles(task).slice(0, 6).map((file) => (
                      <span className="rounded-full border border-emerald-300/20 bg-emerald-400/10 px-2 py-0.5 font-mono text-[0.68rem] text-emerald-100" key={file}>{file}</span>
                    ))}
                    {taskpackFiles(task).length > 6 ? <span className="text-slate-400">+{taskpackFiles(task).length - 6}</span> : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-white/10 bg-black/20 p-4 text-sm text-slate-300">No training packages have been produced yet. Run a successful manual or auto job to create taskpacks.</div>
      )}
    </section>
  );
}

function PackageMetric({ help, label, tone = "slate", value }) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-300/30 bg-emerald-400/10 text-emerald-100"
      : tone === "amber"
        ? "border-amber-300/30 bg-amber-400/10 text-amber-100"
        : "border-white/10 bg-black/20 text-slate-100";
  return (
    <div className={`rounded-xl border p-3 ${toneClass}`}>
      <HelpLabel className="text-[0.68rem] uppercase tracking-widest text-slate-400" help={help}>{label}</HelpLabel>
      <div className="mt-1 font-display text-xl font-semibold">{value}</div>
    </div>
  );
}

function InfoPanel({ children, help, title }) {
  return (
    <section className="rounded-xl border border-white/10 bg-black/20 p-3">
      <HelpLabel className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400" help={help}>{title}</HelpLabel>
      {children}
    </section>
  );
}

function MetadataValue({ help, label, mono = false, value }) {
  return (
    <div className="mb-2 last:mb-0">
      <HelpLabel className="text-[0.68rem] uppercase tracking-widest text-slate-500" help={help}>{label}</HelpLabel>
      <div className={`break-words text-xs text-slate-200 ${mono ? "font-mono" : ""}`}>{compactText(value, "unknown")}</div>
    </div>
  );
}

function ControlPanel({
  control,
  controlError,
  controlPending,
  configPath,
  jobRunning,
  onConfigPathChange,
  onManualTaskChange,
  onStartAuto,
  onStartManual,
  selectedTaskId,
  tasks,
}) {
  const disabled = !control.enabled || jobRunning || Boolean(controlPending);
  const job = control.job;
  const status = job?.status || (control.enabled ? "idle" : "disabled");
  const taskLookup = new Map(tasks.map((task) => [task.id, task]));

  return (
    <section className="mb-6 rounded-2xl border border-white/10 bg-black/20 p-4 backdrop-blur-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <HelpLabel className="font-display text-xl font-semibold" help="These buttons run local Forge operations from the browser. Verification uses Docker; successful tasks are packaged for downstream training or evaluation.">Pipeline controls</HelpLabel>
          <div className="mt-1 text-sm text-slate-300">
            {control.enabled ? "Manual runs one selected task; auto mode fetches the configured batch and tracks each repo in the run queue." : "Restart with forge dashboard-live --enable-controls to allow local run control."}
          </div>
        </div>
        <div className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${jobStatusColor(status)}`}>{status}</div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
        <label className="grid gap-1 text-sm text-slate-300">
          <HelpLabel className="text-xs uppercase tracking-widest text-slate-400" help="Choose one already-fetched task, then run verification and packaging only for that task.">manual task</HelpLabel>
          <select
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 outline-none ring-cyan-300 transition focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50"
            value={selectedTaskId}
            onChange={(event) => onManualTaskChange(event.target.value)}
            disabled={!control.enabled || jobRunning || tasks.length === 0}
          >
            {tasks.length === 0 ? <option value="">no fetched tasks</option> : null}
            {tasks.map((task) => (
              <option value={task.id} key={task.id}>
                {taskOptionLabel(task)}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm text-slate-300">
          <HelpLabel className="text-xs uppercase tracking-widest text-slate-400" help="Path to the YAML batch config. Auto mode fetches tasks from this file, then verifies and packages each successful task once.">auto config</HelpLabel>
          <input
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 outline-none ring-cyan-300 transition focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50"
            value={configPath}
            onChange={(event) => onConfigPathChange(event.target.value)}
            disabled={!control.enabled || jobRunning}
          />
        </label>

        <div className="flex flex-wrap items-end gap-2">
          <button
            className="rounded-xl border border-cyan-300/40 bg-cyan-400/15 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-400/25 disabled:cursor-not-allowed disabled:opacity-50"
            type="button"
            onClick={onStartManual}
            disabled={disabled || !selectedTaskId}
          >
            {controlPending === "manual" ? "Starting" : "Start manual run"}
          </button>
          <button
            className="rounded-xl border border-emerald-300/40 bg-emerald-400/15 px-4 py-2 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-400/25 disabled:cursor-not-allowed disabled:opacity-50"
            type="button"
            onClick={onStartAuto}
            disabled={disabled}
          >
            {controlPending === "auto" ? "Starting" : "Run auto mode"}
          </button>
        </div>
      </div>

      {controlError ? <div className="mt-3 rounded-xl border border-rose-400/40 bg-rose-500/10 p-3 text-sm text-rose-200">{controlError}</div> : null}

      {job ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-black/25 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span className="font-mono">{job.mode} {job.task_id || job.config_path || job.id}</span>
            <span className="font-mono">{job.started_at ? new Date(job.started_at).toLocaleTimeString() : "-"}</span>
          </div>
          {job.queue?.length ? (
            <div className="mb-3 rounded-xl border border-white/10 bg-black/20 p-3">
              <HelpLabel className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400" help="Auto mode fills this queue after it fetches the configured repos and PR metadata. Each row then moves through queued, running, packaged, skipped, or failed.">Run queue</HelpLabel>
              <div className="grid gap-2">
                {job.queue.map((item) => {
                  const task = taskLookup.get(item.task_id) || {};
                  const repo = item.repo_name || task.repo_name || "repo pending";
                  const prNumber = item.pr_number || task.pr_number;
                  const title = item.pr_title || task.pr_title || "metadata pending";
                  return (
                    <div className="grid gap-2 rounded-lg border border-white/10 bg-black/20 p-2 text-xs text-slate-300 sm:grid-cols-[minmax(8rem,0.8fr)_minmax(10rem,1fr)_auto] sm:items-center" key={item.task_id}>
                      <span className="font-mono text-slate-100">{item.task_id}</span>
                      <span className="break-words">
                        {repo}{prNumber ? `#${prNumber}` : ""} · {title}
                      </span>
                      <span className={`w-fit rounded-full border px-2 py-1 font-semibold uppercase tracking-wider ${queueStatusColor(item.status)}`}>{item.status}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-300">
            {(job.logs || []).length ? job.logs.join("\n") : "Waiting for job output."}
            {job.error ? `\n${job.error}` : ""}
          </pre>
        </div>
      ) : null}
    </section>
  );
}

function MetricCard({ help, label, value, tone }) {
  const toneClass =
    tone === "emerald"
      ? "text-emerald-200 border-emerald-300/30 bg-emerald-500/10"
      : tone === "amber"
        ? "text-amber-200 border-amber-300/30 bg-amber-500/10"
        : tone === "rose"
          ? "text-rose-200 border-rose-300/30 bg-rose-500/10"
          : tone === "sky"
            ? "text-sky-200 border-sky-300/30 bg-sky-500/10"
            : "text-slate-200 border-slate-300/30 bg-slate-500/10";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <HelpLabel className="text-xs uppercase tracking-widest" help={help}>{label}</HelpLabel>
      <div className="font-display text-3xl font-semibold leading-none mt-2">{value}</div>
    </div>
  );
}
