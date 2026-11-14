"""Logging setup for CI Doctor.

Provides a --verbose switch and a filter that masks anything resembling an
API key so secrets never reach the log output.
"""

from __future__ import annotations

import logging
import re

# NAME=value / NAME: value where NAME hints at a secret.
_NAME_VALUE_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:[._][A-Za-z0-9_]*)*)"
    r"\s*[:=]\s*['\"]?([^'\"]{3,})['\"]?",
    re.IGNORECASE,
)
_SECRET_NAME = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|pwd|private_key)", re.IGNORECASE
)

# Standalone token patterns that appear without a NAME= wrapper.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-+/]{8,}", re.IGNORECASE)
_GHP_RE = re.compile(r"ghp_[A-Za-z0-9]{8,}")
_GITHUB_PAT_RE = re.compile(r"github_pat_[A-Za-z0-9_]{8,}")
_GOOGLE_RE = re.compile(r"AIza[0-9A-Za-z\-_]{8,}")
_SK_RE = re.compile(r"sk-[A-Za-z0-9]{8,}")


def _should_redact_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


def mask_secrets(text: str) -> str:
    """Return ``text`` with secret-like values replaced by ``[REDACTED]``."""

    def replace_name_value(match: re.Match[str]) -> str:
        name = match.group(1)
        if _should_redact_name(name):
            return f"{name}=[REDACTED]"
        return match.group(0)

    text = _NAME_VALUE_RE.sub(replace_name_value, text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _GHP_RE.sub("ghp_[REDACTED]", text)
    text = _GITHUB_PAT_RE.sub("github_pat_[REDACTED]", text)
    text = _GOOGLE_RE.sub("AIza[REDACTED]", text)
    text = _SK_RE.sub("sk-[REDACTED]", text)
    return text


class SecretMaskFilter(logging.Filter):
    """Log filter that redacts values resembling API keys / secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = ()
        return True


def setup_logging(verbose: bool = False) -> None:
    """Configure the root logger for CI Doctor.

    Args:
        verbose: when True, emit DEBUG-level logs; otherwise INFO only.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(SecretMaskFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Keep third-party loggers quiet unless verbose.
    for name in ("httpx", "google.genai", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
