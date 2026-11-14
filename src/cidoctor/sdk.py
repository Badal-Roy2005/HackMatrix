"""Public SDK for CI Doctor.

Provides the :class:`Analyzer`, the high-level entry point for programmatic
use of CI Doctor without the CLI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from cidoctor.core.analysis import analyze as _analyze
from cidoctor.core.config import Settings
from cidoctor.core.flaky import evaluate_test
from cidoctor.core.models import (
    AnalysisReport,
    FlakyVerdict,
)
from cidoctor.storage.base import Store


class Analyzer:
    """High-level analyzer for CI Doctor.

    Args:
        provider: an AI provider for diagnosis (optional; used by ``explain``).
        store: run-history store. Required for ``analyze`` and ``flaky``.
        settings: runtime settings. Defaults to :class:`Settings`.
    """

    def __init__(
        self,
        provider: Any = None,
        store: Store | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.settings = settings or Settings()

    def analyze(
        self,
        path: str | Path,
        log: str | Path | None = None,
    ) -> AnalysisReport:
        """Analyze a results file and return an :class:`AnalysisReport`.

        Args:
            path: path to the JUnit XML results file.
            log: optional path to a log file.

        Returns:
            An :class:`AnalysisReport`.

        Raises:
            ValueError: if no store is configured.
            ParseError: if the results file cannot be parsed.
        """
        if self.store is None:
            raise ValueError("store is required to analyze results")
        return _analyze(
            results_path=path,
            log_path=log,
            commit=None,
            branch=None,
            store=self.store,
            settings=self.settings,
        )

    def flaky(self, since: datetime | None = None) -> list[FlakyVerdict]:
        """Return flaky verdicts for every test in the store history.

        Args:
            since: optional lower bound on run creation time.

        Returns:
            A list of :class:`FlakyVerdict`, one per test, ranked by
            confidence descending.

        Raises:
            ValueError: if no store is configured.
        """
        if self.store is None:
            raise ValueError("store is required to compute flaky verdicts")

        # Gather every test name in the store history.
        test_names: set[str] = set()
        for run in self.store.list_runs(since=since):
            for result in run.results:
                test_names.add(result.name)

        verdicts: list[FlakyVerdict] = []
        for name in sorted(test_names):
            history = self.store.test_history(name, since=since)
            verdict = evaluate_test(
                history,
                threshold=self.settings.threshold,
                min_runs=self.settings.min_runs,
                main_branch=self.settings.main_branch,
                test=name,
            )
            verdicts.append(verdict)

        verdicts.sort(key=lambda v: v.confidence, reverse=True)
        return verdicts


__all__ = ["Analyzer"]
