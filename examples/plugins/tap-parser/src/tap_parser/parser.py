"""TAP (Test Anything Protocol) parser plugin for CI Doctor.

This is a standalone package that registers itself through the
``cidoctor.parsers`` entry point group. It demonstrates that third-party
parsers can be added without modifying CI Doctor core.
"""

from __future__ import annotations

from pathlib import Path


class TAPParser:
    """Parse TAP output into TestResult-like objects."""

    name = "tap"

    def can_parse(self, path: str | Path) -> bool:
        path = Path(path)
        return path.is_file() and path.suffix.lower() in {".tap", ".txt"}

    def parse(self, path: str | Path) -> list:
        path = Path(path)
        results = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ok "):
                    name = line[3:].strip() or "untitled"
                    results.append(
                        {
                            "name": name,
                            "classname": "tap",
                            "status": "passed",
                            "duration_s": 0.0,
                            "message": "",
                            "trace": "",
                            "fingerprint": "",
                        }
                    )
                elif line.startswith("not ok "):
                    name = line[7:].strip() or "untitled"
                    results.append(
                        {
                            "name": name,
                            "classname": "tap",
                            "status": "failed",
                            "duration_s": 0.0,
                            "message": "TAP failure",
                            "trace": line,
                            "fingerprint": "",
                        }
                    )
        return results


def TAPParserFactory() -> TAPParser:
    """Entry point factory for the cidoctor.parsers group."""
    return TAPParser()
