"""JUnit XML parser for CI Doctor.

Parses JUnit XML (pytest, jest-junit, and other emitters) into
:class:`TestResult` objects using ``defusedxml`` only, so XML entity attacks
are never resolved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import defusedxml.ElementTree as defused_ET

from cidoctor.core.errors import ParseError
from cidoctor.core.models import TestResult, TestStatus

DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


class JUnitParser:
    """Parse JUnit XML files into :class:`TestResult` objects."""

    name = "junit"

    def __init__(self, max_file_size: int = DEFAULT_MAX_FILE_SIZE) -> None:
        self.max_file_size = max_file_size

    def can_parse(self, path: str | Path) -> bool:
        path = Path(path)
        if not path.is_file() or path.is_dir():
            return False
        return path.suffix.lower() == ".xml"

    def parse(self, path: str | Path) -> list[TestResult]:
        path = Path(path)

        if path.is_dir():
            raise ParseError(f"path is a directory: {path}")
        if not path.exists():
            raise ParseError(f"file not found: {path}")

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ParseError(f"cannot stat file {path}: {exc}") from exc
        if size > self.max_file_size:
            raise ParseError(
                f"file {path} exceeds size limit "
                f"({size} > {self.max_file_size} bytes)"
            )

        try:
            tree = defused_ET.parse(str(path))
        except defused_ET.ParseError as exc:
            raise ParseError(f"malformed XML in {path}: {exc}") from exc
        except Exception as exc:
            raise ParseError(f"cannot parse XML in {path}: {exc}") from exc

        root = tree.getroot()
        if root is None:
            raise ParseError(f"malformed XML in {path}: empty document")
        results: list[TestResult] = []

        # Root may be <testsuites> (multiple suites) or a single <testsuite>.
        suites = root if root.tag == "testsuites" else [root]

        for suite in suites:
            self._parse_suite(suite, results)

        return results

    def _parse_suite(
        self, suite: Any, results: list[TestResult]
    ) -> None:
        """Recursively parse a <testsuite> element, handling nested suites."""
        # Neded suites first.
        for nested in suite.findall("testsuite"):
            self._parse_suite(nested, results)

        # system-out / system-err at suite level (appended to following tests).
        suite_out = self._concat(suite, "system-out")
        suite_err = self._concat(suite, "system-err")

        for testcase in suite.findall("testcase"):
            name = testcase.get("name") or ""
            classname = testcase.get("classname") or ""
            duration_s = self._parse_duration(testcase.get("time"))

            status, message, trace = self._classify(testcase)
            if suite_out:
                trace = (trace + "\n" + suite_out) if trace else suite_out
            if suite_err:
                trace = (trace + "\n" + suite_err) if trace else suite_err

            results.append(
                TestResult(
                    name=name,
                    classname=classname,
                    status=status,
                    duration_s=duration_s,
                    message=message,
                    trace=trace,
                )
            )

    @staticmethod
    def _concat(element: Any, tag: str) -> str:
        """Concatenate text of all direct children with the given tag."""
        parts = []
        for child in element.findall(tag):
            text = (child.text or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _parse_duration(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0

    def _classify(
        self, testcase: Any
    ) -> tuple[str, str, str]:
        """Determine status, message and trace for a <testcase>."""
        failure = testcase.find("failure")
        error = testcase.find("error")
        skipped = testcase.find("skipped")

        if failure is not None:
            return (
                TestStatus.FAILED,
                failure.get("message") or "",
                failure.text or "",
            )
        if error is not None:
            return (
                TestStatus.ERROR,
                error.get("message") or "",
                error.text or "",
            )
        if skipped is not None:
            return (
                TestStatus.SKIPPED,
                skipped.get("message") or "",
                skipped.text or "",
            )
        return TestStatus.PASSED, "", ""
