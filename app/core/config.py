"""Application configuration.

Plain dataclass over environment variables — no extra dependencies.
Everything has a sane local-dev default so `python run.py` just works.

Naming history: the project was originally called OpportunityScout, so
`OPPORTUNITYSCOUT_*` variables are still honored as a fallback. New
setups should use `SCOUTNEXUS_*`.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _env(primary: str, legacy: str, default: str) -> str:
    """Read the new SCOUTNEXUS_* variable, falling back to OPPORTUNITYSCOUT_*."""
    return os.environ.get(primary) or os.environ.get(legacy) or default


def _default_database_url() -> str:
    url = os.environ.get("SCOUTNEXUS_DATABASE_URL") or os.environ.get(
        "OPPORTUNITYSCOUT_DATABASE_URL"
    )
    if url:
        return url
    # The default filename keeps the original name so existing local data
    # (data/opportunityscout.db) is not orphaned by the rename.
    return f"sqlite:///{BASE_DIR / 'data' / 'opportunityscout.db'}"


@dataclass
class Settings:
    app_name: str = "ScoutNexus"
    database_url: str = field(default_factory=_default_database_url)
    host: str = field(
        default_factory=lambda: _env(
            "SCOUTNEXUS_HOST", "OPPORTUNITYSCOUT_HOST", "127.0.0.1"
        )
    )
    port: int = field(
        default_factory=lambda: int(
            _env("SCOUTNEXUS_PORT", "OPPORTUNITYSCOUT_PORT", "8000")
        )
    )
    # When True, the UI shows a "demo data" banner. The project ships with
    # demo-only data and no real platform connections.
    demo_mode: bool = field(
        default_factory=lambda: _env(
            "SCOUTNEXUS_DEMO", "OPPORTUNITYSCOUT_DEMO", "true"
        ).lower()
        in ("1", "true", "yes")
    )


settings = Settings()
