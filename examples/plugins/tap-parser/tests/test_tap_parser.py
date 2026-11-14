"""Test that the TAP parser entry point loads without core changes."""

from __future__ import annotations

from importlib.metadata import entry_points


def test_tap_parser_entry_point_loads() -> None:
    """The tap parser is discoverable via the cidoctor.parsers entry point."""
    eps = entry_points(group="cidoctor.parsers")
    tap_eps = [ep for ep in eps if ep.name == "tap"]
    assert tap_eps, "tap parser entry point not found"

    factory = tap_eps[0].load()
    parser = factory()
    assert parser.name == "tap"
    assert hasattr(parser, "can_parse")
    assert hasattr(parser, "parse")
