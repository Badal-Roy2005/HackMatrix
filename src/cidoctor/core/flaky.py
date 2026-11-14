"""Flaky-test detection for CI Doctor.

Public API
----------
``wilson_interval(successes, n, z=1.96)``
    Returns the Wilson score confidence interval ``(lower, upper)`` for a
    proportion ``p = successes / n``.

``evaluate_test(history, threshold, min_runs, main_branch)``
    Returns a :class:`~cidoctor.core.models.FlakyVerdict` for a test given its
    run history.

Confidence computation
----------------------
Confidence is always a value in ``[0, 1]``. It is computed as follows per
verdict:

* ``insufficient_data`` -- low confidence, fixed at ``0.2``, because there are
  fewer than ``min_runs`` observations to draw a conclusion from.
* ``stable`` -- confidence derived from the Wilson *upper* bound on the failure
  rate: ``confidence = 1 - wilson_upper(failures, n)``. When the upper bound on
  failures is tiny, confidence that the test is stable is high.
* ``flaky`` (same-commit mixed outcomes) -- strong confidence, fixed at
  ``0.95``, because a test that both passes and fails on the same commit is
  almost certainly flaky.
* ``flaky`` (main-branch pass rate below threshold with mixed outcomes) --
  confidence derived from the Wilson *lower* bound on the pass rate:
  ``confidence = 1 - wilson_lower(passes, n)``. The further below threshold
  the pass rate is, the higher the confidence.
* ``failing`` -- confidence = ``1 - pass_rate``. A test that almost never
  passes is confidently failing.

Detection rules (evaluated in order)
------------------------------------
1. If fewer than ``min_runs`` runs exist, the verdict is ``insufficient_data``
   with low confidence (0.2). This gate is checked first, so a short history
   (e.g. five runs) can never produce a high-confidence verdict, even when a
   single commit shows both a pass and a fail.
2. If any commit has both a pass and a fail/error, the verdict is ``flaky``
   with strong confidence (0.95). Skipped runs are ignored: passed + skipped
   on the same commit is not mixed.
3. Otherwise, consider only runs on ``main_branch``. If there are at least
   ``min_runs`` such runs, the pass rate is mixed (not 0 or 1), and the pass
   rate is below ``threshold``, the verdict is ``flaky`` with Wilson-derived
   confidence.
4. Otherwise, if there are at least ``min_runs`` runs and the pass rate is
   near zero (``<= 0.05``), the verdict is ``failing``.
5. Otherwise the verdict is ``stable``.

Pass rate is computed over non-skipped runs only; skipped runs are excluded
from the denominator.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from cidoctor.core.models import FlakyVerdict


def wilson_interval(
    successes: int, n: int, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Args:
        successes: number of successes.
        n: total number of trials.
        z: z-score (1.96 for a 95% interval).

    Returns:
        A ``(lower, upper)`` tuple, both in ``[0, 1]``. When ``n`` is zero,
        returns ``(0.0, 1.0)``.
    """
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    half_width = (
        z
        / denominator
        * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    )
    lower = max(0.0, center - half_width)
    upper = min(1.0, center + half_width)
    return lower, upper


def _pass_rate(history: Sequence[dict[str, Any]]) -> float:
    """Pass rate, excluding skipped runs from the denominator.

    Skipped runs are neither passes nor failures, so they are dropped before
    computing the proportion.
    """
    non_skipped = [
        entry for entry in history if entry.get("status") != "skipped"
    ]
    if not non_skipped:
        return 0.0
    passes = sum(1 for entry in non_skipped if entry.get("status") == "passed")
    return passes / len(non_skipped)


def _has_mixed_outcomes_on_commit(history: Sequence[dict[str, Any]]) -> bool:
    """Return True if any commit has both a pass and a fail/error.

    Skipped runs are ignored: a commit with only passed + skipped is not
    mixed and is not evidence of flakiness.
    """
    failure_statuses = {"failed", "error"}
    by_commit: dict[str, set[str]] = {}
    for entry in history:
        commit = entry.get("commit", "unknown")
        status = entry.get("status", "unknown")
        by_commit.setdefault(commit, set()).add(status)
    for statuses in by_commit.values():
        if "passed" in statuses and statuses & failure_statuses:
            return True
    return False


def evaluate_test(
    history: Sequence[dict[str, Any]],
    threshold: float,
    min_runs: int,
    main_branch: str,
) -> FlakyVerdict:
    """Evaluate a flaky-test verdict for a test's run history.

    Args:
        history: list of dicts with keys ``commit``, ``branch``,
            ``created_at`` and ``status`` (as returned by
            :meth:`Store.test_history`).
        threshold: pass-rate threshold below which a test on the main branch
            is considered flaky (when outcomes are mixed).
        min_runs: minimum number of runs required for a conclusive verdict.
        main_branch: the branch treated as the main branch.

    Returns:
        A :class:`FlakyVerdict`.
    """
    test_name = history[0].get("test", "unknown") if history else "unknown"
    n = len(history)

    # Rule 1: insufficient data (checked first, so short histories can never be
    # high-confidence flaky/failing even with same-commit mixed outcomes).
    if n < min_runs:
        return FlakyVerdict(
            test=test_name,
            runs=n,
            pass_rate=_pass_rate(history),
            verdict="insufficient_data",
            confidence=0.2,
            reason=f"only {n} run(s), fewer than min_runs={min_runs}",
        )

    pass_rate = _pass_rate(history)

    # Rule 2: same commit has both pass and fail/error -> flaky, strong
    # confidence. Skipped runs are ignored.
    if _has_mixed_outcomes_on_commit(history):
        return FlakyVerdict(
            test=test_name,
            runs=n,
            pass_rate=pass_rate,
            verdict="flaky",
            confidence=0.95,
            reason="mixed pass and fail/error on the same commit",
        )

    # Rule 3: main branch, mixed outcomes, pass rate below threshold -> flaky.
    main_history = [
        entry for entry in history if entry.get("branch") == main_branch
    ]
    if len(main_history) >= min_runs:
        main_pass_rate = _pass_rate(main_history)
        has_mixed = len(
            {entry.get("status") for entry in main_history}
        ) > 1
        if 0.0 < main_pass_rate < threshold and has_mixed:
            main_passes = round(main_pass_rate * len(main_history))
            pass_lower, _ = wilson_interval(main_passes, len(main_history))
            confidence = 1.0 - pass_lower
            return FlakyVerdict(
                test=test_name,
                runs=n,
                pass_rate=pass_rate,
                verdict="flaky",
                confidence=max(0.0, min(1.0, confidence)),
                reason=(
                    f"main-branch pass rate {main_pass_rate:.2f} below "
                    f"threshold {threshold} with mixed outcomes"
                ),
            )

    # Rule 4: pass rate near zero over min_runs or more -> failing.
    if pass_rate <= 0.05:
        return FlakyVerdict(
            test=test_name,
            runs=n,
            pass_rate=pass_rate,
            verdict="failing",
            confidence=1.0 - pass_rate,
            reason="pass rate near zero over "
            f"{n} run(s) (>= min_runs={min_runs})",
        )

    # Rule 5: otherwise stable.
    failures = n - round(pass_rate * n)
    _, fail_upper = wilson_interval(failures, n)
    confidence = 1.0 - fail_upper
    return FlakyVerdict(
        test=test_name,
        runs=n,
        pass_rate=pass_rate,
        verdict="stable",
        confidence=max(0.0, min(1.0, confidence)),
        reason=f"pass rate {pass_rate:.2f} above threshold {threshold}",
    )


__all__ = ["evaluate_test", "wilson_interval"]
