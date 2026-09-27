"""Source/Candidate separation tests.

Discovery persists Sources without requiring a Campaign; candidacy is a
separate, explicit evaluation step. One source can back candidates for
many campaigns, and re-discovery never duplicates sources.
"""

from app.models import Source
from app.platforms.base import NormalizedSource, PlatformAdapter
from app.services.discovery import DiscoveryService
from tests.conftest import make_demo_campaign


class StubAdapter(PlatformAdapter):
    """Hand-written stub adapter — zero platform-specific imports."""

    platform = "stub"

    def __init__(self, posts):
        self._posts = posts

    def discover(self, query, limit=25):
        return list(self._posts)

    def fetch_post(self, source_id):
        raise AssertionError("not needed")

    def fetch_comments(self, source_id, limit=50):
        return []

    def get_rules(self, community):
        return {}

    def build_url(self, source_id):
        return f"https://example.com/{source_id}"


def make_post(post_id, title="Looking for recommendations"):
    return NormalizedSource(
        platform="stub",
        source_id=post_id,
        community="testers",
        author="someone",
        title=title,
        content="Looking for alternatives to my current tool.",
        url=f"https://example.com/{post_id}",
        engagement={"comments": 3},
    )


def make_service(repos, posts):
    return DiscoveryService(StubAdapter(posts), repos["sources"], repos["candidates"])


def test_discover_sources_needs_no_campaign(repos, session):
    service = make_service(repos, [make_post("s1"), make_post("s2")])
    sources = service.discover_sources(queries=["recommendations"])
    session.commit()

    assert len(sources) == 2
    assert all(isinstance(s, Source) for s in sources)
    assert {s.source_id for s in sources} == {"s1", "s2"}
    # No candidates were created — discovery alone never implies candidacy.
    assert repos["candidates"].list() == []


def test_discover_sources_deduplicates_across_queries(repos, session):
    post = make_post("s1")
    service = make_service(repos, [post])
    first = service.discover_sources(queries=["one", "two", "three"])
    session.commit()
    second = service.discover_sources(queries=["one", "two"])
    session.commit()

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id
    assert session.query(Source).count() == 1


def test_same_source_backs_candidates_for_many_campaigns(repos, session):
    service = make_service(repos, [make_post("s1")])
    (source,) = service.discover_sources(queries=["x"])
    campaign_a = make_demo_campaign(repos["campaigns"], name="Campaign A")
    campaign_b = make_demo_campaign(
        repos["campaigns"], name="Campaign B", code="OTHER"
    )
    session.commit()

    cand_a = service.create_candidate_for_source(source, campaign_a)
    cand_b = service.create_candidate_for_source(source, campaign_b)
    session.commit()

    assert cand_a is not None and cand_b is not None
    assert cand_a.source_id == cand_b.source_id == source.id
    assert cand_a.campaign_id != cand_b.campaign_id
    assert session.query(Source).count() == 1  # source not duplicated


def test_create_candidate_for_source_is_idempotent(repos, session):
    service = make_service(repos, [make_post("s1")])
    (source,) = service.discover_sources(queries=["x"])
    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()

    first = service.create_candidate_for_source(source, campaign)
    session.commit()
    again = service.create_candidate_for_source(source, campaign)
    session.commit()

    assert first is not None
    assert again is None
    assert len(repos["candidates"].list()) == 1


def test_discover_for_campaign_still_composes_both_steps(repos, session):
    service = make_service(repos, [make_post("s1"), make_post("s2")])
    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()

    created = service.discover_for_campaign(campaign, queries=["x"])
    session.commit()
    assert len(created) == 2

    # Second run: nothing new — sources and candidates both deduplicated.
    again = service.discover_for_campaign(campaign, queries=["x"])
    session.commit()
    assert again == []
    assert session.query(Source).count() == 2
    assert len(repos["candidates"].list()) == 2


def test_discover_for_campaign_evaluates_new_sources_only(repos, session):
    service = make_service(repos, [make_post("s1")])
    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()
    service.discover_for_campaign(campaign, queries=["x"])
    session.commit()

    # A newly discovered post gets a candidate; the old one does not duplicate.
    service.adapter = StubAdapter([make_post("s1"), make_post("s3")])
    created = service.discover_for_campaign(campaign, queries=["x"])
    session.commit()

    assert [c.source.source_id for c in created] == ["s3"]
    assert session.query(Source).count() == 2
