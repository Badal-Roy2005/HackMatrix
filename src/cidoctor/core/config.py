"""Settings for CI Doctor, layered by precedence (later wins).

1. Defaults (hard-coded here)
2. ``cidoctor.toml`` (parsed with ``tomllib``)
3. ``CIDOCTOR_*`` environment variables
4. Explicit overrides passed to :class:`Settings`
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError


@dataclass
class Settings:
    """Runtime configuration for CI Doctor."""

    store_path: Path = field(default_factory=lambda: Path(".cidoctor/runs"))
    provider: str | None = None
    model: str | None = None
    window_days: int = 14
    min_runs: int = 10
    threshold: float = 0.95
    main_branch: str = "main"
    redact_patterns: list[str] = field(default_factory=list)
    max_log_chars: int = 400_000
    ollama_host: str = "http://localhost:11434"

    @classmethod
    def load(cls, path: str | Path | None = None) -> Settings:
        """Load settings from defaults, a toml file, env vars and overrides.

        Args:
            path: optional path to ``cidoctor.toml``. Defaults to
                ``cidoctor.toml`` in the current working directory.

        Returns:
            A merged :class:`Settings` instance.
        """
        settings = cls()

        # Layer 2: cidoctor.toml
        toml_path = Path(path) if path is not None else Path("cidoctor.toml")
        if toml_path.is_file():
            settings = _merge_toml(settings, toml_path)

        # Layer 3: CIDOCTOR_* environment variables
        settings = _merge_env(settings)

        return settings

    def with_overrides(self, **overrides: Any) -> Settings:
        """Return a copy with explicit field overrides applied (highest layer)."""
        for key, value in overrides.items():
            if not hasattr(self, key):
                raise ConfigError(f"unknown setting: {key!r}")
            setattr(self, key, value)
        return self


def _merge_toml(settings: Settings, path: Path) -> Settings:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    cidoctor_section = data.get("cidoctor", {})
    for key, value in cidoctor_section.items():
        if hasattr(settings, key):
            if key == "store_path" and isinstance(value, str):
                value = Path(value)
            setattr(settings, key, value)
    return settings


def _merge_env(settings: Settings) -> Settings:
    env_mapping: dict[str, tuple[str, Callable[[str], Any]]] = {
        "CIDOCTOR_STORE_PATH": ("store_path", _parse_path),
        "CIDOCTOR_PROVIDER": ("provider", str),
        "CIDOCTOR_MODEL": ("model", str),
        "CIDOCTOR_WINDOW_DAYS": ("window_days", int),
        "CIDOCTOR_MIN_RUNS": ("min_runs", int),
        "CIDOCTOR_THRESHOLD": ("threshold", float),
        "CIDOCTOR_MAIN_BRANCH": ("main_branch", str),
        "CIDOCTOR_MAX_LOG_CHARS": ("max_log_chars", int),
        "CIDOCTOR_OLLAMA_HOST": ("ollama_host", str),
    }
    for env_var, (field_name, parser) in env_mapping.items():
        raw = os.environ.get(env_var)
        if raw is not None and raw != "":
            try:
                setattr(settings, field_name, parser(raw))
            except (ValueError, TypeError) as exc:
                raise ConfigError(
                    f"invalid value for {env_var}: {raw!r}"
                ) from exc
    return settings


def _parse_path(value: str) -> Path:
    return Path(value)
