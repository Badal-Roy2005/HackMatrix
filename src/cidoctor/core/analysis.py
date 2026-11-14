"""Analysis engine for CI Doctor.

Parses a results file, fingerprints and groups failures, saves the run to the
store, and computes flaky verdicts for every test in the run.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cidoctor.core.config import Settings
from cidoctor.core.fingerprint import fingerprint, group_failures
from cidoctor.core.flaky import evaluate_test
from cidoctor.core.models import AnalysisReport, FlakyVerdict, Run, TestResult
from cidoctor.parsers import default_registry
from cidoctor.storage.base import Store


def _detect_commit() -> str:
    """Auto-detect the current git commit SHA.

    Uses a subprocess argument list (no shell). Falls back to GITHUB_SHA, then
    ``'unknown'``.
    """
    import os

    env_sha = os.environ.get("GITHUB_SHA")
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return env_sha or "unknown"


def _detect_branch() -> str:
    """Auto-detect the current git branch.

    Uses a subprocess argument list (no shell). Falls back to GITHUB_REF_NAME,
    then ``'unknown'``.
    """
    import os

    env_ref = os.environ.get("GITHUB_REF_NAME")
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return env_ref or "unknown"


def analyze(
    results_path: str | Path,
    log_path: str | Path | None = None,
    commit: str | None = None,
    branch: str | None = None,
    store: Store | None = None,
    settings: Settings | None = None,
) -> AnalysisReport:
    """Run a full end-to-end analysis of a results file.

    Parses the file, fingerprints and groups failures, saves the run, then
    computes flaky verdicts for every test in the run.

    Args:
        results_path: path to the JUnit XML results file.
        log_path: optional path to a log file (accepted for interface parity;
            not yet used by the analyzer).
        commit: explicit commit SHA; auto-detected with git if not given.
        branch: explicit branch name; auto-detected with git if not given.
        store: run-history store; required to persist the run and compute
            flaky verdicts.
        settings: runtime settings (window_days, min_runs, threshold,
            main_branch).

    Returns:
        An :class:`AnalysisReport`.

    Raises:
        ParseError: if the results file cannot be parsed.
        ValueError: if no store is provided.
    """
    if store is None:
        raise ValueError("store is required to analyze results")

    settings = settings or Settings()
    path = Path(results_path)

    parser = default_registry().get_parser(path)
    results = parser.parse(path)

    # Assign fingerprints to results.
    fingerprinted: list[TestResult] = []
    for result in results:
        if result.status in {"failed", "error"}:
            fingerprinted.append(
                result.model_copy(
                    update={
                        "fingerprint": fingerprint(result.message, result.trace)
                    }
                )
            )
        else:
            fingerprinted.append(result)
    results = fingerprinted

    commit = commit or _detect_commit()
    branch = branch or _detect_branch()

    run = Run(
        id=_run_id(commit, branch),
        commit=commit,
        branch=branch,
        created_at=datetime.now(UTC),
        results=results,
    )
    store.save_run(run)

    failure_groups = group_failures(results)

    # Compute flaky verdicts for every test in the run.
    flaky_tests: list[FlakyVerdict] = []
    test_names = {r.name for r in results}
    for name in sorted(test_names):
        history = store.test_history(name, since=None)
        verdict = evaluate_test(
            history,
            threshold=settings.threshold,
            min_runs=settings.min_runs,
            main_branch=settings.main_branch,
            test=name,
        )
        flaky_tests.append(verdict)

    return AnalysisReport(
        run=run,
        failure_groups=failure_groups,
        flaky_tests=flaky_tests,
    )


def _run_id(commit: str, branch: str) -> str:
    """Build a safe run id from commit and branch."""
    safe_commit = "".join(c for c in commit if c.isalnum() or c in {"-", "_"})[:40]
    safe_branch = "".join(c for c in branch if c.isalnum() or c in {"-", "_"})[:40]
    return f"{safe_branch}-{safe_commit[:12]}"


__all__ = ["AnalysisReport", "analyze"]
