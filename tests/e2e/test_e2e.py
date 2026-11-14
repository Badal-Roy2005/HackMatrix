"""End-to-end test for CI Doctor.

Builds the wheel with uv build, installs it into a fresh venv, then runs
init, demo seed, analyze, flaky, explain with the mock provider, and
comment --dry-run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: PLW1510
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def venv(tmp_path: Path) -> tuple[Path, Path]:
    """Create a fresh venv with cidoctor installed from a built wheel.

    Returns (venv_python, cidoctor_script).
    """
    # Build the wheel
    build = _run(["uv", "build"], cwd=REPO_ROOT)
    assert build.returncode == 0, f"uv build failed: {build.stderr}"

    # Find the built wheel
    dist_dir = REPO_ROOT / "dist"
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels, "no wheel built"
    wheel = wheels[0]

    # Create venv and install
    venv_dir = tmp_path / "venv"
    _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=REPO_ROOT)
    venv_python = venv_dir / "bin" / "python"
    cidoctor_script = venv_dir / "bin" / "cidoctor"
    _run([str(venv_python), "-m", "pip", "install", str(wheel)], cwd=REPO_ROOT)
    return venv_python, cidoctor_script


def _cidoctor(
    cidoctor_script: Path, cwd: Path, *args: str
) -> subprocess.CompletedProcess:
    return _run([str(cidoctor_script), *args], cwd=cwd)


def test_e2e_from_built_wheel(venv: tuple[Path, Path], tmp_path: Path) -> None:
    """Full e2e: init, demo seed, analyze, flaky, explain (mock), comment --dry-run."""
    _venv_python, cidoctor_script = venv
    workdir = tmp_path / "work"
    workdir.mkdir()

    # init
    r = _cidoctor(cidoctor_script, workdir, "init")
    assert r.returncode == 0, r.stderr
    assert (workdir / "cidoctor.toml").exists()
    assert (workdir / ".cidoctor" / "runs").exists()

    # demo seed
    r = _cidoctor(cidoctor_script, workdir, "demo", "seed")
    assert r.returncode == 0, r.stderr
    assert "simulated" in r.stdout.lower()

    # analyze on a fixture
    fixture = REPO_ROOT / "tests" / "fixtures" / "pytest_style.xml"
    r = _cidoctor(
        cidoctor_script,
        workdir,
        "analyze",
        str(fixture),
        "--commit",
        "abc123",
        "--branch",
        "main",
    )
    assert r.returncode == 1, r.stderr  # failures present -> exit 1

    # flaky
    r = _cidoctor(cidoctor_script, workdir, "flaky", "--since", "14d", "--format", "json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert isinstance(data, list)

    # explain with mock provider
    r = _cidoctor(cidoctor_script, workdir, "explain", "latest", "--provider", "mock")
    assert r.returncode == 0, r.stderr
    assert "Root cause" in r.stdout or "mock" in r.stdout.lower()

    # comment --dry-run
    r = _cidoctor(cidoctor_script, workdir, "comment", "latest", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "<!-- cidoctor:comment -->" in r.stdout


def test_e2e_version(venv: tuple[Path, Path], tmp_path: Path) -> None:
    _venv_python, cidoctor_script = venv
    r = _cidoctor(cidoctor_script, tmp_path, "--version")
    assert r.returncode == 0, r.stderr
    assert "cidoctor" in r.stdout
