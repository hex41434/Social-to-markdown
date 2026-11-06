from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .config import AppConfig
from .connectors import TelegramConnector, XCookieConnector, load_cookie_values
from .connectors.linkedin_browser import browser_item_to_record
from .credentials import CredentialStore
from .service import ImportService, utc_now

STATIC_ROOT = Path(__file__).with_name("control_center")
PLATFORM_CAPS = {"x": 30, "linkedin": 20, "telegram": 100}
MAX_REQUEST_BYTES = 1_000_000


class DashboardError(RuntimeError):
    pass


@dataclass(slots=True)
class Job:
    id: str
    platform: str
    count: int
    status: str = "queued"
    progress: int = 0
    phase: str = "Queued"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    target_url: str | None = None
    created_at: str = field(default_factory=utc_now)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class ControlCenter:
    def __init__(self, config: AppConfig):
        self.config = config
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.last_activity = time.monotonic()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def create_job(self, platform: str, count: int, acknowledge_risk: bool) -> Job:
        platform = platform.lower().strip()
        if platform not in PLATFORM_CAPS:
            raise DashboardError(f"Unsupported platform: {platform}")
        if not 1 <= count <= PLATFORM_CAPS[platform]:
            raise DashboardError(
                f"Count for {platform} must be between 1 and {PLATFORM_CAPS[platform]}"
            )
        if platform in {"x", "linkedin"} and not acknowledge_risk:
            raise DashboardError("Risk acknowledgement is required for this connector")
        job = Job(id=uuid.uuid4().hex, platform=platform, count=count)
        with self.lock:
            self.jobs[job.id] = job
        if platform == "linkedin":
            job.status = "waiting_browser"
            job.progress = 5
            job.phase = "Opening LinkedIn Saved Posts in your signed-in Chrome"
            job.logs.append("Waiting for the Chrome extension to capture visible cards")
            job.target_url = (
                "https://www.linkedin.com/my-items/saved-posts/"
                f"#socials-obsidian-job={job.id}&count={count}"
            )
        else:
            threading.Thread(
                target=self._run_local_job, args=(job,), daemon=True
            ).start()
        return job

    def get_job(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise DashboardError("Unknown job")
        return job

    def browser_progress(self, job_id: str, progress: int, phase: str) -> Job:
        job = self.get_job(job_id)
        if job.platform != "linkedin" or job.status in {"complete", "failed"}:
            raise DashboardError("Job is not accepting browser progress")
        with self.lock:
            job.status = "running"
            job.progress = max(job.progress, min(max(progress, 5), 65))
            if phase:
                job.phase = phase[:160]
                job.logs.append(job.phase)
                job.logs = job.logs[-30:]
        return job

    def browser_results(self, job_id: str, items: list[Any]) -> Job:
        job = self.get_job(job_id)
        if job.platform != "linkedin" or job.status in {"complete", "failed"}:
            raise DashboardError("Job is not accepting browser results")
        if len(items) > job.count:
            items = items[: job.count]
        records = [
            record
            for item in items
            if (record := browser_item_to_record(item, utc_now())) is not None
        ]
        threading.Thread(
            target=self._import_records, args=(job, records), daemon=True
        ).start()
        return job

    def _run_local_job(self, job: Job) -> None:
        try:
            if job.platform == "x":
                self._update(job, 10, "Loading the private X credentials")
                auth_token, ct0 = load_cookie_values(self.config.x_cookie_path)
                try:
                    self._update(job, 20, f"Fetching up to {job.count} X bookmarks")
                    records = XCookieConnector().fetch(
                        auth_token, ct0, utc_now(), max_items=job.count
                    )
                finally:
                    auth_token = ""
                    ct0 = ""
            elif job.platform == "telegram":
                self._update(job, 10, "Opening the saved Telegram session")
                credentials = CredentialStore(self.config.credentials_path).load(
                    "telegram"
                )
                if not credentials:
                    raise DashboardError("Run `socials connect telegram` first")
                connector = TelegramConnector(
                    int(credentials["api_id"]),
                    str(credentials["api_hash"]),
                    self.config.credentials_dir / "telegram",
                )
                self._update(job, 20, f"Fetching up to {job.count} Saved Messages")
                records = asyncio.run(connector.fetch(utc_now(), limit=job.count))
            else:
                raise DashboardError("Unsupported local connector")
            self._update(job, 65, f"Fetched {len(records)} items")
            self._import_records(job, records)
        except Exception as exc:  # noqa: BLE001 - background boundary
            self._fail(job, str(exc))

    def _import_records(self, job: Job, records: list) -> None:
        try:
            self._update(job, 68, "Generating notes with local Ollama")

            def progress(position: int, total: int) -> None:
                percent = 68 + int((position / max(total, 1)) * 28)
                self._update(job, percent, f"Processing item {position} of {total}")

            result = ImportService(self.config).import_live_records(
                records, progress=progress
            )
            with self.lock:
                job.status = "complete"
                job.progress = 100
                job.phase = "Finished"
                job.result = result.as_dict()
                job.logs.append(
                    f"Created {result.created}, updated {result.updated}, "
                    f"skipped {result.skipped}, pending {result.pending}"
                )
        except Exception as exc:  # noqa: BLE001 - background boundary
            self._fail(job, str(exc))

    def _update(self, job: Job, progress: int, phase: str) -> None:
        self.touch()
        with self.lock:
            job.status = "running"
            job.progress = max(job.progress, min(progress, 99))
            job.phase = phase
            job.logs.append(phase)
            job.logs = job.logs[-30:]

    def _fail(self, job: Job, message: str) -> None:
        with self.lock:
            job.status = "failed"
            job.phase = "Failed"
            job.error = message[:1000]
            job.logs.append("Failed: " + job.error)


def _handler_for(center: ControlCenter):
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "SocialsControlCenter/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _headers(
            self, status: HTTPStatus, content_type: str = "application/json"
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin and self._origin_kind() != "untrusted":
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def _origin_kind(self) -> str:
            origin = self.headers.get("Origin")
            if not origin:
                return "local"
            parsed = urlsplit(origin)
            if parsed.scheme == "http" and parsed.hostname in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                return "dashboard"
            if parsed.scheme == "chrome-extension":
                return "extension"
            if origin == "https://www.linkedin.com":
                return "linkedin"
            return "untrusted"

        def _json(self, status: HTTPStatus, payload: Any) -> None:
            self._headers(status)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise DashboardError("Invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise DashboardError("Expected a JSON object")
            return payload

        def do_OPTIONS(self) -> None:
            if self._origin_kind() == "untrusted":
                self._json(
                    HTTPStatus.FORBIDDEN, {"ok": False, "error": "Origin denied"}
                )
                return
            self._headers(HTTPStatus.NO_CONTENT)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/status":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "platforms": PLATFORM_CAPS,
                        "idle_shutdown_minutes": 30,
                    },
                )
                return
            if path.startswith("/api/jobs/"):
                try:
                    job = center.get_job(path.rsplit("/", 1)[-1])
                    self._json(HTTPStatus.OK, job.snapshot())
                except DashboardError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            center.touch()
            path = urlsplit(self.path).path
            try:
                origin_kind = self._origin_kind()
                if origin_kind == "untrusted":
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "Origin denied"},
                    )
                    return
                if path == "/api/jobs" and origin_kind not in {
                    "local",
                    "dashboard",
                }:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "Only the dashboard may start jobs"},
                    )
                    return
                payload = self._body()
                if path == "/api/jobs":
                    job = center.create_job(
                        str(payload.get("platform") or ""),
                        int(payload.get("count") or 0),
                        bool(payload.get("acknowledge_risk")),
                    )
                    self._json(HTTPStatus.CREATED, job.snapshot())
                    return
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                    job_id, action = parts[2], parts[3]
                    if action == "progress":
                        job = center.browser_progress(
                            job_id,
                            int(payload.get("progress") or 0),
                            str(payload.get("phase") or ""),
                        )
                    elif action == "results":
                        items = payload.get("items")
                        if not isinstance(items, list):
                            raise DashboardError("Browser results must be a list")
                        job = center.browser_results(job_id, items)
                    else:
                        raise DashboardError("Unknown job action")
                    self._json(HTTPStatus.OK, job.snapshot())
                    return
                raise DashboardError("Unknown API endpoint")
            except (DashboardError, ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def _serve_static(self, request_path: str) -> None:
            relative = unquote(request_path).lstrip("/") or "index.html"
            candidate = (STATIC_ROOT / relative).resolve()
            try:
                candidate.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
                return
            if not candidate.is_file():
                candidate = STATIC_ROOT / "index.html"
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
            }
            self._headers(
                HTTPStatus.OK,
                content_types.get(candidate.suffix, "application/octet-stream"),
            )
            self.wfile.write(candidate.read_bytes())

    return DashboardHandler


def run_dashboard(
    config: AppConfig,
    port: int = 8766,
    open_browser: bool = True,
    idle_seconds: int = 1800,
) -> None:
    if not 1024 <= port <= 65535:
        raise DashboardError("Port must be between 1024 and 65535")
    center = ControlCenter(config)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(center))

    def idle_monitor() -> None:
        while True:
            time.sleep(min(30, max(1, idle_seconds // 4)))
            if time.monotonic() - center.last_activity >= idle_seconds:
                server.shutdown()
                return

    threading.Thread(target=idle_monitor, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    print(f"Social Saves Control Center: {url}")
    print(f"It will stop after {idle_seconds // 60} minutes of inactivity.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
