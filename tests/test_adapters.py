from __future__ import annotations

import json
from pathlib import Path

from socials_obsidian.adapters import SourceDocument, parse_document

NOW = "2026-08-04T12:00:00Z"


def document(name: str, value: str) -> SourceDocument:
    return SourceDocument(
        name=name,
        content=value.encode(),
        fingerprint="f" * 64,
        label="fixture.zip",
        origin=Path("fixture.zip"),
    )


def test_linkedin_saved_items_csv() -> None:
    record = parse_document(
        document(
            "Saved Items.csv",
            "Date Saved,URL\n2026-07-01,https://www.linkedin.com/posts/example?utm_source=x\n",
        ),
        NOW,
    )[0]
    assert record.platform == "linkedin"
    assert record.saved_at == "2026-07-01T00:00:00"
    assert "utm_source" not in record.source_url


def test_instagram_saved_posts_json() -> None:
    payload = {
        "saved_saved_media": [
            {
                "title": "Design",
                "string_list_data": [
                    {
                        "href": "https://www.instagram.com/reel/ABC123/?igshid=x",
                        "timestamp": 1785600000,
                    }
                ],
            }
        ]
    }
    record = parse_document(
        document("your_instagram_activity/saved/saved_posts.json", json.dumps(payload)),
        NOW,
    )[0]
    assert record.platform == "instagram"
    assert record.source_id == "ABC123"
    assert record.collection == "Design"
    assert "igshid" not in record.source_url


def test_x_javascript_wrapper_with_text() -> None:
    payload = [
        {
            "bookmark": {"tweetId": "123456789"},
            "tweet": {
                "id_str": "123456789",
                "full_text": "A sufficiently long exported post about local-first knowledge systems and personal archives.",
                "user": {"screen_name": "example"},
            },
        }
    ]
    value = "window.YTD.bookmark.part0 = " + json.dumps(payload) + ";"
    record = parse_document(document("data/bookmark.js", value), NOW)[0]
    assert record.platform == "x"
    assert record.source_url == "https://x.com/i/web/status/123456789"
    assert record.author == "example"
    assert "local-first" in record.source_text


def test_telegram_saved_messages_json_handles_fragments_and_forward() -> None:
    payload = {
        "name": "Saved Messages",
        "type": "personal_chat",
        "messages": [
            {
                "id": 42,
                "type": "message",
                "date": "2026-07-30T10:11:12",
                "forwarded_from": "Research Channel",
                "text": [
                    "Read this ",
                    {"type": "link", "text": "https://example.com/article"},
                    " later.",
                ],
                "text_entities": [
                    {"type": "link", "text": "https://example.com/article"}
                ],
            }
        ],
    }
    record = parse_document(document("result.json", json.dumps(payload)), NOW)[0]
    assert record.platform == "telegram"
    assert record.source_id == "42"
    assert record.author == "Research Channel"
    assert record.source_url == "https://example.com/article"
    assert record.source_text == "Read this https://example.com/article later."


def test_instagram_html_unescapes_text() -> None:
    html = '<html><body><div>Saved &amp; useful <a href="https://instagram.com/p/POST1/">Example</a></div></body></html>'
    record = parse_document(document("saved_posts.html", html), NOW)[0]
    assert record.platform == "instagram"
    assert record.source_id == "POST1"
    assert "Saved & useful" in record.source_text


def test_telegram_html_ignores_service_messages() -> None:
    html = """
    <div class="message service" id="message1"><div class="text">Ignored</div></div>
    <div class="message default" id="message2">
      <div class="forwarded"><div class="from_name">Channel</div></div>
      <div class="date details" title="30.07.2026 10:00:00 UTC"></div>
      <div class="text">A useful exported Telegram message with enough text to preserve safely.</div>
    </div>
    """
    records = parse_document(document("messages.html", html), NOW)
    assert [record.source_id for record in records] == ["2"]
    assert records[0].author == "Channel"


def test_malformed_content_is_not_misidentified() -> None:
    assert parse_document(document("result.json", "{not json"), NOW) == []
