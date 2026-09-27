"""Route tests: inbox, detail, review/dismiss actions, 404s, campaigns."""

import pytest

from app.models import CandidateStatus
from tests.conftest import make_demo_campaign, make_demo_candidate, make_demo_source


@pytest.fixture
def seeded(client, engine):
    """Seed one campaign + one candidate through the app's own DB session."""
    from sqlalchemy.orm import sessionmaker

    from app.repositories.candidate_repository import CandidateRepository
    from app.repositories.campaign_repository import CampaignRepository
    from app.repositories.source_repository import SourceRepository

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    campaign = make_demo_campaign(CampaignRepository(sess))
    source = make_demo_source(SourceRepository(sess))
    candidate = make_demo_candidate(
        CandidateRepository(sess), campaign_id=campaign.id, source_id=source.id
    )
    sess.commit()
    ids = {"campaign_id": campaign.id, "source_id": source.id, "candidate_id": candidate.id}
    sess.close()
    return ids


def _status_in_db(engine, candidate_id):
    from sqlalchemy.orm import sessionmaker

    from app.repositories.candidate_repository import CandidateRepository

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = factory()
    status = CandidateRepository(sess).get(candidate_id).status
    sess.close()
    return status


def test_root_redirects_to_inbox(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Opportunity Inbox" in response.text


def test_candidate_list_loads(client, seeded):
    response = client.get("/candidates")
    assert response.status_code == 200
    assert "What AI coding tools are people using?" in response.text
    assert "REDDIT" in response.text
    assert "r/AItools" in response.text


def test_candidate_list_status_filter(client, seeded):
    response = client.get("/candidates?status=new")
    assert response.status_code == 200
    assert "What AI coding tools are people using?" in response.text
    response = client.get("/candidates?status=reviewed")
    assert response.status_code == 200
    assert "What AI coding tools are people using?" not in response.text


def test_candidate_list_bad_filter(client):
    response = client.get("/candidates?status=bogus")
    assert response.status_code == 400


def test_candidate_detail_loads(client, seeded):
    response = client.get(f"/candidates/{seeded['candidate_id']}")
    assert response.status_code == 200
    assert "What AI coding tools are people using?" in response.text
    assert "Why the system found it" in response.text
    assert "Open Original Post" in response.text


def test_candidate_detail_missing_returns_404(client):
    response = client.get("/candidates/99999")
    assert response.status_code == 404


def test_review_action_works(client, seeded, engine):
    response = client.post(f"/candidates/{seeded['candidate_id']}/review")
    assert response.status_code == 200
    assert _status_in_db(engine, seeded["candidate_id"]) == CandidateStatus.REVIEWED
    # Detail page reflects the new status.
    detail = client.get(f"/candidates/{seeded['candidate_id']}")
    assert "REVIEWED" in detail.text


def test_dismiss_action_works(client, seeded, engine):
    response = client.post(f"/candidates/{seeded['candidate_id']}/dismiss")
    assert response.status_code == 200
    assert _status_in_db(engine, seeded["candidate_id"]) == CandidateStatus.DISMISSED


def test_review_missing_candidate_returns_404(client):
    assert client.post("/candidates/99999/review").status_code == 404
    assert client.post("/candidates/99999/dismiss").status_code == 404


def test_campaigns_page_loads(client, seeded):
    response = client.get("/campaigns")
    assert response.status_code == 200
    assert "Muse Invite" in response.text
    assert "invite" in response.text
