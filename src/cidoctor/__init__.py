"""CI Doctor package."""

from .core.models import (
    AnalysisReport,
    Diagnosis,
    FailureGroup,
    FlakyVerdict,
    Run,
    TestResult,
)
from .sdk import Analyzer

__all__ = [
    "AnalysisReport",
    "Analyzer",
    "Diagnosis",
    "FailureGroup",
    "FlakyVerdict",
    "Run",
    "TestResult",
]

__version__ = "0.1.0"
