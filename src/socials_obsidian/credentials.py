from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    """Private local credential store kept outside the Obsidian vault and Git."""

    def __init__(self, path: Path):
        self.path = path

    def load(self, platform: str) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError(f"Cannot read credentials: {exc}") from exc
        value = raw.get(platform, {}) if isinstance(raw, dict) else {}
        return value if isinstance(value, dict) else {}

    def save(self, platform: str, values: dict[str, Any]) -> None:
        raw: dict[str, Any] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except (OSError, json.JSONDecodeError) as exc:
                raise CredentialError(f"Cannot update credentials: {exc}") from exc
        raw[platform] = values
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".credentials.", suffix=".tmp", dir=self.path.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def configured(self, platform: str) -> bool:
        return bool(self.load(platform))
