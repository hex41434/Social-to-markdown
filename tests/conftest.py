from __future__ import annotations

from pathlib import Path

import pytest

from socials_obsidian.config import AppConfig
from socials_obsidian.models import Enrichment


class FakeOllama:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def enrich(self, record):
        from socials_obsidian.ollama import OllamaError

        self.calls += 1
        if self.fail:
            raise OllamaError("offline")
        return Enrichment(
            title=f"Summary of {record.platform} item",
            summary="This is a factual local summary based only on the exported text. It preserves the source meaning.",
            topics=["Artificial Intelligence", "Knowledge Management", "Local First"],
        )


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return AppConfig(
        project_root=tmp_path,
        vault_path=vault,
        notes_folder="Social Saves",
        data_dir=tmp_path / "data",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen3:4b-instruct",
        min_summary_chars=80,
        excerpt_chars=120,
    )
