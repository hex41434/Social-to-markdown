from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..models import CanonicalSave

PINNED_PLAYWRIGHT = "playwright==1.54.0"
MAX_LINKEDIN_ITEMS = 20


class LinkedInBrowserConnectorError(RuntimeError):
    pass


def browser_item_to_record(item: Any, imported_at: str) -> CanonicalSave | None:
    """Convert one constrained browser result into the canonical local model."""
    if not isinstance(item, dict):
        return None
    body = str(item.get("body") or "").strip()
    author = str(item.get("author") or "").strip() or None
    source_url = str(item.get("url") or "").strip() or None
    urn = str(item.get("urn") or "").strip()
    identity_source = urn or source_url or f"{author or ''}|{body}"
    if not identity_source.strip("|"):
        return None
    digits = "".join(character for character in urn if character.isdigit())
    source_id = (
        digits or "browser-" + sha256(identity_source.encode("utf-8")).hexdigest()[:20]
    )
    return CanonicalSave(
        platform="linkedin",
        source_id=source_id,
        source_url=source_url,
        author=author,
        collection="Saved Posts",
        source_text=body or None,
        archive_fingerprint="live:linkedin-browser-readonly",
        imported_at=imported_at,
        archive_label="LinkedIn Saved Posts: Chrome extension read",
    )


class LinkedInBrowserConnector:
    """One-shot Saved Posts reader using a temporary visible Chrome session."""

    def __init__(
        self,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        uv_path: str | None = None,
        runner_path: Path | None = None,
    ):
        self.run_command = run_command
        self.uv_path = uv_path or shutil.which("uv") or ""
        self.runner_path = runner_path or Path(__file__).with_name(
            "_linkedin_browser_runner.py"
        )
        if not self.uv_path:
            raise LinkedInBrowserConnectorError(
                "uv is required for the isolated LinkedIn reader"
            )

    def fetch(self, imported_at: str, max_items: int = 20) -> list[CanonicalSave]:
        if not 1 <= max_items <= MAX_LINKEDIN_ITEMS:
            raise LinkedInBrowserConnectorError("max_items must be between 1 and 20")
        command = [
            self.uv_path,
            "run",
            "--isolated",
            "--no-project",
            "--with",
            PINNED_PLAYWRIGHT,
            "python",
            str(self.runner_path),
            "--max-items",
            str(max_items),
        ]
        try:
            completed = self.run_command(
                command,
                capture_output=True,
                text=True,
                timeout=420,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LinkedInBrowserConnectorError(
                "LinkedIn browser did not finish within seven minutes"
            ) from exc
        except OSError as exc:
            raise LinkedInBrowserConnectorError(
                f"Isolated LinkedIn reader failed: {exc}"
            ) from exc
        stdout = completed.stdout.strip()
        try:
            payload: Any = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = (completed.stderr or stdout)[-1000:]
            raise LinkedInBrowserConnectorError(
                f"LinkedIn reader returned invalid structured output: {detail}"
            ) from exc
        if (
            completed.returncode != 0
            or not isinstance(payload, dict)
            or not payload.get("ok")
        ):
            message = (
                payload.get("error", "LinkedIn Saved Posts read failed")
                if isinstance(payload, dict)
                else "LinkedIn Saved Posts read failed"
            )
            raise LinkedInBrowserConnectorError(str(message))
        data = payload.get("data")
        if not isinstance(data, list):
            raise LinkedInBrowserConnectorError(
                "LinkedIn reader response did not contain a saved-post list"
            )
        return [
            record
            for item in data[:max_items]
            if (record := browser_item_to_record(item, imported_at)) is not None
        ]
