from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

from ..credentials import CredentialStore
from ..models import CanonicalSave

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_ROOT = "https://api.x.com/2"
SCOPES = "bookmark.read tweet.read users.read offline.access"


class XConnectorError(RuntimeError):
    pass


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorization_url(
    client_id: str, redirect_uri: str, state: str, challenge: str
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode() if form else None
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise XConnectorError(f"X API returned HTTP {exc.code}: {detail}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise XConnectorError(f"X API request failed: {exc}") from exc
    if not isinstance(value, dict):
        raise XConnectorError("X API returned an unexpected response")
    return value


class _CallbackHandler(BaseHTTPRequestHandler):
    params: ClassVar[dict[str, str]] = {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        type(self).params = dict(urllib.parse.parse_qsl(parsed.query))
        body = b"Authorization received. You can close this window and return to the terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class XConnector:
    """Official OAuth 2.0 PKCE bookmark reader with local token storage."""

    def __init__(
        self,
        store: CredentialStore,
        request_json: Callable[..., dict[str, Any]] = _request_json,
    ):
        self.store = store
        self.request_json = request_json

    def connect(
        self,
        client_id: str,
        redirect_uri: str = "http://127.0.0.1:8765/callback",
        timeout: int = 180,
    ) -> None:
        parsed = urllib.parse.urlsplit(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise XConnectorError("X redirect URI must use local HTTP loopback")
        if not parsed.port:
            raise XConnectorError("X redirect URI must include a port")
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(32)
        url = authorization_url(client_id, redirect_uri, state, challenge)
        _CallbackHandler.params = {}
        server = HTTPServer((parsed.hostname, parsed.port), _CallbackHandler)
        server.timeout = timeout
        webbrowser.open(url)
        server.handle_request()
        server.server_close()
        params = _CallbackHandler.params
        if params.get("state") != state:
            raise XConnectorError(
                "X authorization timed out or returned an invalid state"
            )
        if "error" in params:
            raise XConnectorError(f"X authorization was rejected: {params['error']}")
        code = params.get("code")
        if not code:
            raise XConnectorError("X authorization did not return a code")
        token = self.request_json(
            TOKEN_URL,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            form={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        token["expires_at"] = time.time() + int(token.get("expires_in", 7200))
        self.store.save(
            "x", {"client_id": client_id, "redirect_uri": redirect_uri, "token": token}
        )

    def _access_token(self) -> str:
        credentials = self.store.load("x")
        token = credentials.get("token")
        if not isinstance(token, dict) or not token.get("access_token"):
            raise XConnectorError("X is not connected; run `socials connect x` first")
        if float(token.get("expires_at", 0)) <= time.time() + 60:
            refresh = token.get("refresh_token")
            if not refresh:
                raise XConnectorError("X token expired; reconnect X")
            token = self.request_json(
                TOKEN_URL,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                form={
                    "refresh_token": str(refresh),
                    "grant_type": "refresh_token",
                    "client_id": str(credentials["client_id"]),
                },
            )
            token["expires_at"] = time.time() + int(token.get("expires_in", 7200))
            credentials["token"] = token
            self.store.save("x", credentials)
        return str(token["access_token"])

    def fetch(self, imported_at: str, max_items: int = 100) -> list[CanonicalSave]:
        if max_items < 1:
            raise XConnectorError("max_items must be positive")
        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        me = self.request_json(f"{API_ROOT}/users/me", headers=headers).get("data", {})
        user_id = me.get("id") if isinstance(me, dict) else None
        if not user_id:
            raise XConnectorError("X did not return the authenticated user ID")
        records: list[CanonicalSave] = []
        next_token: str | None = None
        while len(records) < max_items:
            query: dict[str, str | int] = {
                "max_results": min(100, max_items - len(records)),
                "tweet.fields": "created_at,author_id",
                "expansions": "author_id",
                "user.fields": "name,username",
            }
            if next_token:
                query["pagination_token"] = next_token
            payload = self.request_json(
                f"{API_ROOT}/users/{user_id}/bookmarks?{urllib.parse.urlencode(query)}",
                headers=headers,
            )
            includes = payload.get("includes", {})
            users = includes.get("users", []) if isinstance(includes, dict) else []
            authors = {
                str(user.get("id")): user
                for user in users
                if isinstance(user, dict) and user.get("id")
            }
            for post in payload.get("data", []) or []:
                if not isinstance(post, dict) or not post.get("id"):
                    continue
                author = authors.get(str(post.get("author_id")), {})
                username = author.get("username")
                source_url = (
                    f"https://x.com/{username}/status/{post['id']}"
                    if username
                    else f"https://x.com/i/web/status/{post['id']}"
                )
                records.append(
                    CanonicalSave(
                        platform="x",
                        source_id=str(post["id"]),
                        source_url=source_url,
                        author=(f"@{username}" if username else author.get("name")),
                        published_at=post.get("created_at"),
                        collection="Bookmarks",
                        source_text=post.get("text"),
                        archive_fingerprint="live:x",
                        imported_at=imported_at,
                        archive_label="X API: Bookmarks",
                    )
                )
                if len(records) >= max_items:
                    break
            meta = payload.get("meta", {})
            next_token = meta.get("next_token") if isinstance(meta, dict) else None
            if not next_token:
                break
        return records
