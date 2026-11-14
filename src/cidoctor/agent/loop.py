"""Investigation agent loop for CI Doctor.

Runs a tool-calling agent that gathers its own evidence with safe tools,
producing a :class:`Diagnosis` plus a trace of :class:`ToolCallRecord` items.

A deterministic fallback (single-request Block 5 path) is used when the model
fails, produces malformed output, or returns invalid final JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cidoctor.core.errors import ProviderError
from cidoctor.core.fingerprint import fingerprint, group_failures
from cidoctor.core.flaky import evaluate_test
from cidoctor.core.models import Diagnosis, FlakyVerdict, Run
from cidoctor.providers.base import Evidence, Provider
from cidoctor.storage.base import Store

from .tools import TOOL_DECLARATIONS, ToolCall, ToolCallRecord, ToolContext, call_tool, tool_output


@dataclass
class Final:
    """The model's final answer text."""

    text: str


class AgentModel(Protocol):
    """Protocol for an agent model.

    ``step`` receives the conversation history and the tool declarations, and
    returns either a :class:`ToolCall` or a :class:`Final`.
    """

    name: str

    def step(
        self, history: list[dict[str, Any]], tool_declarations: list[dict[str, Any]]
    ) -> ToolCall | Final:
        ...


@dataclass
class InvestigationContext:
    """Context for an investigation."""

    store: Store
    repo_root: str
    run_id: str
    provider: Provider | None = None


@dataclass
class InvestigationResult:
    """Result of an investigation."""

    diagnosis: Diagnosis
    trace: list[ToolCallRecord] = field(default_factory=list)
    mode: str = "agent"  # "agent" or "fallback"


def _evidence_from_run(
    run: Run, store: Store, provider_name: str = "unknown"
) -> Evidence:
    """Build :class:`Evidence` from a stored run."""
    groups = group_failures(run.results)

    # Flaky verdicts for tests in this run.
    flaky_verdicts: list[FlakyVerdict] = []
    test_names = {r.name for r in run.results}
    for name in sorted(test_names):
        history = store.test_history(name, since=None)
        verdict = evaluate_test(
            history,
            threshold=0.95,
            min_runs=10,
            main_branch="main",
            test=name,
        )
        flaky_verdicts.append(verdict)

    # Fingerprint failures for similar-failures lookup.
    similar: list[str] = []
    seen: set[str] = set()
    for result in run.results:
        if result.status in {"passed", "skipped"}:
            continue
        fp = fingerprint(result.message, result.trace)
        past = store.past_failures_for_fingerprint(fp, limit=5)
        for name in past:
            if name not in seen:
                seen.add(name)
                similar.append(name)

    return Evidence(
        failure_groups=[g.model_dump() for g in groups],
        flaky_verdicts=[v.model_dump() for v in flaky_verdicts],
        similar_failures=[{"test": name} for name in similar],
        test_source=f"{run.commit}@{run.branch}",
    )


def _parse_final_json(text: str) -> Diagnosis | None:
    """Try to parse a :class:`Diagnosis` from the model's final text."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Diagnosis.model_validate(data)
    except Exception:  # noqa: BLE001 - invalid final JSON
        return None


def _fallback_diagnosis(evidence: Evidence, provider: Provider) -> Diagnosis:
    """Single-request Block 5 diagnosis path."""
    return provider.diagnose(evidence)


def investigate(
    model: AgentModel,
    context: InvestigationContext,
    max_tool_calls: int = 6,
    on_event: Callable[[str, str], None] | None = None,
) -> InvestigationResult:
    """Run the tool-calling investigation loop.

    Args:
        model: the agent model (must follow :class:`AgentModel`).
        context: investigation context (store, repo_root, run_id, provider).
        max_tool_calls: maximum number of tool calls before forcing a final.
        on_event: optional callback ``(event, message)`` for progress.

    Returns:
        An :class:`InvestigationResult` with the diagnosis and trace.
    """
    store = context.store
    run = store.get_run(context.run_id)
    if run is None:
        # No run to investigate; fall back to an empty diagnosis.
        diagnosis = Diagnosis(
            root_cause=f"run {context.run_id} not found",
            category="unknown",
            suggested_fix="check the run id",
            confidence=0.0,
            sources=[],
        )
        return InvestigationResult(diagnosis=diagnosis, mode="fallback")

    evidence = _evidence_from_run(run, store)

    tool_ctx = ToolContext(store=store, repo_root=Path(context.repo_root), run_id=context.run_id)

    history: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a CI failure investigation agent. Use the available "
                "tools to gather evidence, then return a final answer as JSON "
                "matching the Diagnosis schema. You may call at most "
                f"{max_tool_calls} tools."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Investigate run {context.run_id} ({run.commit}@{run.branch}). "
                "Available evidence summary is provided; use tools to dig deeper "
                "as needed."
            ),
        },
    ]

    trace: list[ToolCallRecord] = []
    tool_calls_made = 0

    try:
        for _ in range(max_tool_calls + 1):
            step = model.step(history, TOOL_DECLARATIONS)

            if isinstance(step, Final):
                parsed = _parse_final_json(step.text)
                if parsed is not None:
                    return InvestigationResult(
                        diagnosis=parsed, trace=trace, mode="agent"
                    )
                # Invalid final JSON -> fallback.
                break

            if isinstance(step, ToolCall):
                if step.name not in {t["name"] for t in TOOL_DECLARATIONS}:
                    if on_event:
                        on_event("rejected", step.name)
                    # Record rejection and continue.
                    trace.append(
                        ToolCallRecord(
                            name=step.name,
                            args=step.args,
                            output=tool_output(f"error: unknown tool {step.name!r}"),
                        )
                    )
                    history.append(
                        {
                            "role": "tool",
                            "content": tool_output(
                                f"error: unknown tool {step.name!r}"
                            ),
                        }
                    )
                    continue

                tool_calls_made += 1
                if on_event:
                    on_event("calling", step.name)

                output = call_tool(tool_ctx, step.name, step.args)
                trace.append(
                    ToolCallRecord(name=step.name, args=step.args, output=output)
                )
                history.append({"role": "tool", "content": output})

                if tool_calls_made >= max_tool_calls:
                    # Cap reached: force a final answer.
                    break

        # Fallback: single-request Block 5 path.
        provider = context.provider
        if provider is None:
            # No provider available; build a best-effort diagnosis from evidence.
            diagnosis = Diagnosis(
                root_cause="no provider available",
                category="unknown",
                suggested_fix="configure a provider",
                confidence=0.0,
                sources=[],
            )
        else:
            try:
                diagnosis = _fallback_diagnosis(evidence, provider)
            except ProviderError as exc2:
                diagnosis = Diagnosis(
                    root_cause=str(exc2),
                    category="unknown",
                    suggested_fix="provider failed",
                    confidence=0.0,
                    sources=[],
                )

        return InvestigationResult(
            diagnosis=diagnosis, trace=trace, mode="fallback"
        )

    except ProviderError as exc:
        # Provider error -> fallback with a best-effort diagnosis.
        provider = context.provider
        if provider is None:
            diagnosis = Diagnosis(
                root_cause=str(exc),
                category="unknown",
                suggested_fix="configure a provider",
                confidence=0.0,
                sources=[],
            )
        else:
            try:
                diagnosis = _fallback_diagnosis(evidence, provider)
            except ProviderError as exc2:
                diagnosis = Diagnosis(
                    root_cause=str(exc2),
                    category="unknown",
                    suggested_fix="provider failed",
                    confidence=0.0,
                    sources=[],
                )

        return InvestigationResult(
            diagnosis=diagnosis, trace=trace, mode="fallback"
        )


__all__ = [
    "AgentModel",
    "Final",
    "InvestigationContext",
    "InvestigationResult",
    "ToolCall",
    "ToolCallRecord",
    "investigate",
    "tool_output",
]
