from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import CanonicalSave

PINNED_TWITTER_CLI = (
    "twitter-cli @ git+https://github.com/public-clis/twitter-cli.git"
    "@7c634e0d396b1e7af9f63315b414925fe4f29ae7"
)
MAX_COOKIE_BOOKMARKS = 30
COOKIE_FILE_KEYS = {"AUTH_TOKEN", "CT0"}


class XCookieConnectorError(RuntimeError):
    pass


def parse_cookie_header(value: str) -> tuple[str, str]:
    """Extract only auth_token and ct0; discard every other browser cookie."""
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise XCookieConnectorError("Cookie header contains control characters")
    parsed: dict[str, str] = {}
    for part in value.split(";"):
        name, separator, item = part.strip().partition("=")
        if separator and name in {"auth_token", "ct0"} and item:
            parsed[name] = item.strip()
    auth_token = parsed.get("auth_token", "")
    ct0 = parsed.get("ct0", "")
    return validate_cookie_values(auth_token, ct0)


def validate_cookie_values(auth_token: str, ct0: str) -> tuple[str, str]:
    """Validate explicit cookie values without accepting unrelated cookies."""
    auth_token = auth_token.strip()
    ct0 = ct0.strip()
    if any(character in auth_token + ct0 for character in ("\r", "\n", "\x00", ";")):
        raise XCookieConnectorError("X credential values contain invalid characters")
    if not (10 <= len(auth_token) <= 512 and 10 <= len(ct0) <= 512):
        raise XCookieConnectorError("Valid auth_token and ct0 values are required")
    return auth_token, ct0


def load_cookie_values(path: Path) -> tuple[str, str]:
    """Read the two persistent values from a small, private local file."""
    if not path.exists():
        raise XCookieConnectorError(f"X credential file does not exist: {path}")
    if path.is_symlink():
        raise XCookieConnectorError("X credential file may not be a symbolic link")
    details = path.stat()
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise XCookieConnectorError(
            f"X credential file must have mode 0600: chmod 600 '{path}'"
        )
    if details.st_size > 4096:
        raise XCookieConnectorError("X credential file is unexpectedly large")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in COOKIE_FILE_KEYS:
            raise XCookieConnectorError(
                f"Unexpected line {line_number} in X credential file"
            )
        if key in values:
            raise XCookieConnectorError(f"Duplicate {key} in X credential file")
        values[key] = value.strip()

    auth_token = values.get("AUTH_TOKEN", "")
    ct0 = values.get("CT0", "")
    if auth_token.startswith("PASTE_") or ct0.startswith("PASTE_"):
        raise XCookieConnectorError(
            f"Replace both PASTE_... placeholders in {path} with the raw values"
        )
    return validate_cookie_values(auth_token, ct0)


def _redact(value: str, secrets: tuple[str, str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


class XCookieConnector:
    """One-shot unofficial bookmark reader with no persistent credentials."""

    def __init__(
        self,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        uv_path: str | None = None,
        runner_path: Path | None = None,
    ):
        self.run_command = run_command
        self.uv_path = uv_path or shutil.which("uv") or ""
        self.runner_path = runner_path or Path(__file__).with_name(
            "_x_cookie_runner.py"
        )
        if not self.uv_path:
            raise XCookieConnectorError("uv is required for the isolated X reader")

    def fetch(
        self,
        auth_token: str,
        ct0: str,
        imported_at: str,
        max_items: int = 20,
    ) -> list[CanonicalSave]:
        if not 1 <= max_items <= MAX_COOKIE_BOOKMARKS:
            raise XCookieConnectorError("max_items must be between 1 and 30")
        auth_token, ct0 = validate_cookie_values(auth_token, ct0)
        redaction_secrets = (auth_token, ct0)
        child_environment = os.environ.copy()
        child_environment["TWITTER_AUTH_TOKEN"] = auth_token
        child_environment["TWITTER_CT0"] = ct0
        command = [
            self.uv_path,
            "run",
            "--isolated",
            "--no-project",
            "--with",
            PINNED_TWITTER_CLI,
            "python",
            str(self.runner_path),
            "--max-items",
            str(max_items),
        ]
        try:
            completed = self.run_command(
                command,
                env=child_environment,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise XCookieConnectorError(
                "X did not respond within 180 seconds; no credentials were changed"
            ) from exc
        except OSError as exc:
            raise XCookieConnectorError(f"Isolated X reader failed: {exc}") from exc
        finally:
            child_environment.pop("TWITTER_AUTH_TOKEN", None)
            child_environment.pop("TWITTER_CT0", None)
            auth_token = ""
            ct0 = ""

        stdout = completed.stdout.strip()
        try:
            payload: Any = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = _redact((completed.stderr or stdout)[:800], redaction_secrets)
            raise XCookieConnectorError(
                f"X reader returned invalid structured output: {detail}"
            ) from exc
        if (
            completed.returncode != 0
            or not isinstance(payload, dict)
            or not payload.get("ok")
        ):
            message = (
                payload.get("error", "X bookmark read failed")
                if isinstance(payload, dict)
                else "X bookmark read failed"
            )
            raise XCookieConnectorError(_redact(str(message), redaction_secrets))
        data = payload.get("data")
        if not isinstance(data, list):
            raise XCookieConnectorError(
                "X reader response did not contain a bookmark list"
            )
        records = [
            record
            for item in data[:max_items]
            if (record := self._record(item, imported_at)) is not None
        ]
        redaction_secrets = ("", "")
        return records

    @staticmethod
    def _record(item: Any, imported_at: str) -> CanonicalSave | None:
        if not isinstance(item, dict) or not item.get("id"):
            return None
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        username = str(author.get("screenName") or "").strip()
        source_id = str(item["id"])
        source_url = (
            f"https://x.com/{username}/status/{source_id}"
            if username
            else f"https://x.com/i/web/status/{source_id}"
        )
        return CanonicalSave(
            platform="x",
            source_id=source_id,
            source_url=source_url,
            author=(
                f"@{username}" if username else str(author.get("name") or "") or None
            ),
            title=str(item.get("articleTitle") or "") or None,
            published_at=str(item.get("createdAtISO") or item.get("createdAt") or "")
            or None,
            collection="Bookmarks",
            source_text=str(item.get("articleText") or item.get("text") or "") or None,
            archive_fingerprint="live:x-cookie-readonly",
            imported_at=imported_at,
            archive_label="X bookmarks: ephemeral unofficial cookie read",
        )
