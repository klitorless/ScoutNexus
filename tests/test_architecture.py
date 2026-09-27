"""Architecture tests.

The core promise of Stage 1: the candidate/domain layer works against the
PlatformAdapter interface and does NOT require Reddit-specific code
(no PRAW, no Reddit API objects, no network).
"""

import re
from pathlib import Path

from app.platforms import ADAPTER_REGISTRY
from app.platforms.base import NormalizedSource, PlatformAdapter
from app.platforms.mock import MockRedditAdapter
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.source_repository import SourceRepository
from app.services.discovery import DiscoveryService, analyze_source_for_campaign
from tests.conftest import make_demo_campaign

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_adapter_interface_defines_required_methods():
    abstract = PlatformAdapter.__abstractmethods__
    assert abstract == {
        "discover",
        "fetch_post",
        "fetch_comments",
        "get_rules",
        "build_url",
    }


def test_mock_adapter_registers_itself():
    assert ADAPTER_REGISTRY["reddit"] is MockRedditAdapter


def test_discovery_runs_against_a_stub_with_no_reddit_dependency(repos, session):
    """A hand-written stub adapter (zero Reddit imports) drives candidates."""

    class StubAdapter(PlatformAdapter):
        platform = "stub"

        def discover(self, query, limit=25):
            return [
                NormalizedSource(
                    platform="stub",
                    source_id="s1",
                    community="testers",
                    author="someone",
                    title="Looking for recommendations",
                    content="Looking for alternatives to my current tool.",
                    url="https://example.com/s1",
                    engagement={"comments": 3},
                )
            ]

        def fetch_post(self, source_id):
            raise AssertionError("not needed")

        def fetch_comments(self, source_id, limit=50):
            return []

        def get_rules(self, community):
            return {}

        def build_url(self, source_id):
            return "https://example.com/s1"

    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()

    service = DiscoveryService(StubAdapter(), repos["sources"], repos["candidates"])
    created = service.discover_for_campaign(campaign, queries=["recommendations"])
    session.commit()

    assert len(created) == 1
    candidate = created[0]
    assert candidate.campaign_id == campaign.id
    assert candidate.source.platform == "stub"
    assert candidate.why_found  # mock analysis produced reasoning

    # Idempotent: second run creates nothing new.
    again = service.discover_for_campaign(campaign, queries=["recommendations"])
    assert again == []


def test_mock_analysis_is_deterministic_and_coarse(repos, session):
    from app.models import Confidence

    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()
    source = NormalizedSource(
        platform="reddit",
        source_id="x",
        title="Looking for alternatives to Claude Code",
        content="Any recommendations?",
    )
    first = analyze_source_for_campaign(source, campaign)
    second = analyze_source_for_campaign(source, campaign)
    assert first == second
    assert first["confidence"] == Confidence.HIGH
    # No fake precision anywhere in the analysis output.
    blob = " ".join(str(v) for v in first.values())
    assert not re.search(r"\d+\.\d+%", blob)


def test_no_reddit_library_imports_in_app():
    """Core code must not import PRAW or any Reddit API client."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text()
        if re.search(r"^\s*(import|from)\s+praw\b", text, re.MULTILINE):
            offenders.append(str(path))
    assert offenders == []


def test_no_network_calls_in_discovery_service():
    text = (APP_DIR / "services" / "discovery.py").read_text()
    for banned in ("requests.", "urllib", "httpx.", "praw"):
        assert banned not in text


def test_discovery_does_not_depend_on_user_oauth():
    """Discovery keeps using application-level credentials.

    The Stage 4 user-authorized OAuth layer (app/services/reddit_oauth.py)
    is a separate concern; discovery must not import it.
    """
    text = (APP_DIR / "services" / "discovery.py").read_text()
    assert "reddit_oauth" not in text
    assert "RedditConnection" not in text
