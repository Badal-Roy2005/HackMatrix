"""Failure fingerprinting for CI Doctor.

Normalizes failure messages and stack traces so that the same underlying
failure produces the same fingerprint across runs, then hashes the result to
a stable 12-character identifier.

Normalization steps (applied in order):

1. ISO and other timestamps are replaced with ``[TS]``.
2. ``0x`` memory addresses are replaced with ``[ADDR]``.
3. UUIDs are replaced with ``[UUID]``.
4. Line numbers (``, line N``) are replaced with ``, line N`` -> ``, line [LN]``.
5. Absolute paths are replaced by their basename.
6. Temp directories are replaced with ``[TEMP]``.
7. Ports are replaced with ``[PORT]``.
8. Durations like ``1.23s`` are replaced with ``[DUR]``.
9. Long digit runs (6+ digits) are replaced with ``[NUM]``.
10. Whitespace is collapsed.

Stack traces keep the exception type and the first three stack frames only.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import TypedDict

from rapidfuzz import fuzz, process

from cidoctor.core.models import FailureGroup, TestResult, TestStatus


class _GroupEntry(TypedDict):
    fingerprint: str
    count: int
    sample_message: str
    tests: list[str]

# --- normalization patterns ---

_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_TIMESTAMP = re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?")
_HEX_ADDR = re.compile(r"0[xX][0-9a-fA-F]+")
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LINE_NUMBER = re.compile(r",\s*line\s+\d+")
_ABS_PATH = re.compile(r"(?:/[A-Za-z0-9_.\- ]+)+/[A-Za-z0-9_.\-]+")
_TEMP_DIR = re.compile(
    r"/(?:tmp|var/folders|private/tmp|var/tmp)/[^ \"'\\)\\]]+"
)
_PORT = re.compile(r":\d{2,5}(?=[/ \"'\\)\\]|$)")
_DURATION = re.compile(r"\d+(?:\.\d+)?(?:ms|s|m|h)\b")
_LONG_DIGITS = re.compile(r"\d{6,}")


def _keep_basename(match: re.Match[str]) -> str:
    """Replace an absolute path with its basename."""
    return match.group(0).rsplit("/", 1)[-1]


def normalize(message: str, trace: str) -> str:
    """Normalize a failure message + trace into a stable, comparable string.

    The message and trace are joined with a newline, all variable parts are
    redacted/replaced with placeholders, stack traces are reduced to the
    exception type plus the first three frames, and whitespace is collapsed.
    """
    combined = (message or "") + "\n" + (trace or "")

    combined = _ISO_TIMESTAMP.sub("[TS]", combined)
    combined = _TIMESTAMP.sub("[TS]", combined)
    combined = _HEX_ADDR.sub("[ADDR]", combined)
    combined = _UUID.sub("[UUID]", combined)
    combined = _LINE_NUMBER.sub(", line [LN]", combined)
    combined = _ABS_PATH.sub(_keep_basename, combined)
    combined = _TEMP_DIR.sub("[TEMP]", combined)
    combined = _PORT.sub(":[PORT]", combined)
    combined = _DURATION.sub("[DUR]", combined)
    combined = _LONG_DIGITS.sub("[NUM]", combined)

    combined = _reduce_stack_trace(combined)

    # Collapse whitespace.
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined


def _reduce_stack_trace(text: str) -> str:
    """Keep the exception type and the first three stack frames."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return text

    frame_indices = [
        i
        for i, ln in enumerate(lines)
        if re.search(r'\bFile\s+"| at |\bin\b', ln)
    ]

    # Exception-type line: typically the last non-frame line that looks like
    # "ErrorType: message" or "panic: message".
    exception_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*: ", lines[i]):
            exception_idx = i
            break

    keep = set()
    if exception_idx is not None:
        keep.add(exception_idx)
    keep.update(frame_indices[:3])

    kept = [ln for i, ln in enumerate(lines) if i in keep]
    return "\n".join(kept) if kept else text


def fingerprint(message: str, trace: str) -> str:
    """Return a 12-character SHA-1 hex fingerprint of the normalized text."""
    normalized = normalize(message, trace)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def group_failures(results: Iterable[TestResult]) -> list[FailureGroup]:
    """Group failed/error results by fingerprint.

    Returns a list of :class:`FailureGroup` sorted by count descending.
    Only non-passed results are grouped.
    """
    groups: dict[str, _GroupEntry] = {}

    for result in results:
        if result.status in {TestStatus.PASSED, TestStatus.SKIPPED}:
            continue
        fp = fingerprint(result.message, result.trace)
        entry = groups.setdefault(
            fp,
            {
                "fingerprint": fp,
                "count": 0,
                "sample_message": result.message,
                "tests": [],
            },
        )
        entry["count"] += 1
        if result.name not in entry["tests"]:
            entry["tests"].append(result.name)

    failure_groups = [
        FailureGroup(
            fingerprint=entry["fingerprint"],
            count=entry["count"],
            sample_message=entry["sample_message"],
            tests=entry["tests"],
        )
        for entry in groups.values()
    ]
    failure_groups.sort(key=lambda g: g.count, reverse=True)
    return failure_groups


def find_similar(
    text: str, candidates: list[str], threshold: float = 80.0
) -> list[tuple[str, float]]:
    """Return candidates similar to ``text`` above ``threshold``.

    Uses :mod:`rapidfuzz` (``fuzz.WRatio``). Results are sorted by score
    descending.
    """
    if not candidates:
        return []
    matches = process.extract(
        text, candidates, scorer=fuzz.WRatio, score_cutoff=threshold
    )
    # process.extract returns list of (candidate, score, index) tuples.
    return [(candidate, float(score)) for candidate, score, _ in matches]
