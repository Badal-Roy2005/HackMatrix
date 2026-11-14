"""Storage package for CI Doctor.

Defines the Store protocol and the JSON run-history store.
"""

from .base import Store
from .json_store import DEFAULT_MAX_FILE_SIZE, DEFAULT_STORE_PATH, JsonStore

__all__ = ["DEFAULT_MAX_FILE_SIZE", "DEFAULT_STORE_PATH", "JsonStore", "Store"]
