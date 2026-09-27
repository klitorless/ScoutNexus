"""Seed demo data for OpportunityScout (Stage 1).

Creates:
  - a demo "Muse Invite" campaign with FAKE credentials
  - demo Reddit-style sources via the MockRedditAdapter
  - candidates produced by the DiscoveryService (deterministic mock analysis)

Everything created here is synthetic demo content — no scraping, no real
posts, no real invite codes.

Usage:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --reset   # wipe and re-seed demo data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.database import Base, make_engine, make_session_factory
from app.platforms.mock import MockRedditAdapter
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.source_repository import SourceRepository
from app.services.discovery import DiscoveryService

DEMO_CAMPAIGN_NAME = "Muse Invite"
DEMO_QUERIES = [
    "ai coding tools",
    "claude code alternatives",
    "ai assistant developer",
]


def seed(session, reset: bool = False) -> dict[str, int]:
    campaign_repo = CampaignRepository(session)
    source_repo = SourceRepository(session)
    candidate_repo = CandidateRepository(session)

    existing = campaign_repo.get_by_name(DEMO_CAMPAIGN_NAME)
    if reset and existing is not None:
        session.delete(existing)
        session.flush()
        existing = None

    if existing is None:
        campaign = campaign_repo.create(
            name=DEMO_CAMPAIGN_NAME,
            service="Muse",
            offer_type="invite",
            description=(
                "Demo campaign for Stage 1. In production this would hold the "
                "user's real invite code, entered via configuration/UI."
            ),
            code="DEMO-MUSE-CODE",
            url="https://example.com/demo",
            disclosure=(
                "Demo disclosure placeholder. A real campaign must disclose "
                "referral/invite relationships when responding."
            ),
            active=True,
        )
    else:
        campaign = existing

    service = DiscoveryService(
        adapter=MockRedditAdapter(),
        source_repo=source_repo,
        candidate_repo=candidate_repo,
    )
    new_candidates = service.discover_for_campaign(
        campaign, queries=DEMO_QUERIES, limit_per_query=6
    )
    session.commit()

    return {
        "campaign_id": campaign.id,
        "new_candidates": len(new_candidates),
        "total_candidates": len(candidate_repo.list(campaign_id=campaign.id)),
        "total_sources": len(source_repo.list()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed OpportunityScout demo data.")
    parser.add_argument("--reset", action="store_true", help="Wipe demo data and re-seed.")
    args = parser.parse_args()

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()

    stats = seed(session, reset=args.reset)
    session.close()
    print(f"Demo campaign id: {stats['campaign_id']}")
    print(f"New candidates this run: {stats['new_candidates']}")
    print(f"Total candidates: {stats['total_candidates']}")
    print(f"Total sources: {stats['total_sources']}")
    print("Done. Run `python run.py` and open http://127.0.0.1:8000/candidates")


if __name__ == "__main__":
    main()
