"""Tests for the JSON storage and flaky engine (Block 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from logging import WARNING
from unittest.mock import patch

import pytest

from cidoctor.core.errors import StorageError
from cidoctor.core.flaky import evaluate_test, wilson_interval
from cidoctor.core.models import Run, TestResult
from cidoctor.storage import JsonStore


def _make_run(
    run_id: str,
    commit: str,
    branch: str,
    results: list[TestResult],
    created_at: datetime | None = None,
) -> Run:
    if created_at is None:
        created_at = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    return Run(
        id=run_id,
        commit=commit,
        branch=branch,
        created_at=created_at,
        results=results,
    )


def _result(name: str, status: str) -> TestResult:
    return TestResult(
        name=name,
        classname="tests.mod",
        status=status,
        duration_s=1.0,
    )


# --- store round-trip ---


def test_save_and_load_round_trip(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run(
        "run-1",
        "abc123",
        "main",
        [_result("test_login", "failed"), _result("test_logout", "passed")],
    )
    store.save_run(run)

    loaded = store.get_run("run-1")
    assert loaded is not None
    assert loaded.id == "run-1"
    assert loaded.commit == "abc123"
    assert loaded.branch == "main"
    assert len(loaded.results) == 2
    assert loaded.results[0].name == "test_login"
    assert loaded.results[0].status == "failed"


def test_get_run_latest_returns_most_recent(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    older = _make_run(
        "run-1",
        "abc",
        "main",
        [],
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
    )
    newer = _make_run(
        "run-2",
        "def",
        "main",
        [],
        created_at=datetime(2026, 11, 14, 12, 5, 0, tzinfo=UTC),
    )
    store.save_run(older)
    store.save_run(newer)

    latest = store.get_run("latest")
    assert latest is not None
    assert latest.id == "run-2"


def test_get_run_unknown_returns_none(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    assert store.get_run("does-not-exist") is None


def test_list_runs_since_filter(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    now = datetime.now(UTC)
    t1 = now - timedelta(hours=2)
    t2 = now - timedelta(hours=1)
    r1 = _make_run("run-1", "a", "main", [], created_at=t1)
    r2 = _make_run("run-2", "b", "main", [], created_at=t2)
    store.save_run(r1)
    store.save_run(r2)

    all_runs = store.list_runs()
    assert len(all_runs) == 2

    since = now - timedelta(minutes=90)
    recent = store.list_runs(since=since)
    assert len(recent) == 1
    assert recent[0].id == "run-2"


def test_list_runs_branch_filter(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    r1 = _make_run("run-1", "a", "main", [])
    r2 = _make_run("run-2", "b", "feature", [])
    store.save_run(r1)
    store.save_run(r2)

    main_runs = store.list_runs(branch="main")
    assert len(main_runs) == 1
    assert main_runs[0].id == "run-1"


# --- atomic write ---


def test_atomic_write_leaves_no_partial_run(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run("run-1", "abc", "main", [])

    # Simulate a failure after the temp write: make os.replace fail.
    import os as _os

    def failing_replace(src, dst):
        raise OSError("simulated crash")

    with patch.object(_os, "replace", side_effect=failing_replace), pytest.raises(StorageError):
        store.save_run(run)

    # No partial .json run file should remain (only possible temp files).
    json_files = list((tmp_path / "runs").glob("*.json"))
    assert json_files == [], f"unexpected leftover files: {json_files}"


# --- corrupt / unknown schema ---


def test_corrupt_file_skipped_with_warning(tmp_path, caplog) -> None:
    store = JsonStore(tmp_path / "runs")
    good = _make_run("run-good", "a", "main", [])
    store.save_run(good)

    # Write a corrupt file.
    corrupt = tmp_path / "runs" / "20261114T120000_corrupt.json"
    corrupt.write_text("{not valid json")

    with caplog.at_level(WARNING):
        loaded = store.get_run("latest")
    assert loaded is not None
    assert loaded.id == "run-good"
    assert "skipping corrupt run file" in caplog.text


def test_unknown_schema_version_skipped(tmp_path, caplog) -> None:
    store = JsonStore(tmp_path / "runs")
    good = _make_run("run-good", "a", "main", [])
    store.save_run(good)

    bad = tmp_path / "runs" / "20261114T120001_badschema.json"
    bad.write_text('{"schema_version": 999, "run": {}}')

    with caplog.at_level(WARNING):
        loaded = store.get_run("latest")
    assert loaded is not None
    assert loaded.id == "run-good"
    assert "unknown schema version" in caplog.text


# --- hostile ids and path traversal ---


def test_hostile_run_id_rejected(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run("../../etc/passwd", "a", "main", [])
    with pytest.raises(StorageError):
        store.save_run(run)


def test_hostile_run_id_chars_rejected(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run("run; rm -rf /", "a", "main", [])
    with pytest.raises(StorageError):
        store.save_run(run)


def test_path_traversal_rejected(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    # get_run with a traversal-ish id should not escape the store dir.
    result = store.get_run("../../../tmp/evil")
    assert result is None


def test_symlink_skipped(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    good = _make_run("run-good", "a", "main", [])
    store.save_run(good)

    # Create a symlink inside the store dir pointing outside.
    link = tmp_path / "runs" / "20261114T120000_link.json"
    link.symlink_to("/etc/passwd")

    # Should not crash and should still find the good run.
    loaded = store.get_run("latest")
    assert loaded is not None
    assert loaded.id == "run-good"


# --- test_history ---


def test_test_history_returns_shape(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run(
        "run-1",
        "abc",
        "main",
        [_result("test_login", "failed"), _result("test_login", "passed")],
    )
    store.save_run(run)

    history = store.test_history("test_login")
    assert len(history) == 2
    for entry in history:
        assert set(entry) == {"run_id", "commit", "branch", "created_at", "status"}
    statuses = {e["status"] for e in history}
    assert statuses == {"failed", "passed"}


def test_test_history_cache_avoids_reread(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    run = _make_run("run-1", "abc", "main", [_result("test_login", "passed")])
    store.save_run(run)

    h1 = store.test_history("test_login")
    h2 = store.test_history("test_login")
    assert h1 == h2
    # Same object from cache.
    assert store.test_history("test_login") is h1


# --- performance ---


def test_test_history_30_runs_10k_results_under_3s(tmp_path) -> None:
    import time

    store = JsonStore(tmp_path / "runs")
    base = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    for i in range(30):
        created = base + timedelta(seconds=i)
        results = [
            TestResult(
                name=f"test_{j}",
                classname="mod",
                status="passed" if j % 2 == 0 else "failed",
                duration_s=0.1,
            )
            for j in range(10_000)
        ]
        run = Run(
            id=f"run-{i}",
            commit="abc",
            branch="main",
            created_at=created,
            results=results,
        )
        store.save_run(run)

    start = time.monotonic()
    history = store.test_history("test_0")
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"test_history took {elapsed:.2f}s"
    assert len(history) == 30


# --- flaky: Wilson interval ---


def test_wilson_interval_known_values() -> None:
    lower, upper = wilson_interval(0, 10)
    assert lower == 0.0
    # 0/10: Wilson upper is ~0.277, not near 1.0.
    assert 0.2 < upper < 0.4

    lower, upper = wilson_interval(10, 10)
    assert lower > 0.6
    assert upper == 1.0

    lower, upper = wilson_interval(5, 10)
    assert 0.0 < lower < 0.5 < upper < 1.0

    lower, upper = wilson_interval(0, 0)
    assert lower == 0.0
    assert upper == 1.0


def test_wilson_interval_monotonic() -> None:
    lo1, hi1 = wilson_interval(5, 10)
    lo2, hi2 = wilson_interval(8, 10)
    assert lo2 > lo1
    assert hi2 > hi1


# --- flaky: same-commit detection ---


def test_same_commit_mixed_is_flaky_strong_confidence() -> None:
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "failed"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "passed"}
        for i in range(2, 10)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "flaky"
    assert verdict.confidence >= 0.9
    assert "same commit" in verdict.reason


def test_same_commit_passed_and_skipped_is_not_flaky() -> None:
    # passed + skipped on the same commit is not mixed evidence of flakiness.
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "skipped"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "passed"}
        for i in range(2, 15)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "stable"


def test_same_commit_failed_and_skipped_is_not_flaky() -> None:
    # failed + skipped on the same commit (no pass) is not mixed pass/fail.
    # pass_rate = 19/20 = 0.95, not below threshold, so Rule 3 does not fire;
    # Rule 2 does not fire because c1 has no pass. Expect stable.
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "failed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "skipped"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "passed"}
        for i in range(2, 21)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "stable"


def test_passed_and_failed_same_commit_is_flaky() -> None:
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "failed"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "passed"}
        for i in range(2, 15)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "flaky"
    assert verdict.confidence >= 0.9


# --- flaky: insufficient data ---


def test_fewer_than_min_runs_is_insufficient_data() -> None:
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c2", "branch": "main", "created_at": "t2", "status": "passed"},
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "insufficient_data"
    assert verdict.confidence == 0.2


# --- flaky: five runs never produce a high-confidence verdict ---


def test_five_runs_never_high_confidence() -> None:
    # Various 5-run sequences; none should yield high-confidence flaky/failing.
    sequences = [
        ["passed", "passed", "passed", "failed", "failed"],
        ["passed", "failed", "passed", "failed", "passed"],
        ["passed", "passed", "passed", "passed", "passed"],
        ["failed", "failed", "failed", "failed", "failed"],
    ]
    for statuses in sequences:
        history = [
            {
                "commit": f"c{i}",
                "branch": "main",
                "created_at": f"t{i}",
                "status": s,
            }
            for i, s in enumerate(statuses)
        ]
        verdict = evaluate_test(
            history, threshold=0.95, min_runs=10, main_branch="main"
        )
        assert verdict.verdict == "insufficient_data"
        assert verdict.confidence == 0.2


def test_five_runs_same_commit_mixed_is_not_high_confidence() -> None:
    # Same commit has pass + fail, but only 5 runs total (< min_runs=10).
    # The insufficient_data gate is checked first, so no high-confidence
    # flaky verdict can result.
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "failed"},
        {"commit": "c2", "branch": "main", "created_at": "t3", "status": "passed"},
        {"commit": "c3", "branch": "main", "created_at": "t4", "status": "passed"},
        {"commit": "c4", "branch": "main", "created_at": "t5", "status": "passed"},
    ]
    verdict = evaluate_test(
        history, threshold=0.95, min_runs=10, main_branch="main"
    )
    assert verdict.verdict == "insufficient_data"
    assert verdict.confidence == 0.2


# --- flaky: stable ---


def test_stable_sequence() -> None:
    history = [
        {
            "commit": f"c{i}",
            "branch": "main",
            "created_at": f"t{i}",
            "status": "passed",
        }
        for i in range(15)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "stable"
    assert verdict.confidence > 0.5


# --- flaky: failing (consistently failing) ---


def test_consistently_failing_sequence() -> None:
    history = [
        {
            "commit": f"c{i}",
            "branch": "main",
            "created_at": f"t{i}",
            "status": "failed",
        }
        for i in range(15)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "failing"
    assert verdict.confidence > 0.9


# --- flaky: main-branch flaky with mixed outcomes ---


def test_main_branch_flaky_below_threshold() -> None:
    # 15 runs on main, 9 passed 6 failed -> pass rate 0.6 < 0.95, mixed.
    history = [
        {
            "commit": f"c{i}",
            "branch": "main",
            "created_at": f"t{i}",
            "status": "passed" if i < 9 else "failed",
        }
        for i in range(15)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.verdict == "flaky"
    assert 0.0 < verdict.confidence <= 1.0


def test_pass_rate_excludes_skipped() -> None:
    # 2 passed, 2 failed, 100 skipped: pass_rate = 2/4 = 0.5, not 2/104.
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c2", "branch": "main", "created_at": "t2", "status": "passed"},
        {"commit": "c3", "branch": "main", "created_at": "t3", "status": "failed"},
        {"commit": "c4", "branch": "main", "created_at": "t4", "status": "failed"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "skipped"}
        for i in range(5, 105)
    ]
    verdict = evaluate_test(history, threshold=0.95, min_runs=10, main_branch="main")
    assert verdict.pass_rate == 0.5
    assert verdict.verdict == "flaky"


# --- hostile test names ---


def test_hostile_test_name_in_history(tmp_path) -> None:
    store = JsonStore(tmp_path / "runs")
    hostile = "../../etc/passwd"
    run = _make_run("run-1", "abc", "main", [_result(hostile, "failed")])
    store.save_run(run)

    # Should not crash or escape.
    history = store.test_history(hostile)
    assert len(history) == 1
    assert history[0]["run_id"] == "run-1"
