"""Parser package for CI Doctor.

Defines the Parser protocol and holds concrete parsers (JUnit, etc.).
"""

from .base import Parser, ParserRegistry, default_registry
from .junit import JUnitParser

__all__ = ["JUnitParser", "Parser", "ParserRegistry", "default_registry"]
