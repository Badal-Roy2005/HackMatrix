"""Secret redaction for CI Doctor.

Masks secrets in text before it is sent to an AI provider or written to a
log. All replacements use the ``[REDACTED]`` placeholder.
"""

from __future__ import annotations

import re

# AWS access key IDs (20 uppercase alphanumerics) and secret keys (40 chars).
_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_AWS_SECRET_RE = re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key[=:]\s*['\"]?[A-Za-z0-9/+=]{20,}['\"]?")

# GitHub tokens.
_GHP_RE = re.compile(r"ghp_[A-Za-z0-9]{36,}")
_GITHUB_PAT_RE = re.compile(r"github_pat_[A-Za-z0-9_]{20,}")

# Google API keys.
_GOOGLE_RE = re.compile(r"AIza[0-9A-Za-z\-_]{35,}")

# Bearer tokens.
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-+/]{8,}")

# Private key blocks.
_PRIVATE_KEY_BEGIN = re.compile(
    r"-----BEGIN\s+(?:RSA\s+)?PRIVATE KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE KEY-----",
    re.DOTALL,
)

# NAME=value or NAME: value where NAME hints at a secret.
# Non-greedy quantifiers to avoid catastrophic backtracking on large inputs.
_SECRET_NAME_VALUE_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+?)*?)\s*[:=]\s*['\"]?([^'\"]{3,})['\"]?",
    re.IGNORECASE,
)
_SECRET_NAME = re.compile(
    r"(?:^|[._])(secret|token|password|passwd|pwd|api[_-]?key|private[_-]?key)\b",
    re.IGNORECASE,
)


def _should_redact_name(name: str) -> bool:
    # Check the full name first (e.g. "API_KEY" contains "key").
    if _SECRET_NAME.search(name):
        return True
    # Also check each dot/underscore-separated part.
    parts = re.split(r"[._]", name)
    return any(bool(_SECRET_NAME.search(part)) for part in parts)


def redact(text: str, extra_patterns: list[re.Pattern[str]] | None = None) -> str:
    """Return ``text`` with secrets replaced by ``[REDACTED]``.

    Masks:
    * AWS keys and secret keys
    * GitHub tokens (``ghp_`` and ``github_pat_``)
    * Google API keys (``AIza...``)
    * Bearer tokens
    * Private key blocks (``-----BEGIN ... PRIVATE KEY-----``)
    * ``NAME=value`` / ``NAME:value`` where NAME contains SECRET, TOKEN,
      PASSWORD, KEY, or similar

    Args:
        text: text to redact.
        extra_patterns: additional compiled regexes to apply.

    Returns:
        Redacted text.
    """
    patterns: list[re.Pattern[str]] = [
        _AWS_KEY_RE,
        _AWS_SECRET_RE,
        _GHP_RE,
        _GITHUB_PAT_RE,
        _GOOGLE_RE,
        _BEARER_RE,
        _PRIVATE_KEY_BEGIN,
    ]
    if extra_patterns:
        patterns.extend(extra_patterns)

    for pattern in patterns:
        if pattern is _BEARER_RE:
            text = pattern.sub("Bearer [REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)

    # NAME=value / NAME: value where NAME hints at a secret.
    # Process line-by-line to avoid quadratic backtracking on large inputs.
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "=" not in line and ":" not in line:
            continue

        def _replace_line(match: re.Match[str]) -> str:
            name = match.group(1)
            if _should_redact_name(name):
                return f"{name}=[REDACTED]"
            return match.group(0)

        lines[i] = _SECRET_NAME_VALUE_RE.sub(_replace_line, line)

    text = "\n".join(lines)
    return text
