from __future__ import annotations

import shutil
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .adapters import (
    AdapterError,
    SourceDocument,
    iter_source_documents,
    parse_document,
)
from .config import AppConfig
from .models import CanonicalSave, Enrichment, ImportResult
from .notes import atomic_write, build_index, note_filename, render_note
from .ollama import OllamaClient, OllamaError, enrichment_matches_source_language
from .state import StateStore


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ImportService:
    def __init__(self, config: AppConfig, ollama: OllamaClient | None = None):
        self.config = config
        self.config.ensure_local_dirs()
        self.state = StateStore(config.state_path)
        self.ollama = ollama or OllamaClient(
            config.ollama_base_url, config.ollama_model
        )

    def import_paths(self, paths: list[Path], dry_run: bool = False) -> ImportResult:
        result = ImportResult()
        imported_at = utc_now()
        if not paths:
            paths = [self.config.inbox_dir]
        try:
            documents = list(iter_source_documents(paths))
        except (OSError, AdapterError) as exc:
            result.failed += 1
            result.messages.append(str(exc))
            return result
        grouped: dict[str, list[SourceDocument]] = defaultdict(list)
        for document in documents:
            grouped[document.fingerprint].append(document)
        if not grouped:
            result.messages.append("No supported export files found.")
            return result
        for fingerprint, bundle in grouped.items():
            if self.state.archive_seen(fingerprint):
                result.skipped += 1
                result.messages.append(f"Already imported: {bundle[0].label}")
                continue
            records: dict[str, CanonicalSave] = {}
            for document in bundle:
                try:
                    parsed = parse_document(document, imported_at)
                except (AdapterError, ValueError) as exc:
                    result.failed += 1
                    result.messages.append(f"{document.name}: {exc}")
                    parsed = []
                for record in parsed:
                    records[record.stable_key] = record
            if not records:
                result.failed += 1
                result.messages.append(
                    f"No saved items recognized in {bundle[0].label}"
                )
                continue
            bundle_result = self._import_records(list(records.values()), dry_run)
            result.merge(bundle_result)
            result.archives += 1
            if not dry_run:
                self.state.mark_archive(
                    fingerprint, bundle[0].label, imported_at, len(records)
                )
                self._move_processed(bundle[0].origin, fingerprint)
        if not dry_run:
            self.rebuild_index()
        return result

    def _import_records(
        self,
        records: list[CanonicalSave],
        dry_run: bool,
        progress: Callable[[int, int], None] | None = None,
    ) -> ImportResult:
        result = ImportResult()
        total = len(records)
        for position, record in enumerate(records, start=1):
            if progress:
                progress(position, total)
            stored = self.state.get_item(record.stable_key)
            first_imported_at = (
                stored.first_imported_at if stored else record.imported_at
            )
            note_path = stored.note_path if stored else self._new_note_path(record)
            if (
                stored
                and stored.summary_status == "complete"
                and stored.record.source_text == record.source_text
            ):
                status, enrichment = "complete", stored.enrichment
            else:
                status, enrichment = self._enrich_record(record)
            if status != "complete":
                result.pending += 1
            existing = (
                note_path.read_text(encoding="utf-8") if note_path.exists() else None
            )
            rendered = render_note(
                record,
                status,
                enrichment,
                self.config.excerpt_chars,
                existing=existing,
                first_imported_at=first_imported_at,
                last_seen_at=record.imported_at,
            )
            if existing == rendered:
                result.skipped += 1
            elif stored or note_path.exists():
                result.updated += 1
            else:
                result.created += 1
            if dry_run:
                continue
            atomic_write(note_path, rendered)
            self.state.upsert_item(
                record,
                note_path,
                status,
                enrichment,
                first_imported_at=first_imported_at,
                last_seen_at=record.imported_at,
            )
        return result

    def import_live_records(
        self,
        records: list[CanonicalSave],
        dry_run: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> ImportResult:
        """Import official live API records without inferring remote deletions."""
        unique = {record.stable_key: record for record in records}
        result = self._import_records(list(unique.values()), dry_run, progress)
        if not dry_run:
            self.rebuild_index()
        if not unique:
            result.messages.append("The connected account returned no saved items.")
        return result

    def _enrich_record(self, record: CanonicalSave) -> tuple[str, Enrichment | None]:
        if len((record.source_text or "").strip()) < self.config.min_summary_chars:
            return "pending_missing_content", None
        try:
            return "complete", self.ollama.enrich(record)
        except OllamaError:
            return "failed", None

    def enrich_pending(
        self, progress: Callable[[int, int, str], None] | None = None
    ) -> ImportResult:
        return self._reenrich_items(self.state.pending_items(), progress)

    def enrich_all(
        self, progress: Callable[[int, int, str], None] | None = None
    ) -> ImportResult:
        """Regenerate all summaries from stored source text without remote access."""
        return self._reenrich_items(self.state.all_items(), progress)

    def enrich_language_mismatches(
        self, progress: Callable[[int, int, str], None] | None = None
    ) -> ImportResult:
        """Repair stored Persian items whose title or summary changed language."""
        mismatches = [
            stored
            for stored in self.state.all_items()
            if not enrichment_matches_source_language(
                stored.record.source_text or "", stored.enrichment
            )
        ]
        return self._reenrich_items(mismatches, progress)

    def _reenrich_items(
        self,
        items: list,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> ImportResult:
        result = ImportResult()
        total = len(items)
        for position, stored in enumerate(items, start=1):
            record = stored.record
            if progress:
                progress(position, total, record.stable_key)
            if len((record.source_text or "").strip()) < self.config.min_summary_chars:
                existing = (
                    stored.note_path.read_text(encoding="utf-8")
                    if stored.note_path.exists()
                    else None
                )
                rendered = render_note(
                    record,
                    "pending_missing_content",
                    None,
                    self.config.excerpt_chars,
                    existing=existing,
                    first_imported_at=stored.first_imported_at,
                    last_seen_at=stored.last_seen_at,
                )
                if existing == rendered:
                    result.skipped += 1
                else:
                    atomic_write(stored.note_path, rendered)
                    result.updated += 1
                self.state.update_enrichment(
                    record.stable_key, "pending_missing_content", None
                )
                result.pending += 1
                continue
            try:
                enrichment = self.ollama.enrich(record)
            except OllamaError as exc:
                result.failed += 1
                result.messages.append(f"{record.stable_key}: {exc}")
                continue
            existing = (
                stored.note_path.read_text(encoding="utf-8")
                if stored.note_path.exists()
                else None
            )
            rendered = render_note(
                record,
                "complete",
                enrichment,
                self.config.excerpt_chars,
                existing=existing,
                first_imported_at=stored.first_imported_at,
                last_seen_at=stored.last_seen_at,
            )
            if existing == rendered:
                result.skipped += 1
            else:
                atomic_write(stored.note_path, rendered)
                result.updated += 1
            self.state.update_enrichment(record.stable_key, "complete", enrichment)
        self.rebuild_index()
        return result

    def rebuild_index(self) -> Path:
        index_path = self.config.notes_root / "Social Saves.md"
        atomic_write(
            index_path, build_index(self.state.all_items(), self.config.notes_root)
        )
        return index_path

    def _new_note_path(self, record: CanonicalSave) -> Path:
        folder = self.config.notes_root / "Items" / record.platform
        candidate = folder / note_filename(record)
        if not candidate.exists():
            return candidate
        return folder / f"{candidate.stem}-{record.source_id[-8:]}.md"

    def _move_processed(self, origin: Path, fingerprint: str) -> None:
        try:
            origin.resolve().relative_to(self.config.inbox_dir.resolve())
        except ValueError:
            return
        if not origin.exists() or not origin.is_file():
            return
        destination = self.config.processed_dir / origin.name
        if destination.exists():
            destination = (
                self.config.processed_dir
                / f"{origin.stem}-{fingerprint[:10]}{origin.suffix}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origin), str(destination))
