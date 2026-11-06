# Social Saves → Obsidian

Local-first synchronization of social saves into Obsidian. The recommended live connections use documented, user-authorized APIs: Telegram Saved Messages through Telegram's user API and X Bookmarks through OAuth 2.0 PKCE. Official export import remains available for LinkedIn, Instagram, X, and Telegram.

Explicitly opt-in, read-only wrappers are also available for users who accept the account risk: an X cookie reader and a temporary visible-Chrome LinkedIn Saved Posts reader. Neither exposes social write commands or scheduling. No connector downloads Telegram media.

## Clone onto another Mac

```bash
gh repo clone hex41434/Social-to-markdown
cd Social-to-markdown
uv sync
cp config.example.toml config.toml
```

Install `uv` and Ollama if needed. Before running the app, edit `config.toml`:
set `vault_path` to your Obsidian vault on the new Mac, and set `data_dir` to
`"data"` when running commands from the repository directory. Then run:

```bash
ollama pull qwen3:4b-instruct
uv run socials doctor
uv run socials dashboard
```

Reconnect the accounts you use on the new Mac. Local credentials, sessions,
imports, database state, logs, and `config.toml` are intentionally excluded
from Git. To install the desktop launcher for this checkout, run
`bash scripts/install_control_center_app.sh`. Reinstall it after moving the checkout.

## Setup

```bash
cd Social-to-markdown
uv sync
ollama pull qwen3:4b-instruct
uv run socials doctor
```

Generated notes go to `<vault_path>/Social Saves/Items/<platform>/`. Credentials, raw exports, and SQLite state stay under the Git-ignored project `data/` directory and never enter Obsidian.

## Connect Telegram — free and official

1. Sign in at [my.telegram.org](https://my.telegram.org), open **API development tools**, and create a personal application.
2. Copy its `api_id` and `api_hash`.
3. Run:

```bash
uv run socials connect telegram
```

The CLI prompts for the API ID, API hash, phone number, Telegram login code, and two-step-verification password when enabled. It creates a private local Telethon session with mode `0600`; it does not ask for or reuse Telegram Web/Desktop cookies.

Read Saved Messages and write/update Obsidian notes:

```bash
uv run socials sync telegram
```

Use `--limit 100` for only the newest 100 messages, or `--dry-run` to preview counts. Text and forward metadata are read; media bytes are not downloaded.

Telegram documents Saved Messages as the private chat with `inputPeerSelf`: [official Saved Messages API](https://core.telegram.org/api/saved-messages).

## Connect X — official but paid

X exposes private bookmarks through its official API, but requires a developer app and charges owned reads. Current official pricing is `$0.001` per bookmark resource.

1. Create an app in the [X Developer Console](https://developer.x.com/en/portal/dashboard).
2. Enable OAuth 2.0 for a **Native App / public client**.
3. Configure this exact callback URL: `http://127.0.0.1:8765/callback`.
4. Run:

```bash
uv run socials connect x
```

The browser opens X's own consent page. The app requests only `bookmark.read`, `tweet.read`, `users.read`, and `offline.access`; tokens are kept in the private local credential file.

To sync at most 100 bookmarks (maximum current estimate `$0.100`):

```bash
uv run socials sync x --max-items 100 --allow-paid
```

Without `--allow-paid`, the CLI refuses to make the potentially billable bookmark call. See the [official bookmark endpoint](https://docs.x.com/x-api/users/get-bookmarks), [OAuth PKCE guide](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code), and [pricing](https://docs.x.com/x-api/getting-started/pricing).

## X bookmarks — free, unofficial, and not risk-free

This narrow exception uses the open-source [twitter-cli](https://github.com/public-clis/twitter-cli) bookmark reader. It is pinned to a reviewed Git commit and launched in a fresh `uv --isolated` child environment. The wrapper calls only the bookmark-fetch function; it exposes no post, like, follow, delete, or other write command.

Run one small, manual import:

```bash
uv run socials sync x-cookie --max-items 20 --acknowledge-risk
```

The hard maximum is 30 bookmarks per run. There is no scheduler. Put only the two raw values in the private, Git-ignored file `data/credentials/x-cookie.env`:

```dotenv
AUTH_TOKEN=paste_only_the_auth_token_value_here
CT0=paste_only_the_ct0_value_here
```

Do not add cookie names inside the values, quotes, or a combined header. The file stays in place across imports and has mode `0600`; the wrapper never writes its contents to Obsidian, logs, command arguments, or shell history. Never paste either value into chat. Delete the file or log out of X when you want to revoke the saved session.

Browser-cookie discovery is disabled, so the wrapper cannot inspect Safari, Chrome, Firefox, a keychain, or another profile. If the supplied credentials fail, it stops instead of falling back to browser extraction.

This is still unofficial website automation and the risk is not zero. X says non-API website scripting may result in permanent suspension; the official OAuth connector above is the safer choice. Read the [X automation rules](https://help.x.com/en/rules-and-policies/x-automation?lang=browser) before opting in. Each run requires the explicit `--acknowledge-risk` flag.

## LinkedIn Saved Posts — temporary browser, unofficial, and not risk-free

LinkedIn does not expose ordinary Saved Posts through a general self-service API. The optional browser reader is therefore unofficial. It is inspired by the idea in [link2notion](https://github.com/vampirepapi/link2notion), but deliberately does not reuse that project's password `.env`, automatic credential entry, persistent login, or 200-scroll loop.

Run only when you want a manual snapshot:

```bash
uv run socials sync linkedin-browser --max-items 20 --acknowledge-risk
```

A fresh visible Chrome window opens. Log in and complete any verification manually in that window. The reader visits only LinkedIn Saved Posts, performs at most three scrolls, reads at most 20 result cards, closes Chrome, and discards the entire temporary browser session. It does not read the normal Chrome profile, retain cookies, receive an email/password, or expose write actions. There is no scheduler.

These limits reduce activity but do not make browser automation account-safe. LinkedIn prohibits unauthorized scraping and automation; official export import remains the safer option. See [LinkedIn prohibited software](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions).

## LinkedIn official access and Instagram: the honest limitation

- **Instagram:** Meta does not expose the user's Saved Posts collection through its official Instagram API. `socials connect instagram` therefore refuses instead of scraping or reusing cookies.
- **LinkedIn official API:** ordinary self-service LinkedIn API scopes do not expose Saved Items. LinkedIn's EU/EEA portability product may expose saved-item activity, but requires an approved application. `socials connect linkedin` reports that requirement. Most LinkedIn API permissions require explicit approval: [official access documentation](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access).

For both services, official exports remain the implemented account-safe ingestion method:

```bash
uv run socials import /path/to/official-export.zip
```

## Commands

```bash
uv run socials connect telegram
uv run socials sync telegram [--limit N] [--dry-run]
uv run socials connect x
uv run socials sync x --max-items N --allow-paid [--dry-run]
uv run socials sync x-cookie --max-items N --acknowledge-risk [--dry-run]
uv run socials sync linkedin-browser --max-items N --acknowledge-risk [--dry-run]
uv run socials dashboard
uv run socials install-chrome-extension
uv run socials import [paths...] [--dry-run]
uv run socials enrich --pending
uv run socials enrich --all
uv run socials enrich --language-mismatches
# Optional stronger local model for stubborn language mismatches:
uv run socials enrich --language-mismatches --model qwen3.5:latest
uv run socials status
uv run socials rebuild-index
uv run socials doctor
```

Re-syncs deduplicate by platform/source ID and never delete notes because an item disappeared remotely. User frontmatter, user tags, and everything under permanent `## My Notes` are preserved. Titles, summaries, displayed topics, and topical tags use the source language; Persian remains Persian. `enrich --all` regenerates existing notes from locally stored text without contacting a social account. Only local Ollama receives eligible source text.

## AiFa Social visual Control Center

The optional Control Center opens in Google Chrome and lets you choose X, LinkedIn, or Telegram, set a count, and watch capture, import, and local-AI progress. Instagram remains export-only.

One-time setup for LinkedIn on the normal signed-in Chrome profile:

```bash
uv run socials install-chrome-extension
```

Chrome opens `chrome://extensions/` and Finder reveals `chrome-extension/manifest.json`. Turn on **Developer mode**, choose **Load unpacked**, and select the containing `chrome-extension` folder. The extension runs only on the exact LinkedIn Saved Posts page when a Control Center job ID is present. It cannot read cookies, storage, passwords, or unrelated pages.

Install the clickable macOS app on the Desktop:

```bash
./scripts/install_control_center_app.sh
```

The app binds only to `127.0.0.1`, has no scheduler, and stops after 30 minutes without a started or progressing job. X uses the existing private X credential file, LinkedIn uses the current Chrome login through the narrow extension, and Telegram uses the existing local session.

## Hermes

The project skill is registered in Hermes. Use:

```text
/socials-obsidian connect telegram
/socials-obsidian sync telegram
/socials-obsidian connect x
/socials-obsidian sync x --max-items 100 --allow-paid
/socials-obsidian sync x-cookie --max-items 20 --acknowledge-risk
/socials-obsidian sync linkedin-browser --max-items 20 --acknowledge-risk
/socials-obsidian dashboard
/socials-obsidian status
```

Hermes invokes the deterministic CLI and reports counts. The skill must not read saved-message content into the cloud-model context.

## Private storage and safety

- `data/credentials.json` and `data/credentials/*.session` are mode `0600`, Git-ignored, and outside the vault.
- Telegram uses only its API and reads only Saved Messages; it never downloads media.
- X uses OAuth 2.0 PKCE, read-only scopes, refresh tokens, rate limits, and an explicit paid-call acknowledgement.
- The optional X cookie wrapper is manually invoked, read-only, capped at 30, pinned to an exact upstream commit, and isolated from browser cookie stores. At the user's request, its private `0600` credential file persists until manually removed. These controls reduce but do not eliminate suspension risk.
- The optional LinkedIn browser wrapper is manually invoked, read-only, capped at 20, limited to three scrolls, and uses a fresh visible Chrome session that is discarded after every run. It never receives login credentials or reuses the normal Chrome profile. These controls reduce but do not eliminate restriction risk.
- The optional Control Center binds only to loopback, has no scheduler, and stops after 30 idle minutes. Its unpacked Chrome extension is limited to the exact LinkedIn Saved Posts page and job URL, at most 20 result cards and three scrolls, with no cookie, storage, or password permissions. It uses the normal Chrome login, so LinkedIn's unofficial-automation restriction risk still applies.
- Export ZIP members are read in memory from an allowlist and never extracted.
- Raw exports are retained under `data/processed/`; notes are never automatically deleted.
- Ollama is restricted to a loopback address.
- Obsidian Sync may sync generated notes, but credentials, sessions, raw exports, and SQLite state remain outside the vault.

Except for the reviewed X bookmark wrapper and temporary LinkedIn Saved Posts wrapper described above, this project intentionally excludes Twikit, Instagrapi, general Selenium/Playwright scraping, session-token extraction, write automation, scheduling, and evasion techniques.
