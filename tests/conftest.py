"""Shared pytest fixtures: isolated in-memory DB + test client."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app, get_db
from app.models import Campaign, Candidate, CandidateStatus, Confidence, Source
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.source_repository import SourceRepository


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    yield sess
    sess.close()


@pytest.fixture
def repos(session):
    return {
        "campaigns": CampaignRepository(session),
        "sources": SourceRepository(session),
        "candidates": CandidateRepository(session),
    }


@pytest.fixture
def client(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        sess = factory()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def make_demo_campaign(repo, **overrides):
    fields = dict(
        name="Muse Invite",
        service="Muse",
        offer_type="invite",
        description="demo",
        code="DEMO-MUSE-CODE",
        url="https://example.com/demo",
        disclosure="demo disclosure",
        active=True,
    )
    fields.update(overrides)
    return repo.create(**fields)


def make_demo_source(repo, **overrides):
    fields = dict(
        platform="reddit",
        source_id="demo_test_1",
        community="AItools",
        author="demo_user",
        title="What AI coding tools are people using?",
        content="Looking for recommendations on AI coding assistants.",
        url="https://www.reddit.com/r/AItools/comments/demo_test_1/demo/",
        engagement={"comments": 10, "score": 5},
    )
    fields.update(overrides)
    return repo.create(**fields)


def make_demo_candidate(repo, campaign_id, source_id, **overrides):
    fields = dict(
        campaign_id=campaign_id,
        source_id=source_id,
        status=CandidateStatus.NEW,
        user_need="needs a coding assistant",
        why_found="keyword match",
        potential_connection="campaign may be relevant",
        conversation_context="context",
        confidence=Confidence.HIGH,
        evidence_quality="strong keyword evidence",
    )
    fields.update(overrides)
    return repo.create(**fields)
