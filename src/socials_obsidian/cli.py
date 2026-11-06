from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from .config import load_config
from .connectors import (
    LinkedInBrowserConnector,
    LinkedInBrowserConnectorError,
    TelegramConnector,
    TelegramConnectorError,
    XConnector,
    XConnectorError,
    XCookieConnector,
    XCookieConnectorError,
    load_cookie_values,
)
from .credentials import CredentialError, CredentialStore
from .dashboard import DashboardError, run_dashboard
from .ollama import OllamaClient, OllamaError
from .service import ImportService, utc_now

app = typer.Typer(
    no_args_is_help=True,
    help="Sync social saves into Obsidian through official APIs or official exports.",
)
connect_app = typer.Typer(help="Authorize an official live account connection.")
sync_app = typer.Typer(
    help="Read saved items through an explicitly selected connector."
)
app.add_typer(connect_app, name="connect")
app.add_typer(sync_app, name="sync")


def _print_result(result) -> None:
    typer.echo(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))


@app.command()
def doctor() -> None:
    """Check paths, privacy constraints, and local Ollama readiness."""
    config = load_config()
    config.ensure_local_dirs()
    issues = config.validate(require_vault=True)
    typer.echo(f"Project: {config.project_root}")
    typer.echo(f"Vault: {config.vault_path}")
    typer.echo(f"Inbox: {config.inbox_dir}")
    typer.echo(f"Notes: {config.notes_root}")
    credentials = CredentialStore(config.credentials_path)
    typer.echo(
        "Telegram live connection: "
        + (
            "configured"
            if credentials.configured("telegram")
            and (config.credentials_dir / "telegram.session").exists()
            else "not configured"
        )
    )
    typer.echo(
        "X live connection: "
        + ("configured" if credentials.configured("x") else "not configured")
    )
    try:
        client = OllamaClient(config.ollama_base_url, config.ollama_model, timeout=5)
        models = client.available_models()
        if client.is_ready():
            typer.echo(f"Ollama: ready ({config.ollama_model})")
        else:
            issues.append(
                f"Ollama is running but {config.ollama_model} is not installed; run: ollama pull {config.ollama_model}"
            )
            typer.echo("Ollama models: " + (", ".join(models) if models else "none"))
    except OllamaError:
        issues.append(
            "Ollama is not reachable; metadata imports still work and enrichment can be retried later"
        )
    if issues:
        for issue in issues:
            typer.echo(f"WARNING: {issue}", err=True)
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")


@connect_app.command("telegram")
def connect_telegram(
    api_id: Annotated[
        int | None,
        typer.Option(help="Telegram API ID from my.telegram.org; prompted if omitted"),
    ] = None,
    phone: Annotated[
        str | None,
        typer.Option(
            help="Your phone number in international format; prompted if omitted"
        ),
    ] = None,
) -> None:
    """Authorize the free Telegram user API and store a local session."""
    config = load_config()
    config.ensure_local_dirs()
    store = CredentialStore(config.credentials_path)
    previous = store.load("telegram")
    resolved_api_id = (
        api_id or previous.get("api_id") or typer.prompt("Telegram API ID", type=int)
    )
    api_hash = previous.get("api_hash") or typer.prompt(
        "Telegram API hash", hide_input=True
    )
    resolved_phone = phone or typer.prompt("Telegram phone (+country code)")
    connector = TelegramConnector(
        int(resolved_api_id), str(api_hash), config.credentials_dir / "telegram"
    )
    try:
        identity = asyncio.run(connector.connect(resolved_phone))
    except TelegramConnectorError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    store.save("telegram", {"api_id": int(resolved_api_id), "api_hash": str(api_hash)})
    typer.echo(f"Telegram connected as {identity}. Session is local and Git-ignored.")


@connect_app.command("x")
def connect_x(
    client_id: Annotated[
        str | None,
        typer.Option(help="OAuth 2.0 Client ID from the X Developer Console"),
    ] = None,
    redirect_uri: Annotated[
        str,
        typer.Option(help="Must exactly match the callback configured for the X app"),
    ] = "http://127.0.0.1:8765/callback",
) -> None:
    """Authorize X bookmarks through official OAuth 2.0 PKCE."""
    config = load_config()
    config.ensure_local_dirs()
    resolved_client_id = client_id or typer.prompt("X OAuth 2.0 Client ID")
    typer.echo("Opening X consent in your browser; waiting up to three minutes...")
    try:
        XConnector(CredentialStore(config.credentials_path)).connect(
            resolved_client_id, redirect_uri
        )
    except (XConnectorError, CredentialError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("X connected with bookmark.read only; refresh token stored locally.")


@connect_app.command("instagram")
def connect_instagram() -> None:
    """Explain why Saved Posts cannot be connected safely."""
    typer.echo(
        "Instagram does not expose a Saved Posts collection through its official API. "
        "This project will not scrape it or reuse browser cookies because that risks the account.",
        err=True,
    )
    raise typer.Exit(code=2)


@connect_app.command("linkedin")
def connect_linkedin() -> None:
    """Explain the approval requirement for LinkedIn saved-item access."""
    typer.echo(
        "LinkedIn Saved Items are not available through general self-service API permissions. "
        "A live connector requires LinkedIn approval for the EU/EEA portability product; "
        "for an explicitly risk-accepted temporary browser read, use "
        "`socials sync linkedin-browser --acknowledge-risk`.",
        err=True,
    )
    raise typer.Exit(code=2)


@sync_app.command("telegram")
def sync_telegram(
    limit: Annotated[
        int,
        typer.Option(min=0, help="Newest messages to read; 0 reads all Saved Messages"),
    ] = 0,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Read and report without writing notes")
    ] = False,
) -> None:
    """Read Telegram Saved Messages through the authorized user API."""
    config = load_config()
    store = CredentialStore(config.credentials_path)
    credentials = store.load("telegram")
    if not credentials:
        typer.echo("ERROR: run `socials connect telegram` first", err=True)
        raise typer.Exit(code=2)
    connector = TelegramConnector(
        int(credentials["api_id"]),
        str(credentials["api_hash"]),
        config.credentials_dir / "telegram",
    )
    try:
        records = asyncio.run(connector.fetch(utc_now(), limit or None))
    except TelegramConnectorError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    result = ImportService(config).import_live_records(records, dry_run=dry_run)
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@sync_app.command("linkedin-browser")
def sync_linkedin_browser(
    max_items: Annotated[
        int,
        typer.Option(
            min=1,
            max=20,
            help="Hard-capped number of Saved Posts; cannot exceed 20",
        ),
    ] = 20,
    acknowledge_risk: Annotated[
        bool,
        typer.Option(
            "--acknowledge-risk",
            help="Acknowledge that unofficial LinkedIn browser automation may restrict the account",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Read and report without writing notes")
    ] = False,
) -> None:
    """Temporarily open Chrome, manually log in, and read LinkedIn Saved Posts."""
    if not acknowledge_risk:
        typer.echo(
            "Refusing unofficial LinkedIn browser access without --acknowledge-risk. "
            "LinkedIn may restrict accounts for automated website access.",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo(
        "Opening a temporary Chrome session. Log in to LinkedIn manually in that window. "
        "No email, password, cookie file, or browser profile will be retained.",
        color=False,
    )
    typer.echo(
        f"Reading at most {max_items} Saved Posts with no more than three scrolls...",
        color=False,
    )
    try:
        records = LinkedInBrowserConnector().fetch(utc_now(), max_items=max_items)
    except LinkedInBrowserConnectorError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Received {len(records)} LinkedIn Saved Posts; the temporary session is closed.",
        color=False,
    )
    config = load_config()
    result = ImportService(config).import_live_records(records, dry_run=dry_run)
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@sync_app.command("x")
def sync_x(
    max_items: Annotated[
        int,
        typer.Option(min=1, max=1000, help="Maximum bookmark resources to request"),
    ] = 100,
    allow_paid: Annotated[
        bool,
        typer.Option(
            "--allow-paid",
            help="Acknowledge official X owned-read charges (currently $0.001/resource)",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Read and report without writing notes")
    ] = False,
) -> None:
    """Read X bookmarks through the official paid API."""
    if not allow_paid:
        typer.echo(
            f"Refusing a potentially billable API call. Maximum estimate: ${max_items * 0.001:.3f}. "
            "Re-run with --allow-paid.",
            err=True,
        )
        raise typer.Exit(code=2)
    config = load_config()
    try:
        records = XConnector(CredentialStore(config.credentials_path)).fetch(
            utc_now(), max_items=max_items
        )
    except (XConnectorError, CredentialError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    result = ImportService(config).import_live_records(records, dry_run=dry_run)
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@sync_app.command("x-cookie")
def sync_x_cookie(
    max_items: Annotated[
        int,
        typer.Option(
            min=1,
            max=30,
            help="Hard-capped number of bookmarks; cannot exceed 30",
        ),
    ] = 20,
    acknowledge_risk: Annotated[
        bool,
        typer.Option(
            "--acknowledge-risk",
            help="Acknowledge that this unofficial cookie-based read may restrict the X account",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Read and report without writing notes")
    ] = False,
) -> None:
    """Unofficial, read-only X bookmark import using a private credential file."""
    if not acknowledge_risk:
        typer.echo(
            "Refusing unofficial X access without --acknowledge-risk. "
            "X may restrict or permanently suspend accounts for non-API automation.",
            err=True,
        )
        raise typer.Exit(code=2)
    config = load_config()
    config.ensure_local_dirs()
    try:
        auth_token, ct0 = load_cookie_values(config.x_cookie_path)
        typer.echo(
            f"Credentials loaded from {config.x_cookie_path} (the file will be retained).",
            color=False,
        )
        typer.echo(
            f"Fetching up to {max_items} X bookmarks. The first run may download "
            "the pinned reader; timeout is 180 seconds...",
            color=False,
        )
        records = XCookieConnector().fetch(
            auth_token, ct0, utc_now(), max_items=max_items
        )
        typer.echo(
            f"Received {len(records)} bookmarks; updating Obsidian...", color=False
        )
    except XCookieConnectorError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        auth_token = ""
        ct0 = ""
    result = ImportService(config).import_live_records(records, dry_run=dry_run)
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@app.command("import")
def import_exports(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="Export files or directories; defaults to data/inbox"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Parse and report without writing or moving files"
        ),
    ] = False,
) -> None:
    """Import official export files into Obsidian."""
    config = load_config()
    issues = config.validate(require_vault=True)
    if issues:
        for issue in issues:
            typer.echo(f"ERROR: {issue}", err=True)
        raise typer.Exit(code=2)
    result = ImportService(config).import_paths(paths or [], dry_run=dry_run)
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@app.command()
def enrich(
    pending: Annotated[
        bool,
        typer.Option("--pending", help="Retry all items without a complete summary"),
    ] = False,
    all_items: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Regenerate every summary from local stored text; does not contact social accounts",
        ),
    ] = False,
    language_mismatches: Annotated[
        bool,
        typer.Option(
            "--language-mismatches",
            help="Repair same-language violations from local text only",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help="Override the configured local Ollama model for this run",
        ),
    ] = None,
) -> None:
    """Generate same-language titles, summaries, and topical tags with local Ollama."""
    if sum((pending, all_items, language_mismatches)) > 1:
        raise typer.BadParameter(
            "Choose only one of --pending, --all, or --language-mismatches"
        )
    config = load_config()
    service = ImportService(
        config,
        ollama=(
            OllamaClient(config.ollama_base_url, model, timeout=300) if model else None
        ),
    )
    if all_items:
        typer.echo(
            "Re-enriching all items from local stored text; no social account will be contacted..."
        )
        result = service.enrich_all(
            lambda position, total, key: typer.echo(
                f"[{position}/{total}] {key}", color=False
            )
        )
    elif language_mismatches:
        typer.echo(
            "Repairing language mismatches from local stored text; no social account will be contacted..."
        )
        result = service.enrich_language_mismatches(
            lambda position, total, key: typer.echo(
                f"[{position}/{total}] {key}", color=False
            )
        )
    else:
        result = service.enrich_pending(
            lambda position, total, key: typer.echo(
                f"[{position}/{total}] {key}", color=False
            )
        )
    _print_result(result)
    if result.failed:
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Show local archive and item counts."""
    service = ImportService(load_config())
    typer.echo(json.dumps(service.state.counts(), indent=2))


@app.command("rebuild-index")
def rebuild_index() -> None:
    """Rebuild the Obsidian Social Saves index."""
    path = ImportService(load_config()).rebuild_index()
    typer.echo(str(path))


@app.command()
def dashboard(
    port: Annotated[
        int, typer.Option(min=1024, max=65535, help="Loopback dashboard port")
    ] = 8766,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Do not open the default browser")
    ] = False,
) -> None:
    """Open the local Social Saves Control Center."""
    try:
        run_dashboard(load_config(), port=port, open_browser=not no_open)
    except (DashboardError, OSError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("install-chrome-extension")
def install_chrome_extension() -> None:
    """Open Chrome's extension page and reveal the local unpacked extension."""
    extension_path = load_config().project_root / "chrome-extension"
    if not (extension_path / "manifest.json").is_file():
        typer.echo(f"ERROR: extension not found: {extension_path}", err=True)
        raise typer.Exit(code=1)
    typer.echo("One-time Chrome setup:")
    typer.echo("1. Enable Developer mode in chrome://extensions")
    typer.echo("2. Click Load unpacked")
    typer.echo(f"3. Select: {extension_path}")
    subprocess.run(["open", "-a", "Google Chrome", "chrome://extensions/"], check=False)
    subprocess.run(["open", "-R", str(extension_path / "manifest.json")], check=False)


if __name__ == "__main__":
    app()
