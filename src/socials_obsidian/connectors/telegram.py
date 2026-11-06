from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from ..models import CanonicalSave


class TelegramConnectorError(RuntimeError):
    pass


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _forward_author(message: Any) -> str | None:
    forward = getattr(message, "forward", None)
    if not forward:
        return None
    for name in ("from_name", "post_author"):
        value = getattr(forward, name, None)
        if value:
            return str(value)
    sender_id = getattr(forward, "sender_id", None)
    return f"telegram:{sender_id}" if sender_id else None


def message_to_record(message: Any, imported_at: str) -> CanonicalSave | None:
    message_id = getattr(message, "id", None)
    if message_id is None:
        return None
    text = (getattr(message, "raw_text", None) or "").strip() or None
    forward = getattr(message, "forward", None)
    return CanonicalSave(
        platform="telegram",
        source_id=str(message_id),
        author=_forward_author(message),
        saved_at=_iso(getattr(message, "date", None)),
        published_at=_iso(getattr(forward, "date", None)) if forward else None,
        collection="Saved Messages",
        source_text=text,
        archive_fingerprint="live:telegram",
        imported_at=imported_at,
        archive_label="Telegram API: Saved Messages",
    )


class TelegramConnector:
    """Read Saved Messages through Telegram's user API; never downloads media."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: Path,
        client_factory=TelegramClient,
    ):
        if api_id <= 0 or not api_hash.strip():
            raise TelegramConnectorError("Telegram api_id and api_hash are required")
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.client_factory = client_factory

    def _client(self):
        self.session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.client_factory(str(self.session_path), self.api_id, self.api_hash)

    async def connect(self, phone: str) -> str:
        client = self._client()
        try:
            await client.start(phone=phone)
            me = await client.get_me()
            username = getattr(me, "username", None)
            identity = f"@{username}" if username else str(getattr(me, "id", "user"))
            session_file = self.session_path.with_suffix(".session")
            if session_file.exists():
                session_file.chmod(0o600)
            return identity
        except Exception as exc:
            raise TelegramConnectorError(
                f"Telegram authorization failed: {exc}"
            ) from exc
        finally:
            await client.disconnect()

    async def fetch(
        self, imported_at: str, limit: int | None = None
    ) -> list[CanonicalSave]:
        client = self._client()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramConnectorError(
                    "Telegram is not connected; run `socials connect telegram` first"
                )
            records: list[CanonicalSave] = []
            async for message in client.iter_messages("me", limit=limit):
                record = message_to_record(message, imported_at)
                if record:
                    records.append(record)
            return records
        except TelegramConnectorError:
            raise
        except Exception as exc:
            raise TelegramConnectorError(f"Telegram sync failed: {exc}") from exc
        finally:
            await client.disconnect()
