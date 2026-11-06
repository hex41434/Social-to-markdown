from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .models import CanonicalSave, normalize_url

MAX_RELEVANT_MEMBER_BYTES = 64 * 1024 * 1024
MAX_RELEVANT_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
DIRECT_SUFFIXES = {".csv", ".json", ".js", ".html", ".htm"}
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
X_ID_PATTERN = re.compile(r"/status/(\d+)")
INSTAGRAM_ID_PATTERN = re.compile(r"/(?:p|reel|tv)/([^/?#]+)")


class AdapterError(ValueError):
    pass


class UnsafeArchiveError(AdapterError):
    pass


@dataclass(frozen=True, slots=True)
class SourceDocument:
    name: str
    content: bytes
    fingerprint: str
    label: str
    origin: Path


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relevant_archive_member(name: str) -> bool:
    base = PurePosixPath(name).name.lower().replace("_", " ")
    normalized = name.lower().replace("\\", "/")
    return base in {
        "saved items.csv",
        "bookmark.js",
        "bookmarks.js",
        "bookmark.json",
        "bookmarks.json",
        "result.json",
        "messages.html",
        "messages.htm",
    } or normalized.endswith(
        ("/saved_posts.json", "/saved_posts.html", "/saved_posts.htm")
    )


def _validate_member_path(name: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeArchiveError(f"Unsafe ZIP member path: {name}")


def iter_source_documents(paths: Iterable[Path]) -> Iterator[SourceDocument]:
    seen: set[Path] = set()
    for supplied in paths:
        supplied = supplied.expanduser().resolve()
        candidates = [supplied]
        if supplied.is_dir():
            candidates = sorted(path for path in supplied.rglob("*") if path.is_file())
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            if path.suffix.lower() == ".zip":
                fingerprint = _hash_file(path)
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > MAX_ARCHIVE_MEMBERS:
                        raise UnsafeArchiveError(
                            f"Archive has too many members: {path.name}"
                        )
                    relevant_total = 0
                    for member in members:
                        _validate_member_path(member.filename)
                        if member.is_dir() or not _is_relevant_archive_member(
                            member.filename
                        ):
                            continue
                        if member.file_size > MAX_RELEVANT_MEMBER_BYTES:
                            raise UnsafeArchiveError(
                                f"Relevant ZIP member is too large: {member.filename}"
                            )
                        relevant_total += member.file_size
                        if relevant_total > MAX_RELEVANT_TOTAL_BYTES:
                            raise UnsafeArchiveError(
                                f"Relevant ZIP content is too large: {path.name}"
                            )
                        yield SourceDocument(
                            name=member.filename,
                            content=archive.read(member),
                            fingerprint=fingerprint,
                            label=path.name,
                            origin=path,
                        )
            elif path.suffix.lower() in DIRECT_SUFFIXES:
                content = path.read_bytes()
                if len(content) > MAX_RELEVANT_MEMBER_BYTES:
                    raise AdapterError(f"Input file is too large: {path}")
                yield SourceDocument(
                    name=path.name,
                    content=content,
                    fingerprint=sha256(content).hexdigest(),
                    label=path.name,
                    origin=path,
                )


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    return content.decode("utf-8", errors="replace")


def _iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        try:
            return (
                datetime.fromtimestamp(number, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = str(value).strip()
    text = text.replace(" UTC", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return parsed.isoformat()
    except ValueError:
        pass
    for fmt in ("%d %b %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date().isoformat()
        except ValueError:
            continue
    return text


def _make_save(
    document: SourceDocument, imported_at: str, **values: Any
) -> CanonicalSave:
    return CanonicalSave(
        archive_fingerprint=document.fingerprint,
        archive_label=document.label,
        imported_at=imported_at,
        **values,
    )


def parse_linkedin(document: SourceDocument, imported_at: str) -> list[CanonicalSave]:
    text = _decode(document.content)
    rows = csv.DictReader(io.StringIO(text))
    if not rows.fieldnames:
        return []
    names = {name.lower().strip().replace("_", " "): name for name in rows.fieldnames}
    url_key = next(
        (
            names[key]
            for key in names
            if key in {"url", "link", "saved item url", "content url"}
        ),
        None,
    )
    date_key = next(
        (
            names[key]
            for key in names
            if key in {"date saved", "saved date", "saved at"}
        ),
        None,
    )
    if not url_key:
        return []
    result: list[CanonicalSave] = []
    for row in rows:
        url = normalize_url(row.get(url_key))
        if not url or "linkedin.com" not in urlsplit(url).netloc:
            continue
        identifier = sha256(url.encode()).hexdigest()[:20]
        result.append(
            _make_save(
                document,
                imported_at,
                platform="linkedin",
                source_id=f"url-{identifier}",
                source_url=url,
                saved_at=_iso_date(row.get(date_key)) if date_key else None,
                title=(row.get(names.get("title", "")) or None),
                author=(row.get(names.get("author", "")) or None),
            )
        )
    return result


def _instagram_entries(
    node: Any, inherited_collection: str | None = None
) -> Iterator[tuple[dict, str | None]]:
    if isinstance(node, dict):
        collection = inherited_collection
        title = node.get("title")
        if (
            isinstance(title, str)
            and title.strip()
            and title.lower() not in {"saved posts", "saved media"}
        ):
            collection = title.strip()
        data = node.get("string_list_data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("href"):
                    yield item, collection
        for value in node.values():
            yield from _instagram_entries(value, collection)
    elif isinstance(node, list):
        for item in node:
            yield from _instagram_entries(item, inherited_collection)


def parse_instagram_json(
    document: SourceDocument, imported_at: str
) -> list[CanonicalSave]:
    try:
        payload = json.loads(_decode(document.content))
    except json.JSONDecodeError:
        return []
    result: list[CanonicalSave] = []
    for item, collection in _instagram_entries(payload):
        url = normalize_url(str(item.get("href", "")))
        if not url or "instagram.com" not in urlsplit(url).netloc:
            continue
        match = INSTAGRAM_ID_PATTERN.search(urlsplit(url).path)
        source_id = (
            match.group(1) if match else f"url-{sha256(url.encode()).hexdigest()[:20]}"
        )
        result.append(
            _make_save(
                document,
                imported_at,
                platform="instagram",
                source_id=source_id,
                source_url=url,
                saved_at=_iso_date(item.get("timestamp")),
                author=collection,
                collection=collection,
                source_text=item.get("value") or None,
            )
        )
    return result


def parse_instagram_html(
    document: SourceDocument, imported_at: str
) -> list[CanonicalSave]:
    soup = BeautifulSoup(_decode(document.content), "html.parser")
    result: list[CanonicalSave] = []
    for anchor in soup.find_all("a", href=True):
        url = normalize_url(anchor.get("href"))
        if not url or "instagram.com" not in urlsplit(url).netloc:
            continue
        match = INSTAGRAM_ID_PATTERN.search(urlsplit(url).path)
        source_id = (
            match.group(1) if match else f"url-{sha256(url.encode()).hexdigest()[:20]}"
        )
        text = " ".join(anchor.get_text(" ", strip=True).split()) or None
        container_text = (
            " ".join(anchor.parent.get_text(" ", strip=True).split())
            if anchor.parent
            else ""
        )
        result.append(
            _make_save(
                document,
                imported_at,
                platform="instagram",
                source_id=source_id,
                source_url=url,
                title=text,
                source_text=container_text
                if container_text and container_text != text
                else None,
            )
        )
    return result


def _strip_javascript_assignment(text: str) -> str:
    stripped = text.strip().lstrip("\ufeff")
    if stripped.startswith(("[", "{")):
        return stripped.rstrip(";\n ")
    match = re.search(r"=\s*([\[{].*)", stripped, re.DOTALL)
    if not match:
        return stripped
    return match.group(1).rstrip(";\n ")


def _walk_dicts(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def parse_x(document: SourceDocument, imported_at: str) -> list[CanonicalSave]:
    try:
        payload = json.loads(_strip_javascript_assignment(_decode(document.content)))
    except json.JSONDecodeError:
        return []
    result: dict[str, CanonicalSave] = {}
    for item in _walk_dicts(payload):
        tweet = item.get("tweet") if isinstance(item.get("tweet"), dict) else item
        bookmark = (
            item.get("bookmark") if isinstance(item.get("bookmark"), dict) else item
        )
        source_id = (
            bookmark.get("tweetId")
            or bookmark.get("tweet_id")
            or tweet.get("id_str")
            or tweet.get("id")
        )
        url = bookmark.get("url") or tweet.get("url")
        if not source_id and url:
            match = X_ID_PATTERN.search(str(url))
            source_id = match.group(1) if match else None
        if not source_id or not str(source_id).isdigit():
            continue
        source_id = str(source_id)
        text = tweet.get("full_text") or tweet.get("text")
        author = tweet.get("username") or tweet.get("screen_name")
        user = tweet.get("user")
        if isinstance(user, dict):
            author = author or user.get("screen_name") or user.get("name")
        result[source_id] = _make_save(
            document,
            imported_at,
            platform="x",
            source_id=source_id,
            source_url=str(url) if url else f"https://x.com/i/web/status/{source_id}",
            author=author,
            source_text=text,
            published_at=_iso_date(tweet.get("created_at")),
            saved_at=_iso_date(bookmark.get("saved_at") or bookmark.get("created_at")),
        )
    return list(result.values())


def _telegram_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return ""


def _telegram_urls(message: dict, text: str) -> list[str]:
    result: list[str] = []
    for entity in message.get("text_entities", []) or []:
        if not isinstance(entity, dict):
            continue
        candidate = (
            entity.get("href")
            if entity.get("type") == "text_link"
            else entity.get("text")
        )
        if candidate and str(candidate).startswith(("http://", "https://")):
            result.append(str(candidate))
    result.extend(URL_PATTERN.findall(text))
    return list(dict.fromkeys(result))


def _telegram_chats(payload: Any) -> Iterator[dict]:
    if not isinstance(payload, dict):
        return
    if isinstance(payload.get("messages"), list):
        yield payload
    chats = payload.get("chats")
    if isinstance(chats, dict):
        for chat in chats.get("list", []) or []:
            if isinstance(chat, dict):
                yield chat


def parse_telegram_json(
    document: SourceDocument, imported_at: str
) -> list[CanonicalSave]:
    try:
        payload = json.loads(_decode(document.content))
    except json.JSONDecodeError:
        return []
    result: list[CanonicalSave] = []
    for chat in _telegram_chats(payload):
        name = str(chat.get("name", ""))
        chat_type = str(chat.get("type", ""))
        if (
            name
            and name.lower() not in {"saved messages", "saved messages chat"}
            and chat_type != "saved_messages"
        ):
            continue
        for message in chat.get("messages", []) or []:
            if not isinstance(message, dict) or message.get("type") not in {
                None,
                "message",
            }:
                continue
            source_id = message.get("id")
            if source_id is None:
                continue
            text = _telegram_text(message.get("text"))
            urls = _telegram_urls(message, text)
            author = (
                message.get("forwarded_from")
                or message.get("from")
                or message.get("actor")
            )
            result.append(
                _make_save(
                    document,
                    imported_at,
                    platform="telegram",
                    source_id=str(source_id),
                    source_url=urls[0] if urls else None,
                    author=str(author) if author else None,
                    source_text=text or None,
                    saved_at=_iso_date(
                        message.get("date_unixtime") or message.get("date")
                    ),
                    published_at=_iso_date(message.get("forwarded_from_when")),
                )
            )
    return result


def parse_telegram_html(
    document: SourceDocument, imported_at: str
) -> list[CanonicalSave]:
    soup = BeautifulSoup(_decode(document.content), "html.parser")
    result: list[CanonicalSave] = []
    for node in soup.select("div.message"):
        classes = set(node.get("class", []))
        if "service" in classes:
            continue
        raw_id = node.get("id", "")
        match = re.search(r"(\d+)", raw_id)
        if not match:
            continue
        text_node = node.select_one(".text")
        text = (
            " ".join(text_node.get_text(" ", strip=True).split()) if text_node else ""
        )
        urls = [
            anchor.get("href")
            for anchor in node.find_all("a", href=True)
            if str(anchor.get("href")).startswith(("http://", "https://"))
        ]
        author_node = node.select_one(".forwarded .from_name, .from_name")
        date_node = node.select_one(".date.details")
        date_value = date_node.get("title") if date_node else None
        result.append(
            _make_save(
                document,
                imported_at,
                platform="telegram",
                source_id=match.group(1),
                source_url=urls[0] if urls else None,
                author=" ".join(author_node.get_text(" ", strip=True).split())
                if author_node
                else None,
                source_text=unescape(text) or None,
                saved_at=_iso_date(date_value),
            )
        )
    return result


def parse_document(document: SourceDocument, imported_at: str) -> list[CanonicalSave]:
    name = document.name.lower().replace("\\", "/")
    base = PurePosixPath(name).name.replace("_", " ")
    parsers = []
    if base == "saved items.csv" or name.endswith("saved items.csv"):
        parsers = [parse_linkedin]
    elif name.endswith(("saved_posts.json", "saved posts.json")):
        parsers = [parse_instagram_json]
    elif name.endswith(
        ("saved_posts.html", "saved_posts.htm", "saved posts.html", "saved posts.htm")
    ):
        parsers = [parse_instagram_html]
    elif base in {"bookmark.js", "bookmarks.js", "bookmark.json", "bookmarks.json"}:
        parsers = [parse_x]
    elif base == "result.json":
        parsers = [parse_telegram_json, parse_instagram_json, parse_x]
    elif base in {"messages.html", "messages.htm"}:
        parsers = [parse_telegram_html]
    elif name.endswith(".csv"):
        parsers = [parse_linkedin]
    elif name.endswith((".json", ".js")):
        parsers = [parse_instagram_json, parse_x, parse_telegram_json]
    elif name.endswith((".html", ".htm")):
        parsers = [parse_instagram_html, parse_telegram_html]
    for parser in parsers:
        records = parser(document, imported_at)
        if records:
            return records
    return []
