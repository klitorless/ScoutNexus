"""Stage 3 discovery tests: targets, multi-target runs, dedup, errors.

All adapter behavior is stubbed — no Reddit credentials, no network.
Raw discovery must never create candidates or run campaign analysis.
"""

from pathlib import Path

import pytest

from app.main import app, get_discovery_adapter
from app.models import Candidate, Source
from app.platforms.base import NormalizedSource, PlatformAdapter
from app.services.discovery import (
    DEFAULT_DISCOVERY_TARGETS,
    DiscoveryService,
    DiscoveryTarget,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"


class ScriptedAdapter(PlatformAdapter):
    """Stub adapter with scripted per-(query, community) responses.

    Accepts the platform-specific search kwargs (subreddit, sort,
    time_filter) that the real RedditAdapter supports.
    """

    platform = "reddit"

    def __init__(self):
        self.calls: list[tuple] = []
        self.scripts: dict[tuple, object] = {}

    def when(self, query, community=None, posts=(), error=None):
        self.scripts[(query, community)] = error if error is not None else list(posts)
        return self

    def discover(self, query, limit=25, **options):
        subreddit = options.get("subreddit")
        self.calls.append(
            (query, subreddit, options.get("sort"), options.get("time_filter"), limit)
        )
        outcome = self.scripts.get((query, subreddit), [])
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)[:limit]

    def fetch_post(self, source_id):
        raise AssertionError("not needed")

    def fetch_comments(self, source_id, limit=50):
        return []

    def get_rules(self, community):
        return {}

    def build_url(self, source_id):
        return f"https://example.com/{source_id}"


def post(pid, **overrides):
    fields = dict(
        platform="reddit",
        source_id=pid,
        community="testers",
        author="u1",
        title=f"Post {pid}",
        content="some content",
        url=f"https://example.com/{pid}",
        engagement={"comments": 1},
    )
    fields.update(overrides)
    return NormalizedSource(**fields)


def make_service(repos, adapter=None):
    adapter = adapter if adapter is not None else ScriptedAdapter()
    return DiscoveryService(adapter, repos["sources"], repos["candidates"]), adapter


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_target_defaults():
    target = DiscoveryTarget(query="coding assistant")
    assert target.community is None
    assert target.limit == 25
    assert target.sort == "new"
    assert target.time_filter == "week"
    assert target.enabled is True
    assert target.platform == "reddit"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 101},
        {"query": "x", "limit": "many"},
        {"query": "x", "sort": ""},
        {"query": "x", "time_filter": ""},
        {"query": "x", "platform": ""},
    ],
)
def test_target_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        DiscoveryTarget(**kwargs)


def test_default_targets_are_valid_and_enabled():
    assert len(DEFAULT_DISCOVERY_TARGETS) >= 1
    for target in DEFAULT_DISCOVERY_TARGETS:
        assert target.enabled
        assert target.query.strip()


# ---------------------------------------------------------------------------
# Target selection and option pass-through
# ---------------------------------------------------------------------------


def test_disabled_target_is_skipped(repos, session):
    adapter = ScriptedAdapter().when("on", posts=[post("s1")])
    service, _ = make_service(repos, adapter)
    targets = [
        DiscoveryTarget(query="on", limit=5),
        DiscoveryTarget(query="off", enabled=False),
    ]
    result = service.run_discovery(targets)
    session.commit()

    assert result.targets_attempted == 1
    assert result.targets_skipped == 1
    assert [c[0] for c in adapter.calls] == ["on"]
    assert result.sources_created == 1


def test_target_for_other_platform_is_skipped(repos, session):
    service, adapter = make_service(repos)
    result = service.run_discovery(
        [DiscoveryTarget(query="x", platform="youtube")]
    )
    session.commit()

    assert result.targets_attempted == 0
    assert result.targets_skipped == 1
    assert adapter.calls == []
    assert result.sources_created == 0


def test_multiple_targets_each_run_with_their_own_options(repos, session):
    adapter = (
        ScriptedAdapter()
        .when("alpha", community="sub_a", posts=[post("s1")])
        .when("beta", community="sub_b", posts=[post("s2")])
        .when("gamma", posts=[post("s3")])
    )
    service, _ = make_service(repos, adapter)
    targets = [
        DiscoveryTarget(query="alpha", community="sub_a", limit=10, sort="new", time_filter="day"),
        DiscoveryTarget(query="beta", community="sub_b", limit=7, sort="top", time_filter="month"),
        DiscoveryTarget(query="gamma", limit=3),  # site-wide
    ]
    result = service.run_discovery(targets)
    session.commit()

    assert result.targets_attempted == 3
    assert result.sources_created == 3
    assert adapter.calls == [
        ("alpha", "sub_a", "new", "day", 10),
        ("beta", "sub_b", "top", "month", 7),
        ("gamma", None, "new", "week", 3),
    ]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_same_post_from_two_targets_stored_once(repos, session):
    shared = post("shared")
    adapter = (
        ScriptedAdapter()
        .when("alpha", community="sub_a", posts=[shared])
        .when("beta", community="sub_b", posts=[shared])
    )
    service, _ = make_service(repos, adapter)
    result = service.run_discovery(
        [
            DiscoveryTarget(query="alpha", community="sub_a"),
            DiscoveryTarget(query="beta", community="sub_b"),
        ]
    )
    session.commit()

    assert result.sources_seen == 2
    assert result.sources_created == 1
    assert result.duplicates == 1
    assert session.query(Source).count() == 1


def test_repeated_run_creates_no_duplicates(repos, session):
    adapter = ScriptedAdapter().when("alpha", posts=[post("s1"), post("s2")])
    service, _ = make_service(repos, adapter)
    targets = [DiscoveryTarget(query="alpha")]

    first = service.run_discovery(targets)
    session.commit()
    second = service.run_discovery(targets)
    session.commit()

    assert first.sources_created == 2
    assert second.sources_created == 0
    assert second.duplicates == 2
    assert second.ok
    assert session.query(Source).count() == 2


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_discovered_sources_persist_expected_fields(repos, session):
    created_post = post(
        "abc123",
        community="ClaudeCode",
        author="someone",
        title="Looking for a coding assistant",
        content="body text",
        url="https://www.reddit.com/comments/abc123/",
        engagement={"comments": 12, "score": 40},
    )
    adapter = ScriptedAdapter().when("q", community="ClaudeCode", posts=[created_post])
    service, _ = make_service(repos, adapter)
    service.run_discovery([DiscoveryTarget(query="q", community="ClaudeCode")])
    session.commit()

    stored = repos["sources"].get_by_platform_id("reddit", "abc123")
    assert stored is not None
    assert stored.community == "ClaudeCode"
    assert stored.author == "someone"
    assert stored.title == "Looking for a coding assistant"
    assert stored.content == "body text"
    assert stored.url == "https://www.reddit.com/comments/abc123/"
    assert stored.engagement == {"comments": 12, "score": 40}


# ---------------------------------------------------------------------------
# Campaign independence — the hard requirement
# ---------------------------------------------------------------------------


def test_run_discovery_needs_no_campaign_and_creates_none(repos, session):
    adapter = ScriptedAdapter().when("q", posts=[post("s1")])
    service, _ = make_service(repos, adapter)

    result = service.run_discovery([DiscoveryTarget(query="q")])
    session.commit()

    assert result.sources_created == 1
    assert session.query(Candidate).count() == 0


def test_run_discovery_never_runs_campaign_analysis(repos, session, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("campaign analysis must not run during raw discovery")

    monkeypatch.setattr(
        "app.services.discovery.analyze_source_for_campaign", boom
    )
    adapter = ScriptedAdapter().when("q", posts=[post("s1")])
    service, _ = make_service(repos, adapter)

    result = service.run_discovery([DiscoveryTarget(query="q")])
    session.commit()

    assert result.ok
    assert session.query(Candidate).count() == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_one_failing_target_does_not_stop_the_others(repos, session):
    adapter = (
        ScriptedAdapter()
        .when("good_a", posts=[post("s1")])
        .when("bad", error=RuntimeError("Reddit exploded"))
        .when("good_c", posts=[post("s2")])
    )
    service, _ = make_service(repos, adapter)
    result = service.run_discovery(
        [
            DiscoveryTarget(query="good_a"),
            DiscoveryTarget(query="bad"),
            DiscoveryTarget(query="good_c"),
        ]
    )
    session.commit()

    assert result.targets_attempted == 3
    assert not result.ok
    assert len(result.errors) == 1
    assert "bad" in result.errors[0]["target"]
    assert "RuntimeError" in result.errors[0]["error"]
    assert "Reddit exploded" in result.errors[0]["error"]
    # Successful targets preserved.
    assert result.sources_created == 2
    assert session.query(Source).count() == 2


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------


def test_discovery_service_does_not_import_reddit_implementation():
    text = (APP_DIR / "services" / "discovery.py").read_text()
    assert "app.platforms.reddit" not in text
    assert "RedditAdapter" not in text
    assert "RedditError" not in text


# ---------------------------------------------------------------------------
# Web verification
# ---------------------------------------------------------------------------


def test_discovery_console_loads(client):
    response = client.get("/discovery")
    assert response.status_code == 200
    assert "Discovery Console" in response.text
    assert "AI coding assistant" in response.text


def test_sources_list_and_detail(client, engine):
    from sqlalchemy.orm import sessionmaker

    from app.repositories.source_repository import SourceRepository
    from tests.conftest import make_demo_source

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    source = make_demo_source(SourceRepository(sess))
    sess.commit()
    source_id = source.id
    sess.close()

    listing = client.get("/sources")
    assert listing.status_code == 200
    assert "Discovered Sources" in listing.text
    assert "What AI coding tools are people using?" in listing.text

    detail = client.get(f"/sources/{source_id}")
    assert detail.status_code == 200
    assert "demo_test_1" in detail.text
    assert "r/AItools" in detail.text

    assert client.get("/sources/99999").status_code == 404


def test_discovery_run_with_stub_adapter_populates_sources(client, engine):
    shared = post("web1", title="Web discovered post")
    adapter = (
        ScriptedAdapter()
        .when("AI coding assistant", community="ClaudeCode", posts=[shared])
        .when("looking for coding assistant", community="programming", posts=[shared])
    )
    app.dependency_overrides[get_discovery_adapter] = lambda: adapter
    try:
        response = client.post("/discovery/run")
    finally:
        del app.dependency_overrides[get_discovery_adapter]

    assert response.status_code == 200
    assert "Discovery result" in response.text
    # Same post from both default targets -> created once, seen twice.
    assert "New sources created" in response.text
    assert "Web discovered post" in client.get("/sources").text

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    try:
        assert sess.query(Source).count() == 1
        assert sess.query(Candidate).count() == 0
    finally:
        sess.close()


def test_discovery_run_without_credentials_shows_clear_message(client, monkeypatch):
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(var, raising=False)
    response = client.post("/discovery/run")
    assert response.status_code == 200
    assert "not configured" in response.text.lower()
    assert "Discovery did not run" in response.text
