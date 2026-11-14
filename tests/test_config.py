"""Tests for the Settings configuration (Block 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cidoctor.core.config import Settings
from cidoctor.core.errors import ConfigError


def test_defaults() -> None:
    settings = Settings()
    assert settings.store_path == Path(".cidoctor/runs")
    assert settings.provider is None
    assert settings.model is None
    assert settings.window_days == 14
    assert settings.min_runs == 10
    assert settings.threshold == 0.95
    assert settings.main_branch == "main"
    assert settings.redact_patterns == []
    assert settings.max_log_chars == 400_000
    assert settings.ollama_host == "http://localhost:11434"


def test_toml_file_overrides_defaults(tmp_path: Path) -> None:
    toml_path = tmp_path / "cidoctor.toml"
    toml_path.write_text(
        """
[cidoctor]
store_path = ".custom/runs"
min_runs = 20
threshold = 0.9
main_branch = "develop"
max_log_chars = 100000
ollama_host = "http://localhost:9999"
"""
    )
    settings = Settings.load(toml_path)
    assert settings.store_path == Path(".custom/runs")
    assert settings.min_runs == 20
    assert settings.threshold == 0.9
    assert settings.main_branch == "develop"
    assert settings.max_log_chars == 100000
    assert settings.ollama_host == "http://localhost:9999"
    # untouched fields keep defaults
    assert settings.window_days == 14


def test_env_vars_override_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "cidoctor.toml"
    toml_path.write_text(
        """
[cidoctor]
min_runs = 20
threshold = 0.9
"""
    )
    monkeypatch.setenv("CIDOCTOR_MIN_RUNS", "50")
    monkeypatch.setenv("CIDOCTOR_THRESHOLD", "0.99")
    settings = Settings.load(toml_path)
    assert settings.min_runs == 50
    assert settings.threshold == 0.99


def test_explicit_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIDOCTOR_MIN_RUNS", "50")
    settings = Settings().with_overrides(min_runs=5, threshold=0.8)
    assert settings.min_runs == 5
    assert settings.threshold == 0.8


def test_unknown_override_raises() -> None:
    with pytest.raises(ConfigError):
        Settings().with_overrides(not_a_setting=1)


def test_invalid_env_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CIDOCTOR_MIN_RUNS", "not-an-int")
    with pytest.raises(ConfigError):
        Settings.load()


def test_load_from_nonexistent_file_uses_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.toml"
    settings = Settings.load(missing)
    assert settings.min_runs == 10
    assert settings.threshold == 0.95
