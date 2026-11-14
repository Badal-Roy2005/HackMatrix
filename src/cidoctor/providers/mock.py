"""Mock provider for CI Doctor.

A deterministic offline provider returning a canned :class:`Diagnosis`.
Selectable with ``--provider mock``. Used for CI tests and as the demo
safety net when no AI provider is available.
"""

from __future__ import annotations

from cidoctor.core.models import Diagnosis
from cidoctor.providers.base import Evidence


class MockProvider:
    """Deterministic offline provider."""

    name = "mock"

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        """Return a canned :class:`Diagnosis` based on the evidence."""
        failure_count = len(evidence.failure_groups)
        flaky_count = len(evidence.flaky_verdicts)

        if failure_count == 0:
            root_cause = "No test failures detected in this run."
            category = "unknown"
            suggested_fix = "No action needed."
            confidence = 1.0
        elif any(
            g["count"] >= 5 for g in evidence.failure_groups
        ):
            root_cause = (
                "Multiple failures share the same fingerprint, indicating a "
                "systematic issue rather than isolated test flakiness."
            )
            category = "test_bug"
            suggested_fix = (
                "Investigate the common root cause across the grouped failures."
            )
            confidence = 0.9
        elif flaky_count > 0:
            root_cause = "Some tests show flaky behavior across runs."
            category = "flaky"
            suggested_fix = "Add retries or stabilize the flaky tests."
            confidence = 0.7
        else:
            root_cause = "Isolated test failures detected."
            category = "test_bug"
            suggested_fix = "Review and fix the failing tests."
            confidence = 0.8

        return Diagnosis(
            root_cause=root_cause,
            category=category,
            suggested_fix=suggested_fix,
            confidence=confidence,
            sources=["mock provider (canned diagnosis)"],
        )
