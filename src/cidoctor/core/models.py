"""Pydantic v2 domain models for CI Doctor.

All models use strict validation, clean JSON round-trip via
``model_dump`` / ``model_validate``, and immutable ``frozen`` instances where
appropriate so stored run history can be treated as read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TestStatus(str):
    """Status of a single test execution."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class TestResult(BaseModel):
    """A single test execution result parsed from a results file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Field(min_length=1)]
    classname: Annotated[str, Field(min_length=1)]
    status: Annotated[str, Field(min_length=1)]
    duration_s: Annotated[float, Field(ge=0)]
    message: str = ""
    trace: str = ""
    fingerprint: str = ""

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        allowed = {
            TestStatus.PASSED,
            TestStatus.FAILED,
            TestStatus.ERROR,
            TestStatus.SKIPPED,
        }
        if v not in allowed:
            raise ValueError(
                f"status must be one of {sorted(allowed)}, got {v!r}"
            )
        return v

    @field_validator("name", "classname")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v.strip()


class Run(BaseModel):
    """A stored CI run, one per JSON file in the run history store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(min_length=1)]
    branch: Annotated[str, Field(min_length=1)]
    created_at: datetime
    results: list[TestResult] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("run id must be a non-empty string")
        return v.strip()

    @field_validator("commit", "branch")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v.strip()


class FailureGroup(BaseModel):
    """Failures grouped by a shared fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fingerprint: Annotated[str, Field(min_length=1)]
    count: Annotated[int, Field(ge=0)]
    sample_message: str = ""
    tests: list[str] = Field(default_factory=list)

    @field_validator("tests")
    @classmethod
    def _validate_tests(cls, v: list[str]) -> list[str]:
        return [t.strip() for t in v if t.strip()]


class FlakyVerdict(BaseModel):
    """Flaky-test verdict computed from a test's run history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    test: Annotated[str, Field(min_length=1)]
    runs: Annotated[int, Field(ge=0)]
    pass_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    verdict: Annotated[str, Field(min_length=1)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reason: str = ""

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, v: str) -> str:
        allowed = {
            "stable",
            "flaky",
            "failing",
            "insufficient_data",
        }
        if v not in allowed:
            raise ValueError(
                f"verdict must be one of {sorted(allowed)}, got {v!r}"
            )
        return v

    @field_validator("test")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v.strip()


class Diagnosis(BaseModel):
    """AI-produced root-cause diagnosis for a failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_cause: Annotated[str, Field(min_length=1)]
    category: Annotated[str, Field(min_length=1)]
    suggested_fix: str = ""
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    sources: list[str] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        allowed = {"test_bug", "infra", "dependency", "flaky", "unknown"}
        if v not in allowed:
            raise ValueError(
                f"category must be one of {sorted(allowed)}, got {v!r}"
            )
        return v

    @field_validator("root_cause")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v.strip()

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s.strip()]


class AnalysisReport(BaseModel):
    """Complete analysis result for a single run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: Run
    failure_groups: list[FailureGroup] = Field(default_factory=list)
    flaky_tests: list[FlakyVerdict] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None

    @field_validator("run")
    @classmethod
    def _validate_run(cls, v: Run) -> Run:
        if not v.results:
            raise ValueError("run must contain at least one result")
        return v


__all__ = [
    "AnalysisReport",
    "Diagnosis",
    "FailureGroup",
    "FlakyVerdict",
    "Run",
    "TestResult",
    "TestStatus",
]
