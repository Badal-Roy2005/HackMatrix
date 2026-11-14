"""Tests for the CLI (Block 1)."""

from __future__ import annotations

from typer.testing import CliRunner

from cidoctor import __version__
from cidoctor.cli import app


def test_version_output() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"cidoctor {__version__}" in result.output


def test_help_output() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "cidoctor" in result.output
