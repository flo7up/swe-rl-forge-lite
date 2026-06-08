import { useEffect, useMemo, useState } from "react";

const POLL_MS = 2000;
const DEFAULT_CONFIG_PATH = "examples/tasks.yaml";

const STATUS_ORDER = ["usable", "needs_review", "invalid", "unverified"];

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

function CheckPill({ name, value }) {
  const rendered = boolLabel(value);
  const color = rendered === "yes" ? "text-emerald-300" : rendered === "no" ? "text-rose-300" : "text-slate-300";
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-slate-400">{name}</div>
      <div className={`font-mono text-sm ${color}`}>{rendered}</div>
    </div>
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
      const haystack = `${task.id} ${task.repo_name} ${task.pr_title} ${task.test_command}`.toLowerCase();
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
          <MetricCard label="total" value={snapshot.summary?.total ?? 0} tone="slate" />
          <MetricCard label="usable" value={snapshot.summary?.usable ?? 0} tone="emerald" />
          <MetricCard label="needs review" value={snapshot.summary?.needs_review ?? 0} tone="amber" />
          <MetricCard label="invalid" value={snapshot.summary?.invalid ?? 0} tone="rose" />
          <MetricCard label="unverified" value={snapshot.summary?.unverified ?? 0} tone="sky" />
        </section>

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
                    {toLabel(task.recommended_status)}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                  <CheckPill name="base" value={task.checks?.base_commit_found} />
                  <CheckPill name="patch" value={task.checks?.patch_applies} />
                  <CheckPill name="fails before" value={task.checks?.tests_fail_before_patch} />
                  <CheckPill name="passes after" value={task.checks?.tests_pass_after_patch} />
                  <CheckPill name="rerun" value={task.checks?.deterministic_rerun_success} />
                  <CheckPill name="docker" value={task.checks?.docker_build_success} />
                  <CheckPill name="test env" value={task.checks?.test_environment_success} />
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

  return (
    <section className="mb-6 rounded-2xl border border-white/10 bg-black/20 p-4 backdrop-blur-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-semibold">Pipeline controls</h2>
          <div className="mt-1 text-sm text-slate-300">
            {control.enabled ? "Manual runs verify and package one fetched task; auto mode runs the configured batch once." : "Restart with forge dashboard-live --enable-controls to allow local run control."}
          </div>
        </div>
        <div className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${jobStatusColor(status)}`}>{status}</div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
        <label className="grid gap-1 text-sm text-slate-300">
          <span className="text-xs uppercase tracking-widest text-slate-400">manual task</span>
          <select
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 outline-none ring-cyan-300 transition focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50"
            value={selectedTaskId}
            onChange={(event) => onManualTaskChange(event.target.value)}
            disabled={!control.enabled || jobRunning || tasks.length === 0}
          >
            {tasks.length === 0 ? <option value="">no fetched tasks</option> : null}
            {tasks.map((task) => (
              <option value={task.id} key={task.id}>
                {task.id}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm text-slate-300">
          <span className="text-xs uppercase tracking-widest text-slate-400">auto config</span>
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
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-300">
            {(job.logs || []).length ? job.logs.join("\n") : "Waiting for job output."}
            {job.error ? `\n${job.error}` : ""}
          </pre>
        </div>
      ) : null}
    </section>
  );
}

function MetricCard({ label, value, tone }) {
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
      <div className="text-xs uppercase tracking-widest">{label}</div>
      <div className="font-display text-3xl font-semibold leading-none mt-2">{value}</div>
    </div>
  );
}
