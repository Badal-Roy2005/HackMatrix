"""Parser protocol and registry for CI Doctor.

Defines the :class:`Parser` protocol and a :class:`ParserRegistry` that
resolves a parser for a given file path. Built-in parsers are checked first,
then entry points registered in the ``cidoctor.parsers`` group.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cidoctor.core.errors import ParseError
from cidoctor.core.models import TestResult


@runtime_checkable
class Parser(Protocol):
    """Protocol for result-file parsers.

    Concrete parsers expose a ``name`` and implement ``can_parse`` (does this
    parser handle the given path?) and ``parse`` (turn the file into
    :class:`TestResult` objects).
    """

    name: str

    def can_parse(self, path: str | Path) -> bool:
        """Return True if this parser can handle ``path``."""
        ...

    def parse(self, path: str | Path) -> list[TestResult]:
        """Parse ``path`` into a list of :class:`TestResult`."""
        ...


class ParserRegistry:
    """Registry of built-in and entry-point parsers.

    ``get_parser`` checks built-in parsers first, then entry points in the
    ``cidoctor.parsers`` group, raising :class:`ParseError` if none match.
    """

    def __init__(self) -> None:
        self._parsers: list[Parser] = []

    def register(self, parser: Parser) -> None:
        """Register a built-in parser (checked before entry points)."""
        self._parsers.append(parser)

    def get_parser(self, path: str | Path) -> Parser:
        """Return a parser that can parse ``path``.

        Args:
            path: path to the results file.

        Returns:
            A :class:`Parser` instance.

        Raises:
            ParseError: if no registered or entry-point parser matches.
        """
        path = Path(path)

        for parser in self._parsers:
            if parser.can_parse(path):
                return parser

        entry_points: list[object] = []
        try:
            entry_points = list(
                importlib.metadata.entry_points(group="cidoctor.parsers")
            )
        except Exception:  # pragma: no cover - metadata lookup is defensive  # noqa: BLE001
            entry_points = []

        for entry_point in entry_points:
            factory = _load_entry_point(entry_point)
            if factory is None:
                continue
            candidate = factory() if callable(factory) else factory
            if isinstance(candidate, Parser) and candidate.can_parse(path):
                self.register(candidate)
                return candidate

        raise ParseError(f"No parser found for {path}")


def _load_entry_point(entry_point: Any) -> object | None:
    """Load an entry point factory, returning None on any failure.

    Plugin loading must never crash parser resolution, so every failure mode
    is swallowed here.
    """
    try:
        return entry_point.load()  # type: ignore[no-any-return]
    except (ImportError, AttributeError, ValueError, TypeError, OSError):  # pragma: no cover - defensive
        return None


def default_registry() -> ParserRegistry:
    """Return a registry pre-loaded with the built-in JUnit parser."""
    from cidoctor.parsers.junit import JUnitParser

    registry = ParserRegistry()
    registry.register(JUnitParser())
    return registry
