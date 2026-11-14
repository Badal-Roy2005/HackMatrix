"""Provider protocol, Evidence model, and registry for CI Doctor.

Defines the :class:`Provider` protocol, the :class:`Evidence` model that
summarizes everything known about a failing run, and a :class:`ProviderRegistry`
that resolves a provider by name. Built-in providers are checked first, then
entry points registered in the ``cidoctor.providers`` group.
"""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from cidoctor.core.models import Diagnosis


@runtime_checkable
class Provider(Protocol):
    """Protocol for AI diagnosis providers.

    Concrete providers expose a ``name`` and implement ``diagnose``, which
    takes an :class:`Evidence` summary and returns a :class:`Diagnosis`.
    """

    name: str

    def diagnose(self, evidence: Evidence) -> Diagnosis:
        """Return a :class:`Diagnosis` for ``evidence``."""
        ...


class Evidence(BaseModel):
    """Everything known about a failing run, summarized for a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    log_text: str = ""
    diff_text: str = ""
    failure_groups: list[dict[str, Any]] = Field(default_factory=list)
    flaky_verdicts: list[dict[str, Any]] = Field(default_factory=list)
    similar_failures: list[dict[str, Any]] = Field(default_factory=list)
    test_source: str = ""


class ProviderRegistry:
    """Registry of built-in and entry-point providers.

    ``get_provider`` checks built-in providers first, then entry points in
    the ``cidoctor.providers`` group, raising :class:`ProviderError` if none
    match.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        """Register a built-in provider (checked before entry points)."""
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> Provider:
        """Return a provider by name.

        Args:
            name: provider name (e.g. ``gemini``, ``ollama``, ``mock``).

        Returns:
            A :class:`Provider` instance.

        Raises:
            ProviderError: if no registered or entry-point provider matches.
        """
        from cidoctor.core.errors import ProviderError

        if name in self._providers:
            return self._providers[name]

        # Entry points installed by third-party plugins.
        entry_points: list[object] = []
        try:
            entry_points = list(
                importlib.metadata.entry_points(group="cidoctor.providers")
            )
        except Exception:  # pragma: no cover - metadata lookup is defensive  # noqa: BLE001
            entry_points = []

        for entry_point in entry_points:
            factory = _load_entry_point(entry_point)
            if factory is None:
                continue
            provider = factory() if callable(factory) else factory
            if isinstance(provider, Provider):
                self.register(provider)
                if provider.name == name:
                    return provider

        raise ProviderError(f"No provider found for {name!r}")

    def list_names(self) -> list[str]:
        """Return names of all registered providers."""
        return sorted(self._providers.keys())


def _load_entry_point(entry_point: Any) -> object | None:
    """Load an entry point factory, returning None on any failure."""
    try:
        return entry_point.load()  # type: ignore[no-any-return]
    except (ImportError, AttributeError, ValueError, TypeError, OSError):  # pragma: no cover - defensive
        return None


def default_registry() -> ProviderRegistry:
    """Return a registry pre-loaded with the built-in providers."""
    from cidoctor.providers.gemini import GeminiProvider
    from cidoctor.providers.mock import MockProvider
    from cidoctor.providers.ollama import OllamaProvider

    registry = ProviderRegistry()
    registry.register(GeminiProvider())
    registry.register(OllamaProvider())
    registry.register(MockProvider())
    return registry


__all__ = ["Evidence", "Provider", "ProviderRegistry", "default_registry"]
