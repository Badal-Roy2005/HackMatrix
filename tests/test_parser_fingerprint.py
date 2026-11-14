"""Tests for the JUnit parser and fingerprinting (Block 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cidoctor.core.errors import ParseError
from cidoctor.core.fingerprint import (
    find_similar,
    fingerprint,
    group_failures,
    normalize,
)
from cidoctor.core.models import TestResult
from cidoctor.parsers import JUnitParser, default_registry

FIXTURES = Path(__file__).parent / "fixtures"


def test_pytest_style_fixture() -> None:
    parser = JUnitParser()
    results = parser.parse(FIXTURES / "pytest_style.xml")
    statuses = {r.status for r in results}
    assert statuses == {"passed", "failed", "error", "skipped"}
    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 2
    errors = [r for r in results if r.status == "error"]
    assert len(errors) == 1
    skipped = [r for r in results if r.status == "skipped"]
    assert len(skipped) == 1
    # message and trace populated from failure element
    assert failed[0].message == "assert expected == actual"
    assert "AssertionError" in failed[0].trace


def test_jest_junit_style_fixture() -> None:
    parser = JUnitParser()
    results = parser.parse(FIXTURES / "jest_junit_style.xml")
    assert len(results) == 3
    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].message == "Expected element to be visible"


def test_multiple_suites_fixture() -> None:
    parser = JUnitParser()
    results = parser.parse(FIXTURES / "multiple_suites.xml")
    statuses = {r.status for r in results}
    assert statuses == {"passed", "failed", "error"}
    failed = [r for r in results if r.status == "failed"]
    errors = [r for r in results if r.status == "error"]
    assert len(failed) == 1
    assert len(errors) == 1


def test_failure_error_skipped_fixture() -> None:
    parser = JUnitParser()
    results = parser.parse(FIXTURES / "pytest_style.xml")
    by_name = {r.name: r for r in results}
    assert by_name["test_login_failure"].status == "failed"
    assert by_name["test_login_error"].status == "error"
    assert by_name["test_login_skipped"].status == "skipped"


def test_malformed_xml_raises() -> None:
    parser = JUnitParser()
    with pytest.raises(ParseError):
        parser.parse(FIXTURES / "malformed.xml")


def test_xxe_attack_never_resolves_entities() -> None:
    parser = JUnitParser()
    try:
        results = parser.parse(FIXTURES / "xxe_attack.xml")
    except ParseError:
        # defusedxml safely rejects the DOCTYPE; that is the safe outcome.
        return
    # If it parsed, the entity must appear literally, not resolved.
    joined = " ".join(r.message + r.trace for r in results)
    assert "root:" not in joined  # /etc/passwd content must not leak


def test_oversized_file_raises() -> None:
    parser = JUnitParser()
    with pytest.raises(ParseError):
        parser.parse(FIXTURES / "oversized.xml")


def test_oversized_file_configurable_limit() -> None:
    parser = JUnitParser(max_file_size=100)
    # Even a small fixture exceeds a 100-byte limit.
    with pytest.raises(ParseError):
        parser.parse(FIXTURES / "pytest_style.xml")


def test_missing_file_raises() -> None:
    parser = JUnitParser()
    with pytest.raises(ParseError):
        parser.parse(FIXTURES / "does_not_exist.xml")


def test_directory_path_raises() -> None:
    parser = JUnitParser()
    with pytest.raises(ParseError):
        parser.parse(FIXTURES)


def test_same_error_different_timestamps_uuid_paths_one_fingerprint() -> None:
    base_msg = "connection refused at 2026-11-14T12:00:00Z"
    base_trace = (
        "ConnectionError: connection refused\n"
        "  File \"/tmp/build/abc123/src/app.py\", line 42, in connect\n"
        "    sock.connect(('127.0.0.1', 54321))\n"
    )
    fp1 = fingerprint(base_msg, base_trace)

    other_msg = "connection refused at 2026-11-15T09:30:00Z"
    other_trace = (
        "ConnectionError: connection refused\n"
        "  File \"/home/user/project/lib/app.py\", line 99, in connect\n"
        "    sock.connect(('127.0.0.1', 9999))\n"
    )
    fp2 = fingerprint(other_msg, other_trace)

    assert fp1 == fp2
    assert len(fp1) == 12


def test_different_exception_types_different_fingerprints() -> None:
    fp_value = fingerprint("ValueError: bad value", "ValueError: bad value\n")
    fp_type = fingerprint("TypeError: bad type", "TypeError: bad type\n")
    assert fp_value != fp_type


def test_fingerprints_are_deterministic() -> None:
    msg = "assert expected == actual"
    trace = "AssertionError: assert expected == actual\n"
    assert fingerprint(msg, trace) == fingerprint(msg, trace)


def test_normalize_strips_paths_keeps_basename() -> None:
    norm = normalize(
        "boom",
        'File "/home/user/project/src/foo.py", line 42, in main\n',
    )
    assert "/home/user/project/src" not in norm
    assert "foo.py" in norm


def test_normalize_strips_timestamps_and_uuids() -> None:
    norm = normalize(
        "failed at 2026-11-14T12:00:00Z req 550e8400-e29b-41d4-a716-446655440000",
        "",
    )
    assert "2026-11-14" not in norm
    assert "550e8400" not in norm


def test_group_failures_counts_and_ordering() -> None:
    results = [
        TestResult(name="a", classname="c", status="failed", duration_s=1.0, message="m1", trace="t1"),
        TestResult(name="b", classname="c", status="failed", duration_s=1.0, message="m1", trace="t1"),
        TestResult(name="c", classname="c", status="failed", duration_s=1.0, message="m2", trace="t2"),
        TestResult(name="d", classname="c", status="passed", duration_s=1.0),
    ]
    groups = group_failures(results)
    assert len(groups) == 2
    assert groups[0].count == 2
    assert groups[1].count == 1
    assert groups[0].tests == ["a", "b"]


def test_group_failures_skips_passed_and_skipped() -> None:
    results = [
        TestResult(name="a", classname="c", status="passed", duration_s=1.0),
        TestResult(name="b", classname="c", status="skipped", duration_s=1.0),
    ]
    assert group_failures(results) == []


def test_find_similar_returns_matches() -> None:
    matches = find_similar("connection refused", ["connection refused", "timeout error", "connection reset"])
    assert any(m[0] == "connection refused" and m[1] >= 90 for m in matches)


def test_find_similar_below_threshold() -> None:
    matches = find_similar("completely unrelated", ["connection refused", "timeout error"], threshold=90.0)
    assert matches == []


def test_registry_finds_junit_and_rejects_unknown() -> None:
    registry = default_registry()
    parser = registry.get_parser(FIXTURES / "pytest_style.xml")
    assert parser.name == "junit"

    with pytest.raises(ParseError):
        registry.get_parser(FIXTURES / "readme.txt")


def test_unknown_file_extension_rejected() -> None:
    registry = default_registry()
    with pytest.raises(ParseError):
        registry.get_parser(FIXTURES / "pytest_style.json")
