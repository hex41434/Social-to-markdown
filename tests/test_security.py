from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from socials_obsidian.adapters import UnsafeArchiveError, iter_source_documents
from socials_obsidian.config import AppConfig
from socials_obsidian.ollama import OllamaClient


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../Saved Items.csv", "Date Saved,URL\n")
    with pytest.raises(UnsafeArchiveError):
        list(iter_source_documents([archive]))


def test_unexpected_zip_members_are_not_read(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("media/private.mov", b"not parsed")
        handle.writestr(
            "Saved Items.csv",
            "Date Saved,URL\n2026-01-01,https://linkedin.com/posts/1\n",
        )
    documents = list(iter_source_documents([archive]))
    assert [document.name for document in documents] == ["Saved Items.csv"]


def test_ollama_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient("https://api.example.com", "model")


def test_config_rejects_nonlocal_ollama(tmp_path: Path) -> None:
    config = AppConfig(
        project_root=tmp_path,
        vault_path=tmp_path,
        notes_folder="Social Saves",
        data_dir=tmp_path / "data",
        ollama_base_url="https://instagram.com",
        ollama_model="anything",
        min_summary_chars=1,
        excerpt_chars=1,
    )
    assert any("loopback" in issue for issue in config.validate())
