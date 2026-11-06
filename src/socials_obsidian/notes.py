from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import CanonicalSave, Enrichment, slugify

MANAGED_TAG_PREFIXES = ("social-save", "social/", "summary/", "topic/")
GENERATED_START = "<!-- socials-obsidian:generated:start -->"
GENERATED_END = "<!-- socials-obsidian:generated:end -->"


def _safe_scalar(value: Any) -> Any:
    return value if value not in (None, "") else None


def parse_note(value: str) -> tuple[dict[str, Any], str]:
    if not value.startswith("---\n"):
        return {}, value
    end = value.find("\n---\n", 4)
    if end < 0:
        return {}, value
    try:
        frontmatter = yaml.safe_load(value[4:end]) or {}
    except yaml.YAMLError:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, value[end + 5 :]


def preserved_my_notes(body: str) -> str:
    marker = "## My Notes"
    position = body.find(marker)
    if position < 0:
        return "## My Notes\n\n"
    return body[position:].rstrip() + "\n\n"


def default_title(record: CanonicalSave) -> str:
    if record.title:
        return record.title.strip()[:120]
    if record.source_text:
        compact = " ".join(record.source_text.split())
        return compact[:100].rstrip(" .,;:")
    if record.author:
        return f"Saved from {record.author}"
    return f"{record.platform.title()} saved item"


def note_filename(record: CanonicalSave) -> str:
    day = (record.saved_at or record.published_at or record.imported_at or "undated")[
        :10
    ]
    title = default_title(record)
    short_id = slugify(record.source_id, fallback="item", max_length=18)
    return f"{day}-{slugify(title)}-{short_id}.md"


def managed_tags(
    record: CanonicalSave, status: str, enrichment: Enrichment | None
) -> list[str]:
    tags = [
        "social-save",
        f"social/{record.platform}",
        f"summary/{status.replace('_', '-')}",
    ]
    if enrichment:
        tags.extend(f"topic/{topic}" for topic in enrichment.normalized_topics())
    return list(dict.fromkeys(tags))


def render_note(
    record: CanonicalSave,
    status: str,
    enrichment: Enrichment | None,
    excerpt_chars: int,
    existing: str | None = None,
    first_imported_at: str | None = None,
    last_seen_at: str | None = None,
) -> str:
    old_frontmatter, old_body = parse_note(existing or "")
    old_tags = old_frontmatter.get("tags", [])
    if isinstance(old_tags, str):
        old_tags = [old_tags]
    user_tags = [
        str(tag)
        for tag in old_tags
        if not any(
            str(tag) == prefix or str(tag).startswith(prefix)
            for prefix in MANAGED_TAG_PREFIXES
        )
    ]
    title = enrichment.title if enrichment else default_title(record)
    frontmatter = dict(old_frontmatter)
    frontmatter.update(
        {
            "type": "social-save",
            "platform": record.platform,
            "source_id": record.source_id,
            "source_url": _safe_scalar(record.source_url),
            "saved_at": _safe_scalar(record.saved_at),
            "published_at": _safe_scalar(record.published_at),
            "author": _safe_scalar(record.author),
            "collection": _safe_scalar(record.collection),
            "topics": enrichment.cleaned_topics() if enrichment else None,
            "summary_status": status,
            "imported_at": first_imported_at or record.imported_at,
            "last_seen_at": last_seen_at or record.imported_at,
            "tags": list(
                dict.fromkeys(managed_tags(record, status, enrichment) + user_tags)
            ),
        }
    )
    frontmatter = {
        key: value for key, value in frontmatter.items() if value is not None
    }
    lines = [GENERATED_START, f"# {title}", ""]
    if enrichment:
        lines.extend(
            ["> [!summary]", "> " + enrichment.summary.replace("\n", "\n> "), ""]
        )
        lines.extend([f"**Topics:** {' · '.join(enrichment.cleaned_topics())}", ""])
    elif status == "pending_missing_content":
        lines.extend(
            [
                "> [!info] Summary pending",
                "> The official export did not include enough text to summarize safely.",
                "",
            ]
        )
    elif status == "failed":
        lines.extend(
            [
                "> [!warning] Summary unavailable",
                "> Local Ollama enrichment failed. Run `socials enrich --pending` to retry.",
                "",
            ]
        )
    if record.source_url:
        lines.extend([f"[Open original]({record.source_url})", ""])
    if record.source_text:
        excerpt = record.source_text[:excerpt_chars].rstrip()
        if len(record.source_text) > excerpt_chars:
            excerpt += "…"
        lines.extend(["## Exported Excerpt", "", excerpt, ""])
    lines.append(GENERATED_END)
    generated = "\n".join(lines).rstrip() + "\n\n"
    notes = preserved_my_notes(old_body)
    yaml_text = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{generated}{notes}"


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp:
            temp.write(value)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_index(items: list, notes_root: Path) -> str:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    counts: dict[str, int] = {}
    pending = 0
    for item in items:
        counts[item.record.platform] = counts.get(item.record.platform, 0) + 1
        if item.summary_status != "complete":
            pending += 1
    lines = [
        "---",
        "type: social-saves-index",
        f"updated_at: {now}",
        "tags:",
        "- social-save",
        "---",
        "",
        "# Social Saves",
        "",
        f"**Total:** {len(items)} · **Pending summaries:** {pending}",
        "",
        "## Platforms",
        "",
    ]
    for platform in sorted(counts):
        lines.append(f"- **{platform.title()}**: {counts[platform]}")
    lines.extend(
        [
            "",
            "## Browse all items",
            "",
            "```query",
            f'path:"{notes_root.name}/Items" tag:#social-save',
            "```",
            "",
            "## Pending summaries",
            "",
            "```query",
            f'path:"{notes_root.name}/Items" tag:#summary/pending-missing-content',
            "```",
            "",
        ]
    )
    return "\n".join(lines)
