from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import CanonicalSave, Enrichment

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS archives (
    fingerprint TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    item_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    stable_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    source_id TEXT NOT NULL,
    note_path TEXT NOT NULL,
    record_json TEXT NOT NULL,
    summary_status TEXT NOT NULL,
    summary_title TEXT,
    summary_text TEXT,
    summary_topics TEXT,
    first_imported_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    archive_fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(summary_status);
"""


@dataclass(slots=True)
class StoredItem:
    record: CanonicalSave
    note_path: Path
    summary_status: str
    enrichment: Enrichment | None
    first_imported_at: str
    last_seen_at: str


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def archive_seen(self, fingerprint: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM archives WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return row is not None

    def mark_archive(
        self, fingerprint: str, label: str, imported_at: str, item_count: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO archives(fingerprint, label, imported_at, item_count) VALUES (?, ?, ?, ?)",
                (fingerprint, label, imported_at, item_count),
            )

    def get_item(self, stable_key: str) -> StoredItem | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM items WHERE stable_key = ?", (stable_key,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def upsert_item(
        self,
        record: CanonicalSave,
        note_path: Path,
        summary_status: str,
        enrichment: Enrichment | None,
        first_imported_at: str,
        last_seen_at: str,
    ) -> None:
        topics = "\n".join(enrichment.cleaned_topics()) if enrichment else None
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO items(
                    stable_key, platform, source_id, note_path, record_json,
                    summary_status, summary_title, summary_text, summary_topics,
                    first_imported_at, last_seen_at, archive_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stable_key) DO UPDATE SET
                    note_path=excluded.note_path,
                    record_json=excluded.record_json,
                    summary_status=excluded.summary_status,
                    summary_title=excluded.summary_title,
                    summary_text=excluded.summary_text,
                    summary_topics=excluded.summary_topics,
                    last_seen_at=excluded.last_seen_at,
                    archive_fingerprint=excluded.archive_fingerprint
                """,
                (
                    record.stable_key,
                    record.platform,
                    record.source_id,
                    str(note_path),
                    record.to_json(),
                    summary_status,
                    enrichment.title if enrichment else None,
                    enrichment.summary if enrichment else None,
                    topics,
                    first_imported_at,
                    last_seen_at,
                    record.archive_fingerprint,
                ),
            )

    def update_enrichment(
        self, stable_key: str, status: str, enrichment: Enrichment | None
    ) -> None:
        topics = "\n".join(enrichment.cleaned_topics()) if enrichment else None
        with self.connect() as connection:
            connection.execute(
                """UPDATE items SET summary_status = ?, summary_title = ?, summary_text = ?, summary_topics = ?
                WHERE stable_key = ?""",
                (
                    status,
                    enrichment.title if enrichment else None,
                    enrichment.summary if enrichment else None,
                    topics,
                    stable_key,
                ),
            )

    def pending_items(self) -> list[StoredItem]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM items WHERE summary_status != 'complete' ORDER BY first_imported_at, stable_key"
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def all_items(self) -> list[StoredItem]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM items ORDER BY platform, first_imported_at, stable_key"
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            items = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            archives = connection.execute("SELECT COUNT(*) FROM archives").fetchone()[0]
            pending = connection.execute(
                "SELECT COUNT(*) FROM items WHERE summary_status != 'complete'"
            ).fetchone()[0]
        return {"items": items, "archives": archives, "pending": pending}

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> StoredItem:
        topics = str(row["summary_topics"] or "").splitlines()
        enrichment = None
        if row["summary_title"] or row["summary_text"]:
            enrichment = Enrichment(
                title=row["summary_title"] or "",
                summary=row["summary_text"] or "",
                topics=topics,
            )
        return StoredItem(
            record=CanonicalSave.from_json(row["record_json"]),
            note_path=Path(row["note_path"]),
            summary_status=row["summary_status"],
            enrichment=enrichment,
            first_imported_at=row["first_imported_at"],
            last_seen_at=row["last_seen_at"],
        )
