"""Provider package for CI Doctor.

Defines the Provider protocol and holds concrete providers (Gemini, Ollama,
mock).
"""

from .base import Evidence, Provider, ProviderRegistry, default_registry
from .gemini import GeminiProvider

__all__ = ["Evidence", "GeminiProvider", "Provider", "ProviderRegistry", "default_registry"]
