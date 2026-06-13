from __future__ import annotations

import json
import mimetypes
import re
import threading
import uuid
from dataclasses import dataclass, field
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
import ipaddress
from urllib.parse import unquote, urlparse, urlsplit

from forge.dashboard import DashboardTask, collect_dashboard_tasks
from forge.quality_report import recommend_status
from forge.task_builder import fetch_from_config, package_task, verify_task

DEFAULT_LIVE_HOST = "127.0.0.1"
DEFAULT_LIVE_PORT = 8765
DEFAULT_FRONTEND_DIST = Path("frontend") / "dist"
DEFAULT_CONTROL_CONFIG = Path("examples") / "tasks.yaml"

_CONTROL_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def _host_is_local(hostname: str) -> bool:
    host = (hostname or "").strip().strip("[]").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname_from_authority(authority: str | None) -> str:
    """Extract the host from a ``Host`` header value like ``127.0.0.1:8765`` or ``[::1]:8765``."""

    if not authority:
        return ""
    return urlsplit("//" + authority).hostname or ""


def _hostname_from_origin(origin: str | None) -> str:
    if not origin:
        return ""
    return urlsplit(origin).hostname or ""


def _control_request_allowed(host_header: str | None, origin: str | None) -> bool:
    """Allow control requests only from a loopback Host and (if sent) a loopback Origin.

    The control endpoints spawn Docker jobs, so they are state-changing. Validating
    the ``Host`` header defeats DNS-rebinding (an attacker domain pointed at
    127.0.0.1 still sends its own host), and rejecting a non-local ``Origin`` defeats
    cross-site POSTs from a page the user happens to be visiting.
    """

    if not _host_is_local(_hostname_from_authority(host_header)):
        return False
    if origin and not _host_is_local(_hostname_from_origin(origin)):
        return False
    return True


def _cors_origin_for(origin: str | None) -> str | None:
    """Return the request Origin to echo back, but only when it is loopback."""

    if origin and _host_is_local(_hostname_from_origin(origin)):
        return origin
    return None


class ControlBusyError(RuntimeError):
    """Raised when a dashboard control job is already running."""


@dataclass
class ControlQueueItem:
    task_id: str
    status: str
    repo_name: str = ""
    pr_number: int | None = None
    pr_title: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "repo_name": self.repo_name,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
        }


@dataclass
class ControlJob:
    id: str
    mode: str
    status: str
    started_at: str
    finished_at: str | None = None
    task_id: str | None = None
    config_path: str | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    queue: list[ControlQueueItem] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "task_id": self.task_id,
            "config_path": self.config_path,
            "error": self.error,
            "logs": list(self.logs),
            "queue": [item.to_payload() for item in self.queue],
        }


class DashboardControlRunner:
    """Run explicit local forge actions for the live dashboard."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._job: ControlJob | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"enabled": True, "job": None if self._job is None else self._job.to_payload()}

    def wait_for_current_job(self, timeout_seconds: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_seconds)
        return not thread.is_alive()

    def start_manual_run(self, task_id: str) -> dict[str, Any]:
        resolved_task_id = _validate_task_id(task_id)
        job = ControlJob(
            id=uuid.uuid4().hex,
            mode="manual",
            status="running",
            started_at=_now_iso(),
            task_id=resolved_task_id,
            queue=[ControlQueueItem(task_id=resolved_task_id, status="queued")],
        )
        return self._start_job(job, lambda: self._run_task(job, resolved_task_id))

    def start_auto_run(self, config_path: str | None = None) -> dict[str, Any]:
        resolved_config = _resolve_control_config(self.root, config_path)
        display_config = _display_path(self.root, resolved_config)
        job = ControlJob(
            id=uuid.uuid4().hex,
            mode="auto",
            status="running",
            started_at=_now_iso(),
            config_path=display_config,
        )
        return self._start_job(job, lambda: self._run_auto(job, resolved_config))

    def _start_job(self, job: ControlJob, action: Callable[[], None]) -> dict[str, Any]:
        thread = threading.Thread(target=self._run_guarded, args=(job, action), daemon=True)
        with self._lock:
            if self._job is not None and self._job.status == "running":
                raise ControlBusyError("A control job is already running.")
            self._job = job
            self._thread = thread
        thread.start()
        return job.to_payload()

    def _run_guarded(self, job: ControlJob, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - exposed as job status for the dashboard.
            self._finish(job, "failed", str(exc))
        else:
            self._finish(job, "succeeded", None)

    def _run_auto(self, job: ControlJob, config_path: Path) -> None:
        self._append_log(job, f"Fetching tasks from {_display_path(self.root, config_path)}")
        metadata_items = fetch_from_config(config_path, root=self.root, log=lambda message: self._append_log(job, message))
        if not metadata_items:
            self._append_log(job, "No tasks were found in the config.")
            return

        self._set_queue(job, [_queue_item_from_metadata(metadata) for metadata in metadata_items])
        for metadata in metadata_items:
            self._run_task(job, metadata.id)

    def _run_task(self, job: ControlJob, task_id: str) -> None:
        self._update_queue_item(job, task_id, "running")
        try:
            self._append_log(job, f"Verifying {task_id}")
            verification = verify_task(task_id, root=self.root, log=lambda message: self._append_log(job, message))
            status = recommend_status(verification)
            self._append_log(job, f"Verification status for {task_id}: {status}")
            if status == "invalid":
                self._append_log(job, f"Skipping package for {task_id} because verification is invalid.")
                self._update_queue_item(job, task_id, "skipped")
                return

            self._append_log(job, f"Packaging {task_id}")
            package_dir = package_task(task_id, root=self.root, log=lambda message: self._append_log(job, message))
            self._append_log(job, f"Packaged {task_id} at {_display_path(self.root, package_dir)}")
            self._update_queue_item(job, task_id, "packaged")
        except Exception:
            self._update_queue_item(job, task_id, "failed")
            raise

    def _set_queue(self, job: ControlJob, queue: list[ControlQueueItem]) -> None:
        with self._lock:
            job.queue = queue

    def _update_queue_item(self, job: ControlJob, task_id: str, status: str) -> None:
        with self._lock:
            for item in job.queue:
                if item.task_id == task_id:
                    item.status = status
                    return
            job.queue.append(ControlQueueItem(task_id=task_id, status=status))

    def _append_log(self, job: ControlJob, message: str) -> None:
        with self._lock:
            job.logs.append(f"{_now_iso()} {message}")
            del job.logs[:-200]

    def _finish(self, job: ControlJob, status: str, error: str | None) -> None:
        with self._lock:
            job.status = status
            job.finished_at = _now_iso()
            job.error = error


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_item_from_metadata(metadata: Any) -> ControlQueueItem:
    return ControlQueueItem(
        task_id=str(metadata.id),
        status="queued",
        repo_name=str(getattr(metadata, "repo_name", "")),
        pr_number=getattr(metadata, "pr_number", None),
        pr_title=str(getattr(metadata, "pr_title", "")),
    )


def _validate_task_id(task_id: str) -> str:
    value = task_id.strip()
    if not value or value in {".", ".."} or _CONTROL_TASK_ID_RE.fullmatch(value) is None:
        raise ValueError("Task id must contain only letters, numbers, dots, underscores, and hyphens.")
    return value


def _resolve_control_config(root: Path, config_path: str | None) -> Path:
    raw_path = (config_path or str(DEFAULT_CONTROL_CONFIG)).strip()
    if not raw_path:
        raw_path = str(DEFAULT_CONTROL_CONFIG)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Config path must be inside the workspace root.") from exc
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"Config file not found: {_display_path(root, candidate)}")
    return candidate


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def build_dashboard_snapshot(root: Path | None = None) -> dict[str, Any]:
    """Return a JSON-serializable snapshot for the live dashboard."""

    root = root or Path.cwd()
    tasks = collect_dashboard_tasks(root)
    payload = [task.model_dump(mode="json") for task in tasks]
    summary = summarize_tasks(tasks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "tasks": payload,
    }


def summarize_tasks(tasks: list[DashboardTask]) -> dict[str, int]:
    """Compute aggregate counters for the dashboard header cards."""

    counts = {
        "total": len(tasks),
        "usable": 0,
        "needs_review": 0,
        "invalid": 0,
        "unverified": 0,
    }
    for task in tasks:
        status = task.recommended_status
        if status in counts:
            counts[status] += 1
    return counts


def serve_live_dashboard(
    *,
    root: Path | None = None,
    host: str = DEFAULT_LIVE_HOST,
    port: int = DEFAULT_LIVE_PORT,
    static_dir: Path | None = None,
    open_browser: bool = False,
    controls_enabled: bool = False,
    control_runner: DashboardControlRunner | None = None,
) -> None:
    """Start the live dashboard server and block forever."""

    root = (root or Path.cwd()).resolve()
    resolved_static = None if static_dir is None else _resolve_static_dir(root, static_dir)
    runner = control_runner if control_runner is not None else DashboardControlRunner(root) if controls_enabled else None

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required http.server method name
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/tasks":
                self._write_json(build_dashboard_snapshot(root))
                return
            if path == "/api/control/status":
                self._write_json({"enabled": False, "job": None} if runner is None else runner.snapshot())
                return
            if path == "/health":
                self._write_json({"status": "ok"})
                return
            if path.startswith("/api/"):
                self._write_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if resolved_static is None:
                self._write_text(
                    HTTPStatus.NOT_FOUND,
                    "Live API is running, but no frontend build was found. Build frontend with `npm --prefix frontend run build`.\n",
                )
                return

            self._serve_static(path, resolved_static)

        def do_POST(self) -> None:  # noqa: N802 - required http.server method name
            parsed = urlparse(self.path)
            if parsed.path == "/api/control/manual":
                self._handle_control("manual")
                return
            if parsed.path == "/api/control/auto":
                self._handle_control("auto")
                return
            self._write_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_OPTIONS(self) -> None:  # noqa: N802 - required http.server method name
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()

        def _send_cors_headers(self) -> None:
            cors = _cors_origin_for(self.headers.get("Origin"))
            if cors is not None:
                self.send_header("Access-Control-Allow-Origin", cors)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _handle_control(self, mode: str) -> None:
            if runner is None:
                self._write_json(
                    {"enabled": False, "error": "Controls are disabled. Restart dashboard-live with --enable-controls."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            if not _control_request_allowed(self.headers.get("Host"), self.headers.get("Origin")):
                self._write_json(
                    {"enabled": True, "error": "Control requests must originate from localhost."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            try:
                payload = self._read_json_body()
                if mode == "manual":
                    job = runner.start_manual_run(str(payload.get("task_id", "")))
                else:
                    config_path = payload.get("config_path")
                    job = runner.start_auto_run(None if config_path is None else str(config_path))
            except ControlBusyError as exc:
                self._write_json({"enabled": True, "error": str(exc), "job": runner.snapshot()["job"]}, status=HTTPStatus.CONFLICT)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                self._write_json({"enabled": True, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            else:
                self._write_json({"enabled": True, "job": job}, status=HTTPStatus.ACCEPTED)

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length <= 0:
                return {}
            raw_body = self.rfile.read(min(content_length, 65536))
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            return payload

        def _serve_static(self, request_path: str, dist_dir: Path) -> None:
            relative = request_path.lstrip("/")
            if not relative:
                relative = "index.html"

            safe_relative = Path(unquote(relative))
            candidate = (dist_dir / safe_relative).resolve()
            try:
                candidate.relative_to(dist_dir)
            except ValueError:
                self._write_text(HTTPStatus.FORBIDDEN, "Forbidden\n")
                return

            if candidate.is_dir():
                candidate = candidate / "index.html"

            if not candidate.exists() or not candidate.is_file():
                candidate = dist_dir / "index.html"

            if not candidate.exists():
                self._write_text(HTTPStatus.NOT_FOUND, "Not Found\n")
                return

            content = candidate.read_bytes()
            content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _write_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _write_text(self, status: HTTPStatus, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return None

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _resolve_static_dir(root: Path, static_dir: Path) -> Path | None:
    candidate = static_dir if static_dir.is_absolute() else (root / static_dir)
    candidate = candidate.resolve()
    if not candidate.exists() or not candidate.is_dir():
        return None
    return candidate
