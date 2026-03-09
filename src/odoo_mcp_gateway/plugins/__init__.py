"""Plugin system for extending gateway functionality."""

from __future__ import annotations

from .base import OdooPlugin
from .registry import PluginInfo, PluginRegistry

__all__ = [
    "OdooPlugin",
    "PluginInfo",
    "PluginRegistry",
]
