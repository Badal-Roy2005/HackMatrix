"""Command-line interface for CI Doctor.

Block 1 provides the Typer app with ``--version`` and ``--help`` only.
Subcommands are added in later blocks.
"""

from __future__ import annotations

import typer

from cidoctor import __version__

from .core.logging import setup_logging

app = typer.Typer(
    name="cidoctor",
    help="CI Doctor: investigates failing CI runs.",
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cidoctor {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show the version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable verbose logging.",
    ),
) -> None:
    """CI Doctor entry point."""
    setup_logging(verbose=verbose)
