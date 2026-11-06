"""Ephemeral read-only bridge to the pinned twitter-cli package.

This file runs inside a temporary ``uv --isolated`` environment. It deliberately
imports no project modules and emits one JSON object to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

PINNED_VERSION = "0.8.6"


def _deny_browser_cookies():
    return None, ["browser cookie access disabled by socials-obsidian"]


def _error(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    try:
        import twitter_cli
        from twitter_cli import auth, graphql
        from twitter_cli.client import TwitterClient
        from twitter_cli.serialization import tweets_to_data
    except Exception as exc:  # noqa: BLE001 - isolated boundary must emit JSON
        return _error(f"Cannot load pinned twitter-cli: {type(exc).__name__}: {exc}")

    if twitter_cli.__version__ != PINNED_VERSION:
        return _error(
            f"Refusing unexpected twitter-cli version {twitter_cli.__version__}"
        )

    # Fail closed: invalid/missing explicit credentials may never trigger browser,
    # profile, Keychain, or browser-cookie3 access.
    auth.extract_from_browser = _deny_browser_cookies
    auth._extract_in_process = _deny_browser_cookies
    auth._extract_via_subprocess = _deny_browser_cookies
    sys.modules["browser_cookie3"] = None

    # Do not consult a third-party GitHub query-ID service. The audited pinned
    # fallback is used first; X's own frontend bundles remain its only fallback.
    graphql._fetch_from_github = lambda *_args, **_kwargs: None

    if args.self_test:
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": twitter_cli.__version__,
                    "browser_cookie_fallback": "disabled",
                }
            )
        )
        return 0

    if args.max_items is None or not 1 <= args.max_items <= 30:
        return _error("max-items must be between 1 and 30")
    if not os.environ.get("TWITTER_AUTH_TOKEN") or not os.environ.get("TWITTER_CT0"):
        return _error("Explicit ephemeral X cookies are required")

    try:
        cookies = auth.get_cookies()
        client = TwitterClient(
            cookies["auth_token"],
            cookies["ct0"],
            rate_limit_config={
                "requestDelay": 3.0,
                "maxRetries": 1,
                "retryBaseDelay": 5.0,
                "maxCount": 30,
            },
            cookie_string=None,
        )
        tweets = client.fetch_bookmarks(args.max_items)
        print(
            json.dumps(
                {"ok": True, "data": tweets_to_data(tweets[: args.max_items])},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - upstream errors must not escape
        return _error(f"X bookmark read failed: {type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("TWITTER_AUTH_TOKEN", None)
        os.environ.pop("TWITTER_CT0", None)


if __name__ == "__main__":
    raise SystemExit(main())
