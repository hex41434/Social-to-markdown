from __future__ import annotations

import pytest

from socials_obsidian.models import CanonicalSave
from socials_obsidian.ollama import (
    OllamaClient,
    OllamaError,
    enrichment_matches_source_language,
)


def record() -> CanonicalSave:
    return CanonicalSave(
        platform="telegram",
        source_id="1",
        source_text="A long exported message explaining a local-first system for organizing knowledge safely in plain Markdown files.",
    )


def test_structured_ollama_response(monkeypatch) -> None:
    client = OllamaClient("http://127.0.0.1:11434", "model")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "message": {
                "content": '{"title":"Local knowledge","summary":"A local-first knowledge workflow.","topics":["Local First","Obsidian"]}'
            }
        },
    )
    result = client.enrich(record())
    assert result.title == "Local knowledge"
    assert result.normalized_topics() == ["local-first", "obsidian"]


def test_persian_topics_remain_persian() -> None:
    from socials_obsidian.models import Enrichment

    enrichment = Enrichment(
        title="مدیریت دانش شخصی",
        summary="خلاصه‌ای فارسی.",
        topics=["هوش مصنوعی", "مدیریت دانش", "هوش مصنوعی"],
    )
    assert enrichment.cleaned_topics() == ["هوش مصنوعی", "مدیریت دانش"]
    assert enrichment.normalized_topics() == ["هوش-مصنوعی", "مدیریت-دانش"]


def test_persian_source_rejects_english_title_or_summary() -> None:
    from socials_obsidian.models import Enrichment

    source = (
        "این متن فارسی درباره مدیریت دانش شخصی و دسته‌بندی یادداشت‌ها در آبسیدین است."
    )
    english = Enrichment(
        title="Personal knowledge management",
        summary="The item discusses organizing notes in Obsidian.",
        topics=["Knowledge Management"],
    )
    persian = Enrichment(
        title="مدیریت دانش شخصی در آبسیدین",
        summary="این مطلب درباره سازمان‌دهی یادداشت‌ها و مدیریت دانش شخصی در آبسیدین است.",
        topics=["مدیریت دانش", "Obsidian"],
    )
    assert not enrichment_matches_source_language(source, english)
    assert enrichment_matches_source_language(source, persian)


def test_invalid_response_retries_once(monkeypatch) -> None:
    client = OllamaClient("http://127.0.0.1:11434", "model")
    calls = []

    def invalid(*args, **kwargs):
        calls.append(1)
        return {"message": {"content": "not json"}}

    monkeypatch.setattr(client, "_request", invalid)
    with pytest.raises(OllamaError):
        client.enrich(record())
    assert len(calls) == 2


def test_unavailable_server_is_not_ready(monkeypatch) -> None:
    client = OllamaClient("http://127.0.0.1:11434", "model")
    monkeypatch.setattr(
        client,
        "available_models",
        lambda: (_ for _ in ()).throw(OllamaError("offline")),
    )
    assert client.is_ready() is False
