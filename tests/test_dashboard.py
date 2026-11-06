from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from socials_obsidian.dashboard import (
    ControlCenter,
    DashboardError,
    _handler_for,
)


def test_control_center_enforces_risk_and_platform_caps(app_config) -> None:
    center = ControlCenter(app_config)
    with pytest.raises(DashboardError, match="acknowledgement"):
        center.create_job("linkedin", 10, acknowledge_risk=False)
    with pytest.raises(DashboardError, match="between 1 and 20"):
        center.create_job("linkedin", 21, acknowledge_risk=True)

    job = center.create_job("linkedin", 10, acknowledge_risk=True)
    assert job.status == "waiting_browser"
    assert job.progress == 5
    assert job.target_url is not None
    assert "linkedin.com/my-items/saved-posts" in job.target_url
    assert f"socials-obsidian-job={job.id}" in job.target_url
    assert "count=10" in job.target_url


def test_dashboard_rejects_cross_site_job_creation(app_config) -> None:
    center = ControlCenter(app_config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(center))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/jobs"
    body = json.dumps(
        {"platform": "linkedin", "count": 10, "acknowledge_risk": True}
    ).encode()
    try:
        evil = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://evil.test"},
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(evil, timeout=2)
        assert denied.value.code == 403

        local = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{server.server_port}",
            },
        )
        with urllib.request.urlopen(local, timeout=2) as response:
            payload = json.loads(response.read())
        assert response.status == 201
        assert payload["status"] == "waiting_browser"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_chrome_extension_is_narrow_and_credential_free() -> None:
    root = Path(__file__).parents[1] / "chrome-extension"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["content_scripts"][0]["matches"] == [
        "https://www.linkedin.com/my-items/saved-posts/*"
    ]
    assert manifest["host_permissions"] == ["http://127.0.0.1:8766/*"]
    assert "permissions" not in manifest

    capture = (root / "linkedin-capture.js").read_text(encoding="utf-8")
    assert "scroll < 3" in capture
    assert "Math.min(20" in capture
    assert "document.cookie" not in capture
    assert "localStorage" not in capture
    assert "password" not in capture.lower()
    assert "127.0.0.1:8766" in capture


def test_linkedin_extension_follows_local_job_to_terminal_progress() -> None:
    capture = (
        Path(__file__).parents[1] / "chrome-extension" / "linkedin-capture.js"
    ).read_text(encoding="utf-8")
    assert "async function followJobToCompletion" in capture
    assert "await followJobToCompletion()" in capture
    assert 'job.status === "complete"' in capture
    assert 'job.status === "failed"' in capture
    assert "renderProgress(100, `Complete" in capture


def test_linkedin_extension_advances_nested_results_and_load_more() -> None:
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).parents[1] / "chrome-extension"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][0]["js"]
    assert scripts.index("linkedin-scroll.js") < scripts.index("linkedin-capture.js")

    harness = r"""
const fs = require("fs");
global.getComputedStyle = () => ({ overflowY: "auto" });
eval(fs.readFileSync(process.argv[1], "utf8"));
const api = global.SocialSavesLinkedInScroll;
let containerScrolled = false;
let windowScrolled = false;
const container = {
  scrollHeight: 1200, clientHeight: 300, parentElement: null,
  scrollTo: ({ top }) => { containerScrolled = top === 1200; }
};
const card = { parentElement: container, scrollIntoView: () => {} };
const noButtonDocument = {
  querySelectorAll: () => [],
  scrollingElement: { scrollHeight: 2000, clientHeight: 900 }
};
api.advanceResults(card, noButtonDocument, {
  scrollTo: () => { windowScrolled = true; }
});
if (!containerScrolled || windowScrolled) process.exit(1);

let clicked = false;
const button = {
  innerText: "Show more results", disabled: false,
  getAttribute: () => "", getClientRects: () => [1],
  click: () => { clicked = true; }
};
const buttonDocument = { querySelectorAll: () => [button], scrollingElement: null };
const result = api.advanceResults(card, buttonDocument, { scrollTo: () => {} });
if (result !== "button" || !clicked) process.exit(2);
"""
    completed = subprocess.run(
        [node, "-e", harness, str(root / "linkedin-scroll.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr


def test_clickable_launcher_uses_loopback_and_no_social_schedule() -> None:
    launcher = (
        Path(__file__).parents[1]
        / "launcher"
        / "Social Saves.app"
        / "Contents"
        / "MacOS"
        / "Social Saves"
    ).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8766" in launcher
    assert "socials dashboard --no-open" in launcher
    assert "linkedin.com" not in launcher
    assert "x.com" not in launcher
    assert "cron" not in launcher

    manifest = json.loads(
        (Path(__file__).parents[1] / "chrome-extension" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["name"] == "AiFa Social"

    installer = (
        Path(__file__).parents[1] / "scripts" / "install_control_center_app.sh"
    ).read_text(encoding="utf-8")
    assert "/Desktop/AiFa Social.app" in installer
