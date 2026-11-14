"""Tests for Block 7: Ollama provider, routing, and launch command."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from cidoctor.cli import app
from cidoctor.core.errors import ConfigError, ProviderError
from cidoctor.core.models import Diagnosis
from cidoctor.core.routing import ProviderSpec, resolve_model
from cidoctor.providers.ollama import OllamaAgentModel, OllamaProvider

runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()


# --- routing ---


def test_resolve_model_gemini() -> None:
    spec = resolve_model("gemini-1.5-flash", ["llama3"])
    assert spec == ProviderSpec(provider="gemini", model="gemini-1.5-flash")


def test_resolve_model_ollama_installed() -> None:
    spec = resolve_model("llama3", ["llama3", "codellama"])
    assert spec == ProviderSpec(provider="ollama", model="llama3")


def test_resolve_model_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="ollama pull"):
        resolve_model("unknown-model", ["llama3"])


def test_resolve_model_unknown_with_hint() -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_model("unknown-model", ["llama3", "codellama"])
    assert "llama3" in str(exc.value)
    assert "codellama" in str(exc.value)


def test_resolve_model_empty_installed() -> None:
    with pytest.raises(ConfigError):
        resolve_model("anything", [])


# --- OllamaProvider ---


class _MockTransport(httpx.MockTransport):
    """Wrapper that records requests."""


def _make_handler(
    tags_response: dict[str, Any] | None = None,
    chat_response: dict[str, Any] | None = None,
    connect_error: bool = False,
    status_code: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if connect_error:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/api/tags":
            return httpx.Response(
                status_code,
                json=tags_response or {"models": []},
                request=request,
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                status_code,
                json=chat_response or {},
                request=request,
            )
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def test_ollama_list_models() -> None:
    provider = OllamaProvider(host="http://localhost:11434", model="llama3")
    with patch(
        "httpx.get",
        side_effect=lambda url, **kw: httpx.Response(
            200, json={"models": [{"name": "llama3:7b"}, {"name": "codellama"}]}
        ),
    ):
        names = provider.list_models()
    assert "llama3:7b" in names
    assert "codellama" in names


def test_ollama_list_models_connection_refused() -> None:
    provider = OllamaProvider(host="http://localhost:11434", model="llama3")
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")), pytest.raises(
        ProviderError, match="ollama serve"
    ):
        provider.list_models()


def test_ollama_diagnose_success() -> None:
    import json

    diagnosis_json = {
        "root_cause": "test bug",
        "category": "test_bug",
        "suggested_fix": "fix the test",
        "confidence": 0.9,
        "sources": [],
    }
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": json.dumps(diagnosis_json)}
        }
        mock_post.return_value = mock_resp

        provider = OllamaProvider(model="llama3")
        result = provider.diagnose(
            __import__("cidoctor.providers.base", fromlist=["Evidence"]).Evidence()
        )
        assert isinstance(result, Diagnosis)
        assert result.root_cause == "test bug"

        # Verify num_ctx is sent
        call_args = mock_post.call_args
        assert call_args is not None
        json_body = call_args[1]["json"]
        assert json_body["options"]["num_ctx"] == 65536


def test_ollama_diagnose_model_not_installed() -> None:
    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        provider = OllamaProvider(model="nonexistent")
        with pytest.raises(ProviderError, match="ollama pull"):
            provider.diagnose(
                __import__("cidoctor.providers.base", fromlist=["Evidence"]).Evidence()
            )


def test_ollama_diagnose_retry_then_success() -> None:
    import json

    good_json = {
        "root_cause": "recovered",
        "category": "infra",
        "suggested_fix": "retry",
        "confidence": 0.8,
        "sources": [],
    }
    call_count = 0

    def fake_post(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if call_count == 1:
            mock_resp.json.return_value = {"message": {"content": "not json"}}
        else:
            mock_resp.json.return_value = {
                "message": {"content": json.dumps(good_json)}
            }
        return mock_resp

    with patch("httpx.post", side_effect=fake_post):
        provider = OllamaProvider(model="llama3")
        result = provider.diagnose(
            __import__("cidoctor.providers.base", fromlist=["Evidence"]).Evidence()
        )
        assert result.root_cause == "recovered"
        assert call_count == 2


# --- OllamaAgentModel ---


def test_ollama_agent_model_tool_call() -> None:
    from cidoctor.agent.loop import ToolCall

    tool_call = {
        "function": {"name": "get_failure_groups", "arguments": {"run_id": "run-1"}}
    }
    call_count = 0

    def fake_post(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"tool_calls": [tool_call], "content": ""}
        }
        return mock_resp

    with patch("httpx.post", side_effect=fake_post):
        model = OllamaAgentModel(model="llama3")
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


def test_ollama_agent_model_final() -> None:
    from cidoctor.agent.loop import Final

    call_count = 0

    def fake_post(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "message": {"content": "final answer text"}
        }
        return mock_resp

    with patch("httpx.post", side_effect=fake_post):
        model = OllamaAgentModel(model="llama3")
        result = model.step(
            history=[{"role": "user", "content": "investigate"}],
            tool_declarations=[],
        )
        assert isinstance(result, Final)
        assert result.text == "final answer text"


def test_ollama_agent_model_fallback_after_two_invalid() -> None:
    call_count = 0

    def fake_post(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Return empty tool calls (invalid)
        mock_resp.json.return_value = {"message": {"tool_calls": [], "content": ""}}
        return mock_resp

    with patch("httpx.post", side_effect=fake_post):
        model = OllamaAgentModel(model="llama3")
        # First invalid call
        model.step(
            history=[{"role": "user", "content": "investigate"}],
            tool_declarations=[
                {
                    "name": "get_failure_groups",
                    "description": "x",
                    "parameters": {},
                }
            ],
        )
        assert model.invalid_tool_calls == 1
        # Second invalid call should raise ProviderError
        with pytest.raises(ProviderError):
            model.step(
                history=[{"role": "user", "content": "investigate"}],
                tool_declarations=[
                    {
                        "name": "get_failure_groups",
                        "description": "x",
                        "parameters": {},
                    }
                ],
            )


# --- launch command ---


def test_launch_config_saves_and_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cidoctor.toml").write_text("[cidoctor]\n")

    # No models available
    with patch.object(
        OllamaProvider, "list_models", return_value=[]
    ), patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(app, ["launch", "--config"])
        assert result.exit_code == 1
        assert "No models available" in result.output


def test_launch_unknown_model_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cidoctor.toml").write_text("[cidoctor]\n")

    with patch.object(
        OllamaProvider, "list_models", return_value=["llama3"]
    ), patch.dict("os.environ", {}, clear=True):
        result = runner.invoke(app, ["launch", "--model", "unknown-model"])
        assert result.exit_code == 1
        assert "ollama pull" in result.output
