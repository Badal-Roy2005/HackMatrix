"""Tests for logging (Block 1)."""

from __future__ import annotations

import logging

from cidoctor.core.logging import SecretMaskFilter, setup_logging


def test_secret_mask_filter_redacts_api_key() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="connecting with api_key=sk-1234567890abcdef",
        args=(),
        exc_info=None,
    )
    SecretMaskFilter().filter(record)
    assert "[REDACTED]" in record.getMessage()
    assert "sk-1234567890abcdef" not in record.getMessage()


def test_secret_mask_filter_redacts_bearer_token() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Authorization: Bearer ghp_abcdef1234567890",
        args=(),
        exc_info=None,
    )
    SecretMaskFilter().filter(record)
    assert "[REDACTED]" in record.getMessage()
    assert "ghp_abcdef1234567890" not in record.getMessage()


def test_secret_mask_filter_redacts_password_env() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="DB_PASSWORD=hunter2",
        args=(),
        exc_info=None,
    )
    SecretMaskFilter().filter(record)
    assert "[REDACTED]" in record.getMessage()
    assert "hunter2" not in record.getMessage()


def test_setup_logging_sets_level() -> None:
    setup_logging(verbose=True)
    assert logging.getLogger().level == logging.DEBUG
    setup_logging(verbose=False)
    assert logging.getLogger().level == logging.INFO
