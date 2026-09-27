"""Platform adapter registry.

Adapters self-register via @register_adapter so the core can look them up
by platform name without importing platform-specific code.
"""

from __future__ import annotations

from app.platforms.base import NormalizedSource, PlatformAdapter

ADAPTER_REGISTRY: dict[str, type[PlatformAdapter]] = {}


def register_adapter(cls: type[PlatformAdapter]) -> type[PlatformAdapter]:
    ADAPTER_REGISTRY[cls.platform] = cls
    return cls


__all__ = ["ADAPTER_REGISTRY", "NormalizedSource", "PlatformAdapter", "register_adapter"]
