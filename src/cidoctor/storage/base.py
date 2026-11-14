"""Store protocol for CI Doctor run history.

Everything outside ``storage/`` must depend only on this protocol, not on the
concrete :class:`~storage.json_store.JsonStore`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from cidoctor.core.models import Run


@runtime_checkable
class Store(Protocol):
    """Protocol for the CI Doctor run-history store."""

    def save_run(self, run: Run) -> None:
        """Persist a :class:`Run` to the store."""

    def get_run(self, id_or_latest: str) -> Run | None:
        """Return a run by id, or the most recent run when ``id_or_latest`` is
        ``'latest'``. Returns ``None`` when no matching run exists."""

    def list_runs(
        self, since: datetime | None = None, branch: str | None = None
    ) -> list[Run]:
        """List runs, optionally filtered by ``since`` and ``branch``."""

    def test_history(
        self, name: str, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return per-run history for a test.

        Each entry is a dict with keys ``run_id``, ``commit``, ``branch``,
        ``created_at`` and ``status``.
        """

    def past_failures_for_fingerprint(
        self, fingerprint: str, limit: int = 10
    ) -> list[str]:
        """Return test names that previously failed with ``fingerprint``."""
