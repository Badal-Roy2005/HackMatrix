"""Security checklist tests for CI Doctor."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_shell_true_in_src() -> None:
    """No shell=True anywhere in src/."""
    src_dir = REPO_ROOT / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "shell=True" not in content, f"shell=True found in {py_file}"


def test_no_eval_in_src() -> None:
    """No eval() anywhere in src/."""
    src_dir = REPO_ROOT / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "eval(" not in content, f"eval() found in {py_file}"


def test_no_secrets_in_logs() -> None:
    """Logging never logs API keys or secrets."""
    src_dir = REPO_ROOT / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        # Check that GEMINI_API_KEY is not logged directly
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            if "logging" in line.lower() or "logger" in line.lower():
                assert "GEMINI_API_KEY" not in line, (
                    f"Possible API key log at {py_file}:{i}"
                )


def test_defusedxml_used_for_xml() -> None:
    """XML parsing uses defusedxml, not xml.etree.ElementTree directly."""
    src_dir = REPO_ROOT / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "import xml.etree" not in content, (
            f"xml.etree import found in {py_file} - use defusedxml instead"
        )


@pytest.mark.security
def test_pip_audit_passes() -> None:
    """pip-audit passes on dependencies (if pip-audit is installed)."""
    import subprocess

    try:
        result = subprocess.run(  # noqa: PLW1510 - return code checked below
            ["pip-audit", "--desc", "on"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("pip-audit not installed")
    assert result.returncode == 0, f"pip-audit found vulnerabilities: {result.stderr}"
