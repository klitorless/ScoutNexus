"""Application configuration.

Plain dataclass over environment variables — no extra dependencies.
Everything has a sane local-dev default so `python run.py` just works.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _default_database_url() -> str:
    return os.environ.get(
        "OPPORTUNITYSCOUT_DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'data' / 'opportunityscout.db'}",
    )


@dataclass
class Settings:
    app_name: str = "OpportunityScout"
    database_url: str = field(default_factory=_default_database_url)
    host: str = os.environ.get("OPPORTUNITYSCOUT_HOST", "127.0.0.1")
    port: int = int(os.environ.get("OPPORTUNITYSCOUT_PORT", "8000"))
    # When True, the UI shows a "demo data" banner. Stage 1 ships with
    # demo-only data and no real platform connections.
    demo_mode: bool = os.environ.get("OPPORTUNITYSCOUT_DEMO", "true").lower() in (
        "1",
        "true",
        "yes",
    )


settings = Settings()
