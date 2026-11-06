from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PLATFORMS = {"linkedin", "x", "instagram", "telegram"}
TRACKING_QUERY_KEYS = {"igshid", "si", "s", "ref_src", "ref_url"}


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return value
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def slugify(value: str, fallback: str = "saved-item", max_length: int = 72) -> str:
    value = value.casefold().strip().replace("_", "-")
    value = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-")
    return value[:max_length].rstrip("-") or fallback


@dataclass(slots=True)
class CanonicalSave:
    platform: str
    source_id: str
    source_url: str | None = None
    author: str | None = None
    title: str | None = None
    saved_at: str | None = None
    published_at: str | None = None
    collection: str | None = None
    source_text: str | None = None
    archive_fingerprint: str = ""
    imported_at: str = ""
    archive_label: str = ""

    def __post_init__(self) -> None:
        self.platform = self.platform.lower()
        if self.platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {self.platform}")
        self.source_url = normalize_url(self.source_url)
        if not self.source_id:
            if not self.source_url:
                raise ValueError("source_id or source_url is required")
            self.source_id = "url-" + sha256(self.source_url.encode()).hexdigest()[:20]
        self.source_id = str(self.source_id)
        if self.source_text:
            self.source_text = re.sub(r"\s+", " ", self.source_text).strip()

    @property
    def stable_key(self) -> str:
        return f"{self.platform}:{self.source_id}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> CanonicalSave:
        return cls(**json.loads(value))


@dataclass(slots=True)
class Enrichment:
    title: str
    summary: str
    topics: list[str] = field(default_factory=list)

    def cleaned_topics(self) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for topic in self.topics:
            cleaned = re.sub(r"\s+", " ", str(topic)).strip(" #,.;:")
            identity = cleaned.casefold()
            if cleaned and identity not in seen:
                seen.add(identity)
                result.append(cleaned)
        return result[:5]

    def normalized_topics(self) -> list[str]:
        result: list[str] = []
        for topic in self.cleaned_topics():
            slug = slugify(topic, fallback="")
            if slug and slug not in result:
                result.append(slug)
        return result[:5]


@dataclass(slots=True)
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    pending: int = 0
    failed: int = 0
    archives: int = 0
    messages: list[str] = field(default_factory=list)

    def merge(self, other: ImportResult) -> None:
        for name in ("created", "updated", "skipped", "pending", "failed", "archives"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.messages.extend(other.messages)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
