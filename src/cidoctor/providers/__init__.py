"""Provider package for CI Doctor.

Defines the Provider protocol and holds concrete providers (Gemini, Ollama,
mock).
"""

from .base import Evidence, Provider, ProviderRegistry, default_registry
from .gemini import GeminiProvider
from .mock import MockProvider
from .ollama import OllamaAgentModel, OllamaProvider

__all__ = [
    "Evidence",
    "GeminiProvider",
    "MockProvider",
    "OllamaAgentModel",
    "OllamaProvider",
    "Provider",
    "ProviderRegistry",
    "default_registry",
]
