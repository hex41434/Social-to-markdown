---
name: socials-obsidian
description: Connect, sync, import, enrich, and report on social saves in the user's Obsidian vault using official Telegram and X APIs, tightly constrained opt-in X and LinkedIn readers, or official social exports. Use when the user asks to connect a social account, sync Telegram Saved Messages, X Bookmarks, or LinkedIn Saved Posts, import social saves, retry local summaries, check status, rebuild the index, or diagnose the local pipeline.
---

# Social Saves to Obsidian

Use the deterministic CLI in the cloned repository directory. Do not read social content into the agent context; let the CLI send eligible text directly to local Ollama.

## Procedure

1. Work from the cloned repository directory.
2. Map the request to exactly one command:
   - Connect Telegram: `uv run socials connect telegram`
   - Sync Telegram: `uv run socials sync telegram`
   - Connect X: `uv run socials connect x`
   - Sync X through the official API: `uv run socials sync x --max-items <N> --allow-paid`
   - Sync X through the unofficial, read-only cookie wrapper: `uv run socials sync x-cookie --max-items <N> --acknowledge-risk`
   - Sync LinkedIn through the unofficial temporary-browser wrapper: `uv run socials sync linkedin-browser --max-items <N> --acknowledge-risk`
   - Open the visual Control Center: `uv run socials dashboard`
   - Reveal the one-time LinkedIn Chrome extension setup: `uv run socials install-chrome-extension`
   - Diagnose: `uv run socials doctor`
   - Preview inbox: `uv run socials import --dry-run`
   - Import inbox: `uv run socials import`
   - Import paths: `uv run socials import <path>...`
   - Retry summaries: `uv run socials enrich --pending`
   - Regenerate all titles/topics from local text: `uv run socials enrich --all`
   - Repair language mismatches only: `uv run socials enrich --language-mismatches`
   - Report status: `uv run socials status`
   - Rebuild index: `uv run socials rebuild-index`
3. For connect or sync, run `doctor` first. Telegram needs a one-time interactive login using the user's own `api_id`, `api_hash`, phone, login code, and optional 2FA password. Official X needs the user's OAuth Client ID and its consent page; never request an access token in chat.
4. Never run `sync x` without the user's explicit acknowledgement of the estimated paid resource count. The CLI enforces this with `--allow-paid`.
5. Use `sync x-cookie` only after the user explicitly chooses the unofficial risk-bearing option for that run. Keep `<N>` at 20 by default and never above 30. The user must put the `auth_token` and `ct0` values themselves in the private `data/credentials/x-cookie.env` file; never read the file, ask them to send either value in chat, or expose it to the agent context. The CLI enforces the decision with `--acknowledge-risk`.
6. Use `sync linkedin-browser` only after the user explicitly accepts the unofficial browser-automation risk for that run. Never exceed 20 items or three scrolls. The user performs login/2FA manually in the temporary Chrome window; never request or handle their LinkedIn email, password, cookies, or normal Chrome profile. The session must be discarded after the run.
7. The visual Control Center may use the user's normal signed-in Chrome only through the project extension. Require the on-screen risk acknowledgement for X or LinkedIn. Never exceed the dashboard caps or broaden the extension's URL or permissions.
8. Return the CLI's created, updated, skipped, pending, failed, and archive counts. Mention the generated index path only when useful.

## Guardrails

- Prefer the project's Telegram user-API connector and official X OAuth connector. The sole cookie exception is the project's reviewed `sync x-cookie` wrapper: bookmarks only, manual invocation only, at most 30 items, no writes, no scheduler, no browser-cookie discovery, and no credential persistence.
- Never obtain cookies from a browser, browser extension, profile directory, keychain, clipboard, log, command argument, or chat. The sole file exception is `data/credentials/x-cookie.env`, which only the user edits and the deterministic CLI reads; the agent must never inspect its contents.
- Telethon is allowed only inside `socials connect/sync telegram`, scoped to Saved Messages and without media downloads. Never invoke Twikit, Instagrapi, Selenium, or general browser automation. The sole Playwright exception is the deterministic `sync linkedin-browser` wrapper with its hard-coded temporary-session limits.
- The Control Center must remain loopback-only, manually invoked, unscheduled, and auto-stopping. Its Chrome extension may run only on the exact LinkedIn Saved Posts job URL, with no cookie, storage, password, or broad browsing permissions.
- Do not claim official live Saved Posts support for Instagram or LinkedIn. LinkedIn's temporary browser reader is explicitly unofficial; Instagram remains export-only.
- Never send exported content to Hermes' configured language model or another cloud service.
- Never delete raw exports or Obsidian notes. The CLI moves successful inbox files to `data/processed/` and retains them.
- Treat `data/`, `config.toml`, `logs/`, and the state database as private and keep them out of Git.

## Verification

Treat a command as successful only when it exits cleanly or reports solely pending URL-only summaries. If failures are reported, quote the affected local filename or stable item key and the concise error without exposing saved content.
