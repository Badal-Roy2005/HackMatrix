"""Tests for Block 4: analysis engine, CLI base, and SDK."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import typer
from typer.testing import CliRunner

from cidoctor import Analyzer, __version__
from cidoctor.cli import _parse_duration, app
from cidoctor.core.analysis import analyze as _run_analysis
from cidoctor.core.config import Settings
from cidoctor.core.models import AnalysisReport, Run, TestResult
from cidoctor.storage import JsonStore

runner = CliRunner()

FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="pytest" tests="3" failures="1" time="5.0">
  <testsuite name="suite" tests="3" failures="1" time="5.0">
    <testcase name="test_pass" classname="mod" time="1.0"/>
    <testcase name="test_fail" classname="mod" time="1.0">
      <failure message="assert x == y" type="AssertionError">AssertionError: assert x == y
  File "mod.py", line 5, in test_fail
    assert x == y</failure>
    </testcase>
    <testcase name="test_skip" classname="mod" time="0.0">
      <skipped message="no data" type="SkipException"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"cidoctor {__version__}" in result.output


def test_init_creates_config_and_store(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "cidoctor.toml"])
    assert result.exit_code == 0
    assert (tmp_path / "cidoctor.toml").is_file()
    assert (tmp_path / ".cidoctor" / "runs").is_dir()
    assert "gitignore" in result.output


def test_analyze_on_fixture(tmp_path) -> None:
    results_file = tmp_path / "results.xml"
    results_file.write_text(FIXTURE_XML)

    settings = Settings().with_overrides(store_path=str(tmp_path / ".cidoctor/runs"))
    store = JsonStore(settings.store_path)

    report = _run_analysis(
        results_path=str(results_file),
        commit="abc123",
        branch="main",
        store=store,
        settings=settings,
    )

    assert isinstance(report.run, Run)
    assert report.run.commit == "abc123"
    assert report.run.branch == "main"
    assert len(report.run.results) == 3
    assert len(report.failure_groups) == 1
    assert report.failure_groups[0].count == 1


def test_analyze_exit_code_nonzero_on_failure(tmp_path) -> None:
    results_file = tmp_path / "results.xml"
    results_file.write_text(FIXTURE_XML)

    result = runner.invoke(app, ["analyze", str(results_file)])
    assert result.exit_code == 1


def test_analyze_missing_file_clean_error(tmp_path) -> None:
    result = runner.invoke(app, ["analyze", str(tmp_path / "nope.xml")])
    assert result.exit_code == 1
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_analyze_directory_path_clean_error(tmp_path) -> None:
    result = runner.invoke(app, ["analyze", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_analyze_json_output_shape(tmp_path) -> None:
    results_file = tmp_path / "results.xml"
    results_file.write_text(FIXTURE_XML)

    settings = Settings().with_overrides(store_path=str(tmp_path / ".cidoctor/runs"))
    store = JsonStore(settings.store_path)

    report = _run_analysis(
        results_path=str(results_file),
        commit="abc123",
        branch="main",
        store=store,
        settings=settings,
    )
    data = report.model_dump()
    assert "run" in data
    assert "failure_groups" in data
    assert "flaky_tests" in data
    assert data["run"]["commit"] == "abc123"


def test_flaky_ranked_output(tmp_path) -> None:
    settings = Settings().with_overrides(
        store_path=str(tmp_path / ".cidoctor/runs"), min_runs=3
    )
    store = JsonStore(settings.store_path)

    base = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        status = "failed" if i % 2 == 0 else "passed"
        run = Run(
            id=f"run-{i}",
            commit="abc",
            branch="main",
            created_at=base + timedelta(hours=i),
            results=[
                TestResult(
                    name="test_flaky",
                    classname="mod",
                    status=status,
                    duration_s=0.5,
                )
            ],
        )
        store.save_run(run)

    analyzer = Analyzer(store=store, settings=settings)
    since = base - timedelta(days=1)
    verdicts = analyzer.flaky(since=since)
    assert len(verdicts) == 1
    assert verdicts[0].test == "test_flaky"


def test_demo_seed_then_flaky_flags_seeded_tests(tmp_path) -> None:
    settings = Settings().with_overrides(
        store_path=str(tmp_path / ".cidoctor/runs"), min_runs=10
    )
    store = JsonStore(settings.store_path)

    # Seed via the same logic as the CLI demo seed command.
    base = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    for i in range(30):
        created = base + timedelta(hours=i)
        results: list[TestResult] = []
        for j in range(60):
            name = f"test_{j:02d}"
            if j == 0:
                status = "failed"
            elif j in (1, 2):
                status = "failed" if i % 2 == 0 else "passed"
            else:
                status = "passed"
            results.append(
                TestResult(
                    name=name,
                    classname="tests.demo",
                    status=status,
                    duration_s=0.5,
                )
            )
        run = Run(
            id=f"demo-{i:02d}",
            commit=f"abc{i:04d}",
            branch="main",
            created_at=created,
            results=results,
        )
        store.save_run(run)

    analyzer = Analyzer(store=store, settings=settings)
    since = base - timedelta(days=1)
    verdicts = analyzer.flaky(since=since)

    flaky_tests = {v.test for v in verdicts if v.verdict == "flaky"}
    failing_tests = {v.test for v in verdicts if v.verdict == "failing"}

    # test_00 is consistently failing; test_01 and test_02 are flaky.
    assert "test_00" in failing_tests
    assert "test_01" in flaky_tests or "test_01" in failing_tests
    assert "test_02" in flaky_tests or "test_02" in failing_tests


def test_sdk_round_trip(tmp_path) -> None:
    settings = Settings().with_overrides(store_path=str(tmp_path / ".cidoctor/runs"))
    store = JsonStore(settings.store_path)
    analyzer = Analyzer(store=store, settings=settings)

    results_file = tmp_path / "results.xml"
    results_file.write_text(FIXTURE_XML)

    report = analyzer.analyze(path=str(results_file))
    assert isinstance(report, AnalysisReport)

    # flaky without any history returns insufficient_data verdicts.
    verdicts = analyzer.flaky(since=None)
    assert all(v.verdict == "insufficient_data" for v in verdicts)


def test_parse_duration() -> None:

    assert _parse_duration("14d") == timedelta(days=14)
    assert _parse_duration("12h") == timedelta(hours=12)
    assert _parse_duration("90m") == timedelta(minutes=90)
    assert _parse_duration("30s") == timedelta(seconds=30)


def test_parse_duration_invalid() -> None:

    with pytest.raises(typer.BadParameter):
        _parse_duration("abc")
    with pytest.raises(typer.BadParameter):
        _parse_duration("14x")
