from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from socials_obsidian.cli import app
from socials_obsidian.connectors.linkedin_browser import (
    MAX_LINKEDIN_ITEMS,
    LinkedInBrowserConnector,
    LinkedInBrowserConnectorError,
)
from socials_obsidian.connectors.telegram import message_to_record
from socials_obsidian.connectors.x import SCOPES, XConnector, authorization_url
from socials_obsidian.connectors.x_cookie import (
    MAX_COOKIE_BOOKMARKS,
    XCookieConnector,
    XCookieConnectorError,
    load_cookie_values,
    parse_cookie_header,
)
from socials_obsidian.credentials import CredentialStore


def test_private_credential_store_is_git_safe(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.save("telegram", {"api_id": 123, "api_hash": "secret"})
    assert store.load("telegram")["api_hash"] == "secret"
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_telegram_saved_message_becomes_canonical_record() -> None:
    forward = SimpleNamespace(
        from_name="Research Channel",
        post_author=None,
        sender_id=42,
        date=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )
    message = SimpleNamespace(
        id=77,
        raw_text="A saved Telegram message with source text.",
        date=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        forward=forward,
    )
    record = message_to_record(message, "2026-08-04T12:00:00Z")
    assert record is not None
    assert record.stable_key == "telegram:77"
    assert record.author == "Research Channel"
    assert record.saved_at == "2026-08-02T10:00:00Z"
    assert record.published_at == "2026-08-01T09:00:00Z"


def test_x_authorization_url_uses_pkce_and_minimal_read_scopes() -> None:
    value = authorization_url("client", "http://127.0.0.1:8765/callback", "s", "c")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(value).query))
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == SCOPES
    assert "bookmark.write" not in query["scope"]


def test_x_fetch_reads_bookmarks_and_builds_canonical_urls(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    store.save(
        "x",
        {
            "client_id": "client",
            "redirect_uri": "http://127.0.0.1:8765/callback",
            "token": {"access_token": "token", "expires_at": time.time() + 3600},
        },
    )
    calls: list[str] = []

    def fake_request(url: str, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        if url.endswith("/users/me"):
            return {"data": {"id": "me"}}
        return {
            "data": [
                {
                    "id": "123",
                    "text": "A bookmarked post returned by the official X API.",
                    "author_id": "9",
                    "created_at": "2026-08-01T10:00:00.000Z",
                }
            ],
            "includes": {
                "users": [{"id": "9", "username": "author", "name": "Author"}]
            },
            "meta": {"result_count": 1},
        }

    records = XConnector(store, request_json=fake_request).fetch(
        "2026-08-04T12:00:00Z", max_items=100
    )
    assert len(calls) == 2
    assert records[0].source_url == "https://x.com/author/status/123"
    assert records[0].author == "@author"
    assert records[0].source_text.startswith("A bookmarked post")


def test_x_sync_requires_explicit_paid_acknowledgement() -> None:
    result = CliRunner().invoke(app, ["sync", "x"])
    assert result.exit_code == 2
    assert "potentially billable" in result.output


def test_unavailable_saved_collection_connectors_are_truthful() -> None:
    runner = CliRunner()
    instagram = runner.invoke(app, ["connect", "instagram"])
    linkedin = runner.invoke(app, ["connect", "linkedin"])
    assert instagram.exit_code == 2
    assert "does not expose" in instagram.output
    assert linkedin.exit_code == 2
    assert "not available" in linkedin.output


def test_x_cookie_parser_keeps_only_required_values() -> None:
    auth_token, ct0 = parse_cookie_header(
        "guest_id=discard; auth_token=abcdefghij123; other=discard; ct0=csrf-token-12345"
    )
    assert auth_token == "abcdefghij123"
    assert ct0 == "csrf-token-12345"
    with pytest.raises(XCookieConnectorError):
        parse_cookie_header("auth_token=only-one-value")


def test_x_cookie_private_file_loader(tmp_path: Path) -> None:
    path = tmp_path / "x-cookie.env"
    path.write_text(
        "# Raw values only\nAUTH_TOKEN=abcdefghij123\nCT0=csrf-token-12345\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    assert load_cookie_values(path) == ("abcdefghij123", "csrf-token-12345")


def test_x_cookie_file_rejects_placeholder_and_open_permissions(tmp_path: Path) -> None:
    path = tmp_path / "x-cookie.env"
    path.write_text(
        "AUTH_TOKEN=PASTE_AUTH_TOKEN_VALUE_HERE\nCT0=PASTE_CT0_VALUE_HERE\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(XCookieConnectorError, match="placeholders"):
        load_cookie_values(path)
    path.chmod(0o644)
    with pytest.raises(XCookieConnectorError, match="0600"):
        load_cookie_values(path)


def test_x_cookie_connector_is_capped_read_only_and_ephemeral(tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        payload = {
            "ok": True,
            "data": [
                {
                    "id": "456",
                    "text": "A bookmark read through the constrained one-shot wrapper.",
                    "author": {"screenName": "safeauthor", "name": "Safe Author"},
                    "createdAtISO": "2026-08-04T10:00:00Z",
                }
            ],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    connector = XCookieConnector(
        run_command=fake_run,
        uv_path="/usr/bin/uv",
        runner_path=tmp_path / "runner.py",
    )
    records = connector.fetch(
        "abcdefghij123",
        "csrf-token-12345",
        "2026-08-04T12:00:00Z",
        max_items=20,
    )
    command_text = " ".join(captured["command"])
    assert "bookmarks" not in command_text
    assert "auth_token" not in command_text
    assert "abcdefghij123" not in command_text
    assert "--max-items 20" in command_text
    assert "TWITTER_AUTH_TOKEN" not in captured["environment"]
    assert "TWITTER_CT0" not in captured["environment"]
    assert records[0].source_url == "https://x.com/safeauthor/status/456"


def test_x_cookie_connector_hard_cap() -> None:
    connector = XCookieConnector(run_command=lambda *args, **kwargs: None, uv_path="uv")
    with pytest.raises(XCookieConnectorError, match="between 1 and 30"):
        connector.fetch(
            "abcdefghij123",
            "csrf-token-12345",
            "2026-08-04T12:00:00Z",
            max_items=MAX_COOKIE_BOOKMARKS + 1,
        )


def test_x_cookie_cli_requires_explicit_risk_acknowledgement() -> None:
    result = CliRunner().invoke(app, ["sync", "x-cookie"])
    assert result.exit_code == 2
    assert "acknowledge-risk" in result.output


def test_x_cookie_timeout_is_explained() -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=180)

    connector = XCookieConnector(run_command=timeout, uv_path="uv")
    with pytest.raises(XCookieConnectorError, match="within 180 seconds"):
        connector.fetch(
            "abcdefghij123",
            "csrf-token-12345",
            "2026-08-04T12:00:00Z",
        )


def test_x_cookie_runner_disables_browser_fallback() -> None:
    runner = (
        Path(__file__).parents[1]
        / "src"
        / "socials_obsidian"
        / "connectors"
        / "_x_cookie_runner.py"
    ).read_text(encoding="utf-8")
    assert "auth.extract_from_browser = _deny_browser_cookies" in runner
    assert "client.fetch_bookmarks(args.max_items)" in runner
    assert "client.create_" not in runner
    assert "client.delete_" not in runner


def test_linkedin_browser_connector_is_capped_and_canonical(tmp_path: Path) -> None:
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        payload = {
            "ok": True,
            "data": [
                {
                    "urn": "urn:li:activity:1234567890",
                    "author": "Example Author",
                    "body": "A saved LinkedIn post about local knowledge workflows.",
                    "url": "https://www.linkedin.com/feed/update/urn:li:activity:1234567890/",
                }
            ],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    connector = LinkedInBrowserConnector(
        run_command=fake_run,
        uv_path="/usr/bin/uv",
        runner_path=tmp_path / "runner.py",
    )
    records = connector.fetch("2026-08-05T12:00:00Z", max_items=20)

    command_text = " ".join(captured["command"])
    assert "--isolated" in captured["command"]
    assert "playwright==1.54.0" in command_text
    assert "--max-items 20" in command_text
    assert "password" not in command_text.lower()
    assert records[0].source_id == "1234567890"
    assert records[0].platform == "linkedin"
    assert records[0].collection == "Saved Posts"


def test_linkedin_browser_connector_hard_cap() -> None:
    connector = LinkedInBrowserConnector(
        run_command=lambda *args, **kwargs: None, uv_path="uv"
    )
    with pytest.raises(LinkedInBrowserConnectorError, match="between 1 and 20"):
        connector.fetch("2026-08-05T12:00:00Z", max_items=MAX_LINKEDIN_ITEMS + 1)


def test_linkedin_browser_cli_requires_risk_acknowledgement() -> None:
    result = CliRunner().invoke(app, ["sync", "linkedin-browser"])
    assert result.exit_code == 2
    assert "acknowledge-risk" in result.output


def test_linkedin_runner_has_no_credentials_or_persistent_profile() -> None:
    runner = (
        Path(__file__).parents[1]
        / "src"
        / "socials_obsidian"
        / "connectors"
        / "_linkedin_browser_runner.py"
    ).read_text(encoding="utf-8")
    assert "MAX_ITEMS = 20" in runner
    assert "MAX_SCROLLS = 3" in runner
    assert "headless=False" in runner
    assert "browser.new_context" in runner
    assert "launch_persistent_context" not in runner
    assert "LINKEDIN_EMAIL" not in runner
    assert "LINKEDIN_PASSWORD" not in runner
    assert ".fill(" not in runner
    assert ".click(" not in runner
