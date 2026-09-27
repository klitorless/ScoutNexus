"""Source + candidate model, relationship, and lifecycle tests."""

from app.models import CandidateStatus, Confidence
from tests.conftest import make_demo_campaign, make_demo_candidate, make_demo_source


def _setup(repos, session):
    campaign = make_demo_campaign(repos["campaigns"])
    source = make_demo_source(repos["sources"])
    session.commit()
    return campaign, source


def test_create_source(repos, session):
    source = make_demo_source(repos["sources"])
    session.commit()

    fetched = repos["sources"].get(source.id)
    assert fetched.platform == "reddit"
    assert fetched.source_id == "demo_test_1"
    assert fetched.community == "AItools"
    assert fetched.engagement == {"comments": 10, "score": 5}
    assert fetched.comment_count() == 10


def test_source_upsert_is_idempotent(repos, session):
    source, created = repos["sources"].upsert(
        platform="reddit", source_id="demo_x", title="First"
    )
    assert created is True
    same, created_again = repos["sources"].upsert(
        platform="reddit", source_id="demo_x", title="Updated"
    )
    assert created_again is False
    assert same.id == source.id
    session.commit()
    assert repos["sources"].get_by_platform_id("reddit", "demo_x").title == "Updated"
    # Same source_id on a different platform is a different source.
    other, created_other = repos["sources"].upsert(
        platform="youtube", source_id="demo_x", title="Other platform"
    )
    assert created_other is True
    assert other.id != source.id


def test_create_candidate_with_relationships(repos, session):
    campaign, source = _setup(repos, session)
    candidate = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source.id
    )
    session.commit()

    fetched = repos["candidates"].get(candidate.id)
    assert fetched.status == CandidateStatus.NEW
    assert fetched.confidence == Confidence.HIGH
    # Relationships resolve both directions.
    assert fetched.campaign.name == "Muse Invite"
    assert fetched.source.title == "What AI coding tools are people using?"
    assert candidate in campaign.candidates
    assert candidate in source.candidates


def test_candidate_defaults_to_new(repos, session):
    campaign, source = _setup(repos, session)
    candidate = repos["candidates"].create(
        campaign_id=campaign.id, source_id=source.id
    )
    session.commit()
    assert candidate.status == CandidateStatus.NEW
    assert candidate.reviewed_at is None


def test_lifecycle_new_to_reviewed(repos, session):
    campaign, source = _setup(repos, session)
    candidate = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source.id
    )
    session.commit()

    repos["candidates"].mark_reviewed(candidate)
    session.commit()

    fetched = repos["candidates"].get(candidate.id)
    assert fetched.status == CandidateStatus.REVIEWED
    assert fetched.reviewed_at is not None


def test_lifecycle_new_to_dismissed(repos, session):
    campaign, source = _setup(repos, session)
    candidate = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source.id
    )
    session.commit()

    repos["candidates"].mark_dismissed(candidate)
    session.commit()

    fetched = repos["candidates"].get(candidate.id)
    assert fetched.status == CandidateStatus.DISMISSED
    assert fetched.reviewed_at is not None


def test_inbox_ordering_new_first(repos, session):
    campaign, source = _setup(repos, session)
    reviewed = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source.id,
        status=CandidateStatus.REVIEWED,
    )
    source2 = make_demo_source(repos["sources"], source_id="demo_test_2")
    session.commit()
    new = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source2.id
    )
    session.commit()

    ordered = repos["candidates"].list()
    assert [c.id for c in ordered] == [new.id, reviewed.id]


def test_count_by_status(repos, session):
    campaign, source = _setup(repos, session)
    make_demo_candidate(repos["candidates"], campaign_id=campaign.id, source_id=source.id)
    source2 = make_demo_source(repos["sources"], source_id="demo_test_2")
    session.commit()
    dismissed = make_demo_candidate(
        repos["candidates"], campaign_id=campaign.id, source_id=source2.id
    )
    repos["candidates"].mark_dismissed(dismissed)
    session.commit()

    counts = repos["candidates"].count_by_status()
    assert counts == {"NEW": 1, "DISMISSED": 1}
