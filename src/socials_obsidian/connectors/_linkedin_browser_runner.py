"""Temporary visible-Chrome LinkedIn Saved Posts reader.

The user authenticates manually in a new temporary Chrome session. The session
is discarded when the process exits. This module receives no credentials and
emits exactly one JSON object to stdout.
"""

from __future__ import annotations

import argparse
import json
import time
from importlib.metadata import version

PINNED_VERSION = "1.54.0"
SAVED_POSTS_URL = "https://www.linkedin.com/my-items/saved-posts/"
MAX_ITEMS = 20
MAX_SCROLLS = 3


def _error(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def _login_required(url: str) -> bool:
    return any(part in url for part in ("/login", "/signin", "/checkpoint"))


def _wait_for_manual_login(page, timeout_seconds: int = 300) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if page.url.startswith("https://www.linkedin.com/") and not _login_required(
            page.url
        ):
            return True
        page.wait_for_timeout(2000)
    return False


def _extract_card(card) -> dict:
    return card.evaluate(
        r"""
        node => {
          const firstText = selectors => {
            for (const selector of selectors) {
              const element = node.querySelector(selector);
              const value = element?.innerText?.trim();
              if (value) return value;
            }
            return '';
          };
          const author = firstText([
            ".entity-result__title-text span[aria-hidden='true']",
            "a[href*='/in/'] span[aria-hidden='true']",
            "span[dir='ltr'] span[aria-hidden='true']"
          ]);
          const body = firstText([
            "p.entity-result__content-summary",
            ".entity-result__content-summary",
            ".entity-result__summary"
          ]).replace(/(?:…|\.\.\.)see more\s*$/i, '').trim();
          const link = node.querySelector(
            "a[href*='/feed/update/'], a[href*='/posts/']"
          );
          return {
            urn: node.getAttribute('data-chameleon-result-urn') || '',
            author,
            body,
            url: link?.href || ''
          };
        }
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except (ImportError, OSError) as exc:
        return _error(f"Cannot load pinned Playwright: {type(exc).__name__}: {exc}")
    if version("playwright") != PINNED_VERSION:
        return _error(f"Refusing unexpected Playwright version {version('playwright')}")
    if args.self_test:
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": version("playwright"),
                    "headless": False,
                    "credentials_received": False,
                    "persistent_profile": False,
                    "max_items": MAX_ITEMS,
                    "max_scrolls": MAX_SCROLLS,
                }
            )
        )
        return 0
    if not 1 <= args.max_items <= MAX_ITEMS:
        return _error("max-items must be between 1 and 20")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            context = browser.new_context(viewport={"width": 1280, "height": 850})
            try:
                page = context.new_page()
                page.goto(SAVED_POSTS_URL, wait_until="domcontentloaded", timeout=60000)
                if _login_required(page.url) and not _wait_for_manual_login(page):
                    return _error("Manual LinkedIn login timed out after five minutes")

                page.goto(SAVED_POSTS_URL, wait_until="domcontentloaded", timeout=60000)
                selector = (
                    "div[data-chameleon-result-urn], "
                    "li.reusable-search__result-container, .entity-result"
                )
                try:
                    page.wait_for_selector(selector, timeout=30000)
                except TimeoutError:
                    return _error(
                        "No LinkedIn Saved Posts were found; the page may be empty or changed"
                    )

                previous_count = 0
                unchanged = 0
                for _ in range(MAX_SCROLLS):
                    count = page.locator(selector).count()
                    if count >= args.max_items:
                        break
                    unchanged = unchanged + 1 if count <= previous_count else 0
                    if unchanged >= 2:
                        break
                    previous_count = count
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(4000)

                cards = page.locator(selector)
                data = [
                    _extract_card(cards.nth(index))
                    for index in range(min(cards.count(), args.max_items))
                ]
                print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
                return 0
            finally:
                context.close()
                browser.close()
    except (Error, OSError) as exc:
        return _error(f"LinkedIn browser failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
