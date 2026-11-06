from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .models import CanonicalSave, Enrichment


class OllamaError(RuntimeError):
    pass


PERSIAN_LETTER = re.compile(r"[\u0600-\u06ff]")
UNICODE_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def is_persian_dominant(value: str) -> bool:
    letters = UNICODE_LETTER.findall(value or "")
    if not letters:
        return False
    persian_count = sum(bool(PERSIAN_LETTER.match(letter)) for letter in letters)
    return persian_count / len(letters) >= 0.5


def enrichment_matches_source_language(
    source_text: str, enrichment: Enrichment | None
) -> bool:
    if not is_persian_dominant(source_text):
        return True
    return bool(
        enrichment
        and is_persian_dominant(enrichment.title)
        and is_persian_dominant(enrichment.summary)
    )


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        host = urlsplit(self.base_url).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Ollama must use a loopback address")

    def available_models(self) -> list[str]:
        payload = self._request("GET", "/api/tags")
        return [
            str(item.get("name"))
            for item in payload.get("models", [])
            if item.get("name")
        ]

    def is_ready(self) -> bool:
        try:
            models = self.available_models()
        except OllamaError:
            return False
        wanted = self.model.split(":", 1)[0]
        return any(
            name == self.model or name.split(":", 1)[0] == wanted for name in models
        )

    def enrich(self, record: CanonicalSave) -> Enrichment:
        source_text = (record.source_text or "").strip()
        if not source_text:
            raise OllamaError("No source text available")
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                },
            },
            "required": ["title", "summary", "topics"],
        }
        language_instruction = (
            "زبان غالب متن فارسی است. عنوان و خلاصه را حتماً با نثر طبیعی فارسی بنویس؛ "
            "ترجمهٔ انگلیسی یا عنوان انگلیسی پذیرفته نیست. موضوع‌ها نیز فارسی باشند، "
            "مگر نام فنی شناخته‌شده‌ای مانند React یا API. "
            if is_persian_dominant(source_text)
            else "Keep the title, summary, and topics in the source's dominant language. "
        )
        prompt = (
            "Analyze only the supplied exported text. Do not infer facts from the URL, author, or outside knowledge. "
            "Identify the main language of the source. Write the title, summary, and topics in that same language; "
            "especially preserve Persian text in Persian and never translate it into English. "
            f"{language_instruction}"
            "The title must be a specific 4-12 word description of what the item is about, not a copied opening line, "
            "generic label, or clickbait. Write a factual 2-3 sentence summary and 3-5 specific topical noun phrases. "
            "Topics must describe the actual subject; avoid generic labels such as bookmark, post, thread, social media, or X. "
            "Return only JSON matching the schema.\n\n"
            f"Platform: {record.platform}\nAuthor: {record.author or 'unknown'}\nExported text:\n{source_text}"
        )
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt}],
        }
        last_error: Exception | None = None
        for _ in range(2):
            try:
                payload = self._request("POST", "/api/chat", body)
                content = payload.get("message", {}).get("content", "")
                parsed = json.loads(self._extract_json(content))
                title = str(parsed.get("title", "")).strip()
                summary = str(parsed.get("summary", "")).strip()
                topics = parsed.get("topics", [])
                if not title or not summary or not isinstance(topics, list):
                    raise ValueError("Incomplete structured response")
                enrichment = Enrichment(
                    title=title, summary=summary, topics=[str(item) for item in topics]
                )
                if not enrichment_matches_source_language(source_text, enrichment):
                    raise ValueError(
                        "Title or summary language does not match the Persian source"
                    )
                return enrichment
            except (ValueError, json.JSONDecodeError, OllamaError) as exc:
                last_error = exc
        raise OllamaError(f"Invalid Ollama response: {last_error}")

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OllamaError(str(exc)) from exc

    @staticmethod
    def _extract_json(value: str) -> str:
        value = value.strip()
        if value.startswith("{"):
            return value
        match = re.search(r"\{.*\}", value, re.DOTALL)
        if not match:
            raise ValueError("No JSON object in response")
        return match.group(0)
