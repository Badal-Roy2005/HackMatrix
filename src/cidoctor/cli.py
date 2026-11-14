"""Command-line interface for CI Doctor.

Block 4 adds the ``init``, ``analyze``, ``flaky`` and ``demo seed`` commands.
Block 1 provided the Typer app with ``--version`` and ``--verbose`` only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cidoctor import __version__
from cidoctor.core.analysis import analyze as _run_analysis
from cidoctor.core.config import Settings
from cidoctor.core.logging import setup_logging
from cidoctor.core.models import AnalysisReport, FlakyVerdict, TestResult
from cidoctor.providers import default_registry
from cidoctor.storage import JsonStore

app = typer.Typer(
    name="cidoctor",
    help="CI Doctor: investigates failing CI runs.",
    add_completion=False,
)
console = Console()


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


def _parse_duration(value: str) -> timedelta:
    """Parse a duration string like ``14d``, ``12h``, ``90m`` into a timedelta.

    Args:
        value: a string consisting of a number and a unit suffix
            (``d``, ``h``, ``m``, ``s``).

    Returns:
        A :class:`timedelta`.

    Raises:
        typer.BadParameter: if the value cannot be parsed.
    """
    value = value.strip()
    if len(value) < 2:
        raise typer.BadParameter(
            f"invalid duration {value!r}: use a number and unit (e.g. 14d, 12h)"
        )
    num_part = value[:-1]
    unit = value[-1].lower()
    try:
        num = float(num_part)
    except ValueError as exc:
        raise typer.BadParameter(
            f"invalid duration {value!r}: cannot parse number {num_part!r}"
        ) from exc
    multipliers = {
        "d": lambda n: timedelta(days=n),
        "h": lambda n: timedelta(hours=n),
        "m": lambda n: timedelta(minutes=n),
        "s": lambda n: timedelta(seconds=n),
    }
    if unit not in multipliers:
        raise typer.BadParameter(
            f"invalid duration unit {unit!r}: use d, h, m, or s"
        )
    return multipliers[unit](num)


@app.command()
def init(
    path: str = typer.Argument(
        "cidoctor.toml", help="Path to the config file to write."
    ),
) -> None:
    """Write a default cidoctor.toml and create the .cidoctor/ directory."""
    config_path = Path(path)
    if config_path.exists():
        console.print(f"[yellow]{config_path} already exists, skipping.[/yellow]")
    else:
        default_toml = """# CI Doctor configuration
[cidoctor]
store_path = ".cidoctor/runs"
provider = ""
model = ""
window_days = 14
min_runs = 10
threshold = 0.95
main_branch = "main"
max_log_chars = 400000
ollama_host = "http://localhost:11434"
"""
        config_path.write_text(default_toml)
        console.print(f"[green]Wrote {config_path}[/green]")

    store_dir = Path(".cidoctor/runs")
    store_dir.mkdir(parents=True, exist_ok=True)
    console.print(
        "[green]Created .cidoctor/runs[/green] — consider adding .cidoctor/ to .gitignore"
    )


@app.command()
def analyze(
    results: str = typer.Argument(..., help="Path to the JUnit XML results file."),
    log: str | None = typer.Option(
        None, "--log", help="Path to a log file (accepted for interface parity)."
    ),
    commit: str | None = typer.Option(
        None, "--commit", help="Commit SHA (auto-detected with git if omitted)."
    ),
    branch: str | None = typer.Option(
        None, "--branch", help="Branch name (auto-detected with git if omitted)."
    ),
    format: str = typer.Option(
        "table", "--format", help="Output format: table or json."
    ),
) -> None:
    """Analyze a JUnit results file and report failures and flaky tests."""
    settings = Settings()
    store = JsonStore(settings.store_path)

    try:
        report = _run_analysis(
            results_path=results,
            log_path=log,
            commit=commit,
            branch=branch,
            store=store,
            settings=settings,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001 - clean CLI error message
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    if format == "json":
        console.print(report.model_dump_json(indent=2))
    else:
        _print_analysis_table(report)

    # Exit code 1 if any test failed or errored.
    if any(
        r.status in {"failed", "error"} for r in report.run.results
    ):
        raise typer.Exit(code=1)


@app.command()
def flaky(
    since: str = typer.Option(
        "14d", "--since", help="Look back this long (e.g. 14d, 12h)."
    ),
    min_runs: int | None = typer.Option(
        None, "--min-runs", help="Minimum runs for a conclusive verdict."
    ),
    format: str = typer.Option(
        "table", "--format", help="Output format: table or json."
    ),
) -> None:
    """Show flaky-test verdicts ranked by confidence."""
    settings = Settings()
    if min_runs is not None:
        settings = settings.with_overrides(min_runs=min_runs)

    store = JsonStore(settings.store_path)
    delta = _parse_duration(since)
    since_dt = datetime.now(UTC) - delta

    from cidoctor.sdk import Analyzer

    analyzer = Analyzer(store=store, settings=settings)
    verdicts = analyzer.flaky(since=since_dt)

    if format == "json":
        console.print(
            json.dumps(
                [v.model_dump() for v in verdicts], indent=2, default=str
            )
        )
    else:
        _print_flaky_table(verdicts)


demo = typer.Typer(
    name="demo",
    help="Generate demo data.",
)


@demo.command(name="seed")
def demo_seed() -> None:
    """Generate deterministic simulated demo data."""
    from cidoctor.core.models import Run, TestResult

    console.print(
        "[bold yellow]NOTICE: The demo seed data is simulated. "
        "It does not represent real CI results.[/bold yellow]"
    )

    settings = Settings()
    store = JsonStore(settings.store_path)

    base = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)

    for i in range(30):
        created = base + timedelta(hours=i)
        results: list[TestResult] = []
        for j in range(60):
            name = f"test_{j:02d}"
            if j == 0:
                # consistently failing test
                status = "failed"
            elif j in (1, 2):
                # two flaky tests: alternate pass/fail
                status = "failed" if i % 2 == 0 else "passed"
            else:
                status = "passed"
            results.append(
                TestResult(
                    name=name,
                    classname="tests.demo",
                    status=status,
                    duration_s=0.5,
                )
            )
        run = Run(
            id=f"demo-{i:02d}",
            commit=f"abc{i:04d}",
            branch="main",
            created_at=created,
            results=results,
        )
        store.save_run(run)

    console.print(
        "[green]Seeded 30 simulated runs (~60 tests each): "
        "test_00 consistently failing, test_01 and test_02 flaky.[/green]"
    )


app.add_typer(demo, name="demo")


def _print_analysis_table(report: AnalysisReport) -> None:
    run = report.run

    console.print(f"\n[bold]Run {run.id}[/bold]")
    console.print(f"  commit: {run.commit}  branch: {run.branch}")
    console.print(f"  results: {len(run.results)} tests")

    failed = [
        r for r in run.results if r.status in {"failed", "error"}
    ]
    passed = [r for r in run.results if r.status == "passed"]
    skipped = [r for r in run.results if r.status == "skipped"]

    table = Table(title="Test outcome summary")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("passed", str(len(passed)))
    table.add_row("failed", str(len(failed)), style="red")
    table.add_row("error", str(len([r for r in run.results if r.status == "error"])), style="red")
    table.add_row("skipped", str(len(skipped)))
    console.print(table)

    if report.failure_groups:
        gt = Table(title="Failure groups (by fingerprint)")
        gt.add_column("Fingerprint", style="bold")
        gt.add_column("Count", justify="right")
        gt.add_column("Sample message")
        gt.add_column("Tests")
        for g in report.failure_groups[:10]:
            gt.add_row(
                g.fingerprint,
                str(g.count),
                g.sample_message[:80],
                ", ".join(g.tests[:5]),
            )
        console.print(gt)


def _print_flaky_table(verdicts: list[FlakyVerdict]) -> None:
    table = Table(title="Flaky-test verdicts")
    table.add_column("Test", style="bold")
    table.add_column("Runs", justify="right")
    table.add_column("Pass rate", justify="right")
    table.add_column("Verdict", style="bold")
    table.add_column("Confidence", justify="right")
    table.add_column("Reason")
    for v in verdicts:
        table.add_row(
            v.test,
            str(v.runs),
            f"{v.pass_rate:.2f}",
            v.verdict,
            f"{v.confidence:.2f}",
            v.reason,
        )
    console.print(table)


@app.command()
def explain(
    run_id: str = typer.Argument(
        "latest", help="Run id or 'latest'."
    ),
    provider: str | None = typer.Option(
        None, "--provider", help="Provider name (e.g. gemini)."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Model name (provider-specific)."
    ),
    agent: bool = typer.Option(
        True, "--agent/--no-agent", help="Use the investigation agent (default on)."
    ),
    format: str = typer.Option(
        "table", "--format", help="Output format: table or json."
    ),
) -> None:
    """Explain the root cause of a run's failures."""
    from cidoctor.core.errors import ProviderError
    from cidoctor.core.fingerprint import fingerprint, group_failures
    from cidoctor.core.flaky import evaluate_test

    settings = Settings()
    store = JsonStore(settings.store_path)

    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]Run {run_id!r} not found.[/red]")
        raise typer.Exit(code=1)

    # Fingerprint failures and group them.
    fingerprinted: list[Any] = []
    for r in run.results:
        if r.status in {"failed", "error"}:
            fingerprinted.append(
                r.model_copy(
                    update={"fingerprint": fingerprint(r.message, r.trace)}
                )
            )
        else:
            fingerprinted.append(r)

    failure_groups = group_failures(fingerprinted)

    if not failure_groups:
        console.print("[green]No failures in this run.[/green]")
        raise typer.Exit(code=0)

    # Compute flaky verdicts for tests in this run.
    test_names = {r.name for r in run.results}
    flaky_verdicts = []
    for name in sorted(test_names):
        history = store.test_history(name, since=None)
        verdict = evaluate_test(
            history,
            threshold=settings.threshold,
            min_runs=settings.min_runs,
            main_branch=settings.main_branch,
            test=name,
        )
        flaky_verdicts.append(verdict)

    # Build Evidence.
    from cidoctor.providers.base import Evidence

    evidence = Evidence(
        failure_groups=[g.model_dump() for g in failure_groups],
        flaky_verdicts=[v.model_dump() for v in flaky_verdicts],
        similar_failures=[],
        test_source=f"{run.commit}@{run.branch}",
    )

    # Resolve provider.
    registry = default_registry()
    provider_name = provider or settings.provider or "gemini"
    try:
        provider_instance = registry.get_provider(provider_name)
    except ProviderError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    if model is not None and hasattr(provider_instance, "_model"):
        provider_instance._model = model

    if agent:
        # Agent path: investigation loop with tool calls.
        from cidoctor.agent.loop import InvestigationContext, investigate
        from cidoctor.providers.gemini import GeminiAgentModel

        agent_model = GeminiAgentModel(model=model, client=None)
        context = InvestigationContext(
            store=store,
            repo_root=str(Path.cwd()),
            run_id=run.id,
            provider=provider_instance,
        )

        def on_event(event: str, message: str) -> None:
            if event == "calling":
                console.print(f"[dim]investigating: {message}[/dim]")

        try:
            result = investigate(
                model=agent_model,
                context=context,
                on_event=on_event,
            )
        except ProviderError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(code=1)

        diagnosis = result.diagnosis
        if result.mode == "fallback":
            console.print("[yellow](agent fell back to single-request diagnosis)[/yellow]")
    else:
        # Single-request path.
        try:
            diagnosis = provider_instance.diagnose(evidence)
        except ProviderError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(code=1)

    if format == "json":
        console.print(diagnosis.model_dump_json(indent=2))
    else:
        panel = Panel(
            f"[bold]Root cause:[/bold] {diagnosis.root_cause}\n"
            f"[bold]Category:[/bold] {diagnosis.category}\n"
            f"[bold]Suggested fix:[/bold] {diagnosis.suggested_fix}\n"
            f"[bold]Confidence:[/bold] {diagnosis.confidence:.2f}",
            title="Diagnosis",
            border_style="blue",
        )
        console.print(panel)

