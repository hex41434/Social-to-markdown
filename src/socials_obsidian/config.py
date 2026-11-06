from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AppConfig:
    project_root: Path
    vault_path: Path
    notes_folder: str
    data_dir: Path
    ollama_base_url: str
    ollama_model: str
    min_summary_chars: int
    excerpt_chars: int

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def credentials_dir(self) -> Path:
        return self.data_dir / "credentials"

    @property
    def x_cookie_path(self) -> Path:
        return self.credentials_dir / "x-cookie.env"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.sqlite"

    @property
    def notes_root(self) -> Path:
        return self.vault_path / self.notes_folder

    def ensure_local_dirs(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.credentials_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def validate(self, require_vault: bool = True) -> list[str]:
        issues: list[str] = []
        if require_vault and not self.vault_path.is_dir():
            issues.append(f"Vault does not exist: {self.vault_path}")
        if (
            Path(self.notes_folder).is_absolute()
            or ".." in Path(self.notes_folder).parts
        ):
            issues.append("notes_folder must be a safe relative path")
        parsed = urlsplit(self.ollama_base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            issues.append("Ollama base_url must use a loopback host")
        if self.min_summary_chars < 1 or self.excerpt_chars < 1:
            issues.append("Ollama character limits must be positive")
        return issues


def load_config(path: Path | None = None) -> AppConfig:
    env_path = os.environ.get("SOCIALS_OBSIDIAN_CONFIG")
    config_path = path or (
        Path(env_path).expanduser() if env_path else PROJECT_ROOT / "config.toml"
    )
    raw: dict = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    ollama = raw.get("ollama", {})
    return AppConfig(
        project_root=PROJECT_ROOT,
        vault_path=Path(
            raw.get("vault_path", "~/Documents/Obsidian Vault")
        ).expanduser(),
        notes_folder=str(raw.get("notes_folder", "Social Saves")),
        data_dir=Path(raw.get("data_dir", PROJECT_ROOT / "data")).expanduser(),
        ollama_base_url=str(ollama.get("base_url", "http://127.0.0.1:11434")).rstrip(
            "/"
        ),
        ollama_model=str(ollama.get("model", "qwen3:4b-instruct")),
        min_summary_chars=int(ollama.get("min_summary_chars", 80)),
        excerpt_chars=int(ollama.get("excerpt_chars", 600)),
    )
