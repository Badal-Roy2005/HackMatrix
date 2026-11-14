"""Tests for Block 6: investigation agent, tools, and Gemini agent model."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cidoctor.agent.loop import Final, InvestigationContext, ToolCall, investigate
from cidoctor.agent.tools import (
    ToolContext,
    call_tool,
    get_failure_groups,
    get_git_diff,
    get_test_history,
    read_file,
    tool_output,
)
from cidoctor.core.errors import ProviderError
from cidoctor.core.fingerprint import group_failures
from cidoctor.core.flaky import evaluate_test
from cidoctor.core.models import (
    Diagnosis,
    FlakyVerdict,
    Run,
    TestResult,
)
from cidoctor.providers.base import Evidence
from cidoctor.providers.gemini import GeminiAgentModel, GeminiProvider
from cidoctor.storage import JsonStore

# --- ToolContext fixtures ---


@pytest.fixture
def store_tmp(tmp_path: Path) -> JsonStore:
    return JsonStore(tmp_path / "runs")


@pytest.fixture
def sample_run() -> Run:
    return Run(
        id="run-1",
        commit="abc123",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[
            TestResult(
                name="test_login",
                classname="mod",
                status="failed",
                duration_s=1.0,
                message="assert x == y",
                trace="AssertionError: assert x == y\n  File mod.py:5",
            ),
            TestResult(
                name="test_logout",
                classname="mod",
                status="passed",
                duration_s=0.5,
            ),
        ],
    )


def _make_ctx(
    store: JsonStore, repo_root: Path | None = None, run_id: str = "run-1"
) -> ToolContext:
    return ToolContext(
        store=store,
        repo_root=repo_root or Path("/tmp/repo"),
        run_id=run_id,
    )


# --- get_failure_groups ---


def test_get_failure_groups(store_tmp: JsonStore, sample_run: Run) -> None:
    store_tmp.save_run(sample_run)
    ctx = _make_ctx(store_tmp)
    output = get_failure_groups(ctx, "run-1")
    assert "test_login" in output
    assert "AssertionError" in output or "assert x == y" in output


def test_get_failure_groups_unknown_run(store_tmp: JsonStore) -> None:
    ctx = _make_ctx(store_tmp)
    output = get_failure_groups(ctx, "nope")
    assert "not found" in output


# --- get_test_history ---


def test_get_test_history(store_tmp: JsonStore, sample_run: Run) -> None:
    store_tmp.save_run(sample_run)
    ctx = _make_ctx(store_tmp)
    output = get_test_history(ctx, "test_login", days=14)
    assert "test_login" in output or "run-1" in output


# --- get_git_diff validation ---


def test_get_git_diff_head() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")))
    # Should not crash even if no git repo; just returns error/capped output.
    output = get_git_diff(ctx, "HEAD")
    assert isinstance(output, str)


def test_get_git_diff_head_n() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")))
    output = get_git_diff(ctx, "HEAD~1")
    assert isinstance(output, str)


def test_get_git_diff_hex_sha() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")))
    output = get_git_diff(ctx, "abc123def456")
    assert isinstance(output, str)


def test_get_git_diff_rejects_hostile() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")))
    for bad in ["--output=/tmp/x", "; rm -rf /", "HEAD; rm", "..", ""]:
        output = get_git_diff(ctx, bad)
        assert "invalid commit spec" in output or "invalid" in output.lower()


# --- read_file safety ---


def test_read_file_traversal_blocked() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")), repo_root=Path("/tmp/repo"))
    output = read_file(ctx, "../../etc/passwd")
    assert "escapes" in output.lower() or "denied" in output.lower()


def test_read_file_dotenv_denied() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")), repo_root=Path("/tmp/repo"))
    output = read_file(ctx, ".env")
    assert "denied" in output.lower()


def test_read_file_pem_denied() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")), repo_root=Path("/tmp/repo"))
    output = read_file(ctx, "cert.pem")
    assert "denied" in output.lower()


def test_read_file_id_rsa_denied() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")), repo_root=Path("/tmp/repo"))
    output = read_file(ctx, "id_rsa")
    assert "denied" in output.lower()


def test_read_file_git_dir_denied() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")), repo_root=Path("/tmp/repo"))
    output = read_file(ctx, ".git/config")
    assert "denied" in output.lower()


# --- tool_output capping and redaction ---


def test_tool_output_caps() -> None:
    long_text = "x" * 10000
    output = tool_output(long_text, cap=100)
    assert len(output) <= 100


def test_tool_output_redacts_secrets() -> None:
    output = tool_output("API_KEY=sk-1234567890secret")
    assert "sk-1234567890secret" not in output
    assert "[REDACTED]" in output


# --- call_tool unknown tool ---


def test_call_tool_unknown() -> None:
    ctx = _make_ctx(JsonStore(Path("/tmp/runs")))
    output = call_tool(ctx, "nonexistent_tool", {})
    assert "unknown tool" in output


# --- AgentModel protocol ---


class _ScriptedModel:
    """A scripted AgentModel that returns predetermined steps."""

    def __init__(self, steps: list[ToolCall | Final]) -> None:
        self._steps = list(steps)
        self.calls = 0

    @property
    def name(self) -> str:
        return "scripted"

    def step(
        self, history: list[dict[str, Any]], tool_declarations: list[dict[str, Any]]
    ) -> ToolCall | Final:
        step = self._steps[self.calls]
        self.calls += 1
        return step


class _MockProvider:
    name = "mock"

    def __init__(self, diagnosis: Diagnosis) -> None:
        self._diagnosis = diagnosis

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        return self._diagnosis


def test_investigate_tools_then_final() -> None:
    store = JsonStore(Path("/tmp/runs"))
    run = Run(
        id="run-1",
        commit="abc",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[
            TestResult(
                name="test_login",
                classname="mod",
                status="failed",
                duration_s=1.0,
                message="boom",
                trace="Error: boom",
            )
        ],
    )
    store.save_run(run)

    # Script: call get_failure_groups, then final answer.
    model = _ScriptedModel(
        steps=[
            ToolCall(name="get_failure_groups", args={"run_id": "run-1"}),
            Final(
                text='{"root_cause": "test bug", "category": "test_bug", '
                '"suggested_fix": "fix test", "confidence": 0.9, "sources": []}'
            ),
        ]
    )

    ctx = InvestigationContext(
        store=store,
        repo_root=str(Path("/tmp/repo")),
        run_id="run-1",
        provider=_MockProvider(
            Diagnosis(
                root_cause="unused",
                category="unknown",
                suggested_fix="unused",
                confidence=0.0,
                sources=[],
            )
        ),
    )

    events: list[tuple[str, str]] = []
    result = investigate(model=model, context=ctx, on_event=lambda e, m: events.append((e, m)))

    assert result.mode == "agent"
    assert result.diagnosis.root_cause == "test bug"
    assert len(result.trace) == 1
    assert result.trace[0].name == "get_failure_groups"
    assert any(e[0] == "calling" for e in events)


def test_investigate_cap_enforced() -> None:
    store = JsonStore(Path("/tmp/runs"))
    run = Run(
        id="run-1",
        commit="abc",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[],
    )
    store.save_run(run)

    # Script: always request a tool call, never final.
    model = _ScriptedModel(
        steps=[
            ToolCall(name="get_failure_groups", args={"run_id": "run-1"})
            for _ in range(10)
        ]
    )

    ctx = InvestigationContext(
        store=store,
        repo_root=str(Path("/tmp/repo")),
        run_id="run-1",
        provider=_MockProvider(
            Diagnosis(
                root_cause="fallback",
                category="unknown",
                suggested_fix="fix",
                confidence=0.5,
                sources=[],
            )
        ),
    )

    result = investigate(model=model, context=ctx, max_tool_calls=2)
    assert result.mode == "fallback"
    assert len(result.trace) == 2  # cap enforced


def test_investigate_unknown_tool_rejected() -> None:
    store = JsonStore(Path("/tmp/runs"))
    run = Run(
        id="run-1",
        commit="abc",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[],
    )
    store.save_run(run)

    model = _ScriptedModel(
        steps=[
            ToolCall(name="nonexistent_tool", args={}),
            Final(
                text='{"root_cause": "x", "category": "unknown", '
                '"suggested_fix": "y", "confidence": 0.5, "sources": []}'
            ),
        ]
    )

    ctx = InvestigationContext(
        store=store,
        repo_root=str(Path("/tmp/repo")),
        run_id="run-1",
        provider=GeminiProvider(model="m", client=None),
    )

    result = investigate(model=model, context=ctx)
    assert result.mode == "agent"
    assert result.diagnosis.root_cause == "x"


def test_investigate_fallback_on_bad_json() -> None:
    store = JsonStore(Path("/tmp/runs"))
    run = Run(
        id="run-1",
        commit="abc",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[
            TestResult(
                name="test_login",
                classname="mod",
                status="failed",
                duration_s=1.0,
                message="boom",
                trace="Error: boom",
            )
        ],
    )
    store.save_run(run)

    model = _ScriptedModel(
        steps=[
            Final(text="not valid json at all")
        ]
    )

    ctx = InvestigationContext(
        store=store,
        repo_root=str(Path("/tmp/repo")),
        run_id="run-1",
        provider=_MockProvider(
            Diagnosis(
                root_cause="fallback",
                category="unknown",
                suggested_fix="fix",
                confidence=0.5,
                sources=[],
            )
        ),
    )

    result = investigate(model=model, context=ctx)
    assert result.mode == "fallback"


def test_investigate_fallback_on_provider_error() -> None:
    store = JsonStore(Path("/tmp/runs"))
    run = Run(
        id="run-1",
        commit="abc",
        branch="main",
        created_at=datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC),
        results=[],
    )
    store.save_run(run)

    # Provider that always raises.
    class _BadProvider:
        name = "bad"

        def diagnose(self, evidence: Evidence) -> Diagnosis:
            raise ProviderError("boom")

    model = _ScriptedModel(
        steps=[
            Final(text="not valid json at all")
        ]
    )

    ctx = InvestigationContext(
        store=store,
        repo_root=str(Path("/tmp/repo")),
        run_id="run-1",
        provider=_BadProvider(),
    )

    result = investigate(model=model, context=ctx)
    assert result.mode == "fallback"
    assert result.diagnosis.root_cause == "boom"


# --- GeminiAgentModel ---


class _FakeGenerateContentResponse:
    def __init__(self, text: str | None = None, function_call: Any = None) -> None:
        self.text = text
        self.candidates = []
        if function_call is not None:
            from google.genai import types

            self.candidates = [
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(function_call=function_call)],
                    )
                )
            ]


def test_gemini_agent_model_tool_call() -> None:
    from google.genai import types

    fc = types.FunctionCall(
        name="get_failure_groups",
        args={"run_id": "run-1"},
    )
    response = _FakeGenerateContentResponse(function_call=fc)

    class _FakeModels:
        def generate_content(self, **kwargs: Any) -> Any:
            return response

    client = MagicMock()
    client.models = _FakeModels()

    model = GeminiAgentModel(model="m", client=client)
    result = model.step(
        history=[{"role": "user", "content": "investigate"}],
        tool_declarations=[
            {
                "name": "get_failure_groups",
                "description": "x",
                "parameters": {},
            }
        ],
    )

    assert isinstance(result, ToolCall)
    assert result.name == "get_failure_groups"
    assert result.args == {"run_id": "run-1"}


def test_gemini_agent_model_final() -> None:
    response = _FakeGenerateContentResponse(
        text='{"root_cause": "final answer", "category": "test_bug", '
        '"suggested_fix": "fix", "confidence": 0.8, "sources": []}'
    )

    class _FakeModels:
        def generate_content(self, **kwargs: Any) -> Any:
            return response

    client = MagicMock()
    client.models = _FakeModels()

    model = GeminiAgentModel(model="m", client=client)
    result = model.step(
        history=[{"role": "user", "content": "investigate"}],
        tool_declarations=[],
    )

    assert isinstance(result, Final)
    assert "final answer" in result.text


# --- Diagnosis validation ---


def test_diagnosis_validation() -> None:
    d = Diagnosis(
        root_cause="test bug",
        category="test_bug",
        suggested_fix="fix the test",
        confidence=0.9,
        sources=["logs"],
    )
    assert d.category == "test_bug"
    assert d.confidence == 0.9


def test_flaky_verdict_validation() -> None:
    v = FlakyVerdict(
        test="test_login",
        runs=10,
        pass_rate=0.5,
        verdict="flaky",
        confidence=0.8,
        reason="mixed outcomes",
    )
    assert v.verdict == "flaky"


def test_evaluate_test_flaky() -> None:
    history = [
        {"commit": "c1", "branch": "main", "created_at": "t1", "status": "passed"},
        {"commit": "c1", "branch": "main", "created_at": "t2", "status": "failed"},
    ] + [
        {"commit": f"c{i}", "branch": "main", "created_at": f"t{i}", "status": "passed"}
        for i in range(2, 15)
    ]
    verdict = evaluate_test(
        history,
        threshold=0.95,
        min_runs=10,
        main_branch="main",
        test="test_login",
    )
    assert verdict.verdict == "flaky"
    assert verdict.test == "test_login"


def test_group_failures_count() -> None:
    results = [
        TestResult(
            name="a", classname="c", status="failed", duration_s=1.0,
            message="m1", trace="t1",
        ),
        TestResult(
            name="b", classname="c", status="failed", duration_s=1.0,
            message="m1", trace="t1",
        ),
        TestResult(
            name="c", classname="c", status="failed", duration_s=1.0,
            message="m2", trace="t2",
        ),
    ]
    groups = group_failures(results)
    assert len(groups) == 2
    assert groups[0].count == 2
