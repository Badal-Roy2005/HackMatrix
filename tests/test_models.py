"""Tests for the core domain models (Block 1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cidoctor.core.models import (
    AnalysisReport,
    Diagnosis,
    FlakyVerdict,
    Run,
    TestResult,
)


def test_testresult_minimal() -> None:
    result = TestResult(
        name="test_login",
        classname="tests.auth",
        status="passed",
        duration_s=1.5,
    )
    assert result.name == "test_login"
    assert result.status == "passed"
    assert result.duration_s == 1.5
    assert result.message == ""
    assert result.trace == ""
    assert result.fingerprint == ""


def test_testresult_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        TestResult(
            name="",
            classname="tests.auth",
            status="passed",
            duration_s=1.0,
        )


def test_testresult_rejects_negative_duration() -> None:
    with pytest.raises(ValueError):
        TestResult(
            name="test_login",
            classname="tests.auth",
            status="passed",
            duration_s=-1.0,
        )


def test_testresult_rejects_bad_status() -> None:
    with pytest.raises(ValueError):
        TestResult(
            name="test_login",
            classname="tests.auth",
            status="unknown_status",
            duration_s=1.0,
        )


def test_testresult_json_round_trip() -> None:
    result = TestResult(
        name="test_login",
        classname="tests.auth",
        status="failed",
        duration_s=2.5,
        message="assertion failed",
        trace="Traceback ...",
        fingerprint="abc123",
    )
    data = result.model_dump()
    restored = TestResult.model_validate(data)
    assert restored == result
    assert restored.fingerprint == "abc123"


def test_run_validation_and_round_trip() -> None:
    run = Run(
        id="run-1",
        commit="abc123def",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[
            TestResult(
                name="test_login",
                classname="tests.auth",
                status="failed",
                duration_s=1.0,
            )
        ],
    )
    data = run.model_dump()
    restored = Run.model_validate(data)
    assert restored == run
    assert restored.id == "run-1"
    assert restored.branch == "main"


def test_run_rejects_empty_id() -> None:
    with pytest.raises(ValueError):
        Run(
            id="",
            commit="abc",
            branch="main",
            created_at=datetime.now(UTC),
        )


def test_flaky_verdict_validation() -> None:
    verdict = FlakyVerdict(
        test="test_login",
        runs=10,
        pass_rate=0.8,
        verdict="flaky",
        confidence=0.9,
        reason="mixed outcomes",
    )
    assert verdict.verdict == "flaky"
    assert verdict.confidence == 0.9


def test_flaky_verdict_rejects_bad_verdict() -> None:
    with pytest.raises(ValueError):
        FlakyVerdict(
            test="test_login",
            runs=10,
            pass_rate=0.8,
            verdict="weird",
            confidence=0.9,
        )


def test_flaky_verdict_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValueError):
        FlakyVerdict(
            test="test_login",
            runs=10,
            pass_rate=0.8,
            verdict="stable",
            confidence=1.5,
        )


def test_diagnosis_category_validation() -> None:
    diagnosis = Diagnosis(
        root_cause="flaky network call",
        category="infra",
        suggested_fix="retry with backoff",
        confidence=0.8,
        sources=["logs"],
    )
    assert diagnosis.category == "infra"


def test_diagnosis_rejects_bad_category() -> None:
    with pytest.raises(ValueError):
        Diagnosis(
            root_cause="x",
            category="not_a_category",
            confidence=0.5,
        )


def test_analysis_report_requires_results() -> None:
    with pytest.raises(ValueError):
        AnalysisReport(
            run=Run(
                id="run-1",
                commit="abc",
                branch="main",
                created_at=datetime.now(UTC),
            ),
        )
