from __future__ import annotations

import json
from pathlib import Path

from conftest import FakeOllama

from socials_obsidian.models import CanonicalSave, Enrichment
from socials_obsidian.notes import parse_note
from socials_obsidian.service import ImportService


def telegram_export(path: Path, text: str, include_second: bool = True) -> None:
    messages = [
        {"id": 1, "type": "message", "date": "2026-08-01T10:00:00", "text": text},
    ]
    if include_second:
        messages.append(
            {"id": 2, "type": "message", "date": "2026-08-02T10:00:00", "text": "short"}
        )
    path.write_text(
        json.dumps({"name": "Saved Messages", "messages": messages}), encoding="utf-8"
    )


def test_end_to_end_import_and_idempotency(app_config, tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    telegram_export(
        export,
        "This exported Telegram message is long enough for a factual local summary about knowledge management and safe archives.",
    )
    fake = FakeOllama()
    service = ImportService(app_config, ollama=fake)
    first = service.import_paths([export])
    assert first.created == 2
    assert first.pending == 1
    assert fake.calls == 1
    assert service.state.counts() == {"items": 2, "archives": 1, "pending": 1}

    notes = sorted((app_config.notes_root / "Items" / "telegram").glob("*.md"))
    assert len(notes) == 2
    assert (app_config.notes_root / "Social Saves.md").exists()
    complete = next(
        path for path in notes if "Summary of telegram item" in path.read_text()
    )
    content = complete.read_text()
    assert "summary_status: complete" in content
    assert "## My Notes" in content

    second = service.import_paths([export])
    assert second.skipped == 1
    assert service.state.counts()["items"] == 2


def test_update_preserves_user_metadata_and_notes(app_config, tmp_path: Path) -> None:
    first_export = tmp_path / "first.json"
    telegram_export(
        first_export,
        "A long original exported message about reliable note systems and careful local processing.",
        False,
    )
    service = ImportService(app_config, ollama=FakeOllama())
    service.import_paths([first_export])
    item = service.state.all_items()[0]
    original = item.note_path.read_text()
    frontmatter, body = parse_note(original)
    frontmatter["favorite"] = True
    frontmatter["tags"].append("personal/important")
    import yaml

    edited = (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False)
        + "---\n\n"
        + body.replace("## My Notes\n", "## My Notes\n\nMy durable observation.\n")
    )
    item.note_path.write_text(edited, encoding="utf-8")

    second_export = tmp_path / "second.json"
    telegram_export(
        second_export,
        "A revised and still sufficiently long exported message about reliable note systems and local processing.",
        False,
    )
    result = service.import_paths([second_export])
    assert result.updated == 1
    updated = item.note_path.read_text()
    assert "favorite: true" in updated
    assert "personal/important" in updated
    assert "My durable observation." in updated


def test_later_export_never_deletes_missing_items(app_config, tmp_path: Path) -> None:
    full = tmp_path / "full.json"
    telegram_export(
        full,
        "A long saved message about local archives, durable metadata, and cautious processing for personal knowledge.",
    )
    service = ImportService(app_config, ollama=FakeOllama())
    service.import_paths([full])
    paths = {
        item.record.source_id: item.note_path for item in service.state.all_items()
    }

    partial = tmp_path / "partial.json"
    telegram_export(
        partial,
        "A changed long saved message about local archives and durable metadata for personal knowledge.",
        False,
    )
    service.import_paths([partial])
    assert paths["2"].exists()
    assert service.state.counts()["items"] == 2


def test_dry_run_writes_no_notes_or_archive_state(app_config, tmp_path: Path) -> None:
    export = tmp_path / "result.json"
    telegram_export(
        export,
        "A long message that would normally be summarized locally and written to the Obsidian vault.",
        False,
    )
    service = ImportService(app_config, ollama=FakeOllama())
    result = service.import_paths([export], dry_run=True)
    assert result.created == 1
    assert service.state.counts()["items"] == 0
    assert not app_config.notes_root.exists()


def test_failed_ollama_keeps_metadata_note_retryable(
    app_config, tmp_path: Path
) -> None:
    export = tmp_path / "result.json"
    telegram_export(
        export,
        "This message has enough exported content but the local model is unavailable during the initial import.",
        False,
    )
    service = ImportService(app_config, ollama=FakeOllama(fail=True))
    result = service.import_paths([export])
    assert result.created == 1
    assert result.pending == 1
    item = service.state.all_items()[0]
    assert item.summary_status == "failed"
    assert "Summary unavailable" in item.note_path.read_text()


def test_live_sync_is_idempotent_and_reuses_existing_summary(app_config) -> None:
    fake = FakeOllama()
    service = ImportService(app_config, ollama=fake)
    record = CanonicalSave(
        platform="telegram",
        source_id="live-1",
        saved_at="2026-08-04T10:00:00Z",
        source_text=(
            "A sufficiently long Telegram Saved Message about local-first notes, "
            "safe synchronization, and preserving personal annotations in Obsidian."
        ),
        imported_at="2026-08-04T12:00:00Z",
        archive_fingerprint="live:telegram",
    )
    first = service.import_live_records([record])
    second = service.import_live_records([record])
    assert first.created == 1
    assert second.skipped == 1
    assert fake.calls == 1
    assert service.state.counts()["items"] == 1


def test_enrich_all_regenerates_complete_notes_without_remote_import(
    app_config,
) -> None:
    fake = FakeOllama()
    service = ImportService(app_config, ollama=fake)
    record = CanonicalSave(
        platform="x",
        source_id="reenrich-1",
        source_text=(
            "A sufficiently detailed saved item about multilingual topic labels, "
            "descriptive titles, and preserving manual Obsidian annotations."
        ),
        imported_at="2026-08-04T12:00:00Z",
    )
    service.import_live_records([record])
    item = service.state.all_items()[0]
    content = item.note_path.read_text(encoding="utf-8").replace(
        "## My Notes\n", "## My Notes\n\nKeep this manual note.\n"
    )
    item.note_path.write_text(content, encoding="utf-8")

    class RegeneratedOllama:
        calls = 0

        def enrich(self, record):
            self.calls += 1
            return Enrichment(
                title="Descriptive multilingual topics",
                summary="A regenerated summary based on locally stored source text.",
                topics=["Multilingual Notes", "Topic Classification"],
            )

    regenerated = RegeneratedOllama()
    service.ollama = regenerated
    result = service.enrich_all()

    updated = item.note_path.read_text(encoding="utf-8")
    assert result.updated == 1
    assert regenerated.calls == 1
    assert "topics:" in updated
    assert "**Topics:** Multilingual Notes · Topic Classification" in updated
    assert "Keep this manual note." in updated


def test_enrich_all_recreates_missing_short_content_note(app_config) -> None:
    service = ImportService(app_config, ollama=FakeOllama())
    record = CanonicalSave(
        platform="x",
        source_id="short-missing",
        source_text="short source",
        imported_at="2026-08-04T12:00:00Z",
    )
    service.import_live_records([record])
    item = service.state.all_items()[0]
    item.note_path.unlink()

    result = service.enrich_all()

    assert result.updated == 1
    assert result.pending == 1
    assert item.note_path.exists()
    assert "summary_status: pending_missing_content" in item.note_path.read_text(
        encoding="utf-8"
    )
