"""Custom error types for CI Doctor.

All errors raised by CI Doctor should subclass ``CIDoctorError`` so callers
can catch the entire family with a single ``except``.
"""


class CIDoctorError(Exception):
    """Base exception for all CI Doctor errors."""


class ParseError(CIDoctorError):
    """Raised when a results file cannot be parsed."""


class StorageError(CIDoctorError):
    """Raised when the run-history store cannot be read or written."""


class ProviderError(CIDoctorError):
    """Raised when an AI provider fails or is misconfigured."""


class ConfigError(CIDoctorError):
    """Raised when configuration is missing or invalid."""
