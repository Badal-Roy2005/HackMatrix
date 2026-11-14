"""Performance test: analyzing 10,000 results completes in under 5 seconds."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

from cidoctor.core.analysis import analyze as _run_analysis
from cidoctor.core.config import Settings
from cidoctor.storage import JsonStore


@pytest.mark.performance
def test_analyze_10000_results_under_5s(tmp_path) -> None:
    settings = Settings().with_overrides(
        store_path=str(tmp_path / ".cidoctor/runs")
    )

    # Build a JUnit XML with 10,000 test cases.
    root = ET.Element("testsuite", name="perf", tests="10000", failures="5000")
    for i in range(10_000):
        status = "failed" if i % 2 == 0 else "passed"
        tc = ET.SubElement(
            root,
            "testcase",
            name=f"test_{i:05d}",
            classname="mod",
            time="0.1",
        )
        if status == "failed":
            failure = ET.SubElement(tc, "failure")
            failure.set("message", "assertion failed")
            failure.set("type", "AssertionError")
            failure.text = "AssertionError: assertion failed"

    results_file = tmp_path / "results.xml"
    ET.ElementTree(root).write(results_file)

    store = JsonStore(settings.store_path)
    start = time.monotonic()
    report = _run_analysis(
        results_path=str(results_file),
        commit="abc",
        branch="main",
        store=store,
        settings=settings,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"analyze took {elapsed:.2f}s"
    assert report is not None
