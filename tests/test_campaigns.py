"""Campaign model + repository tests."""

import pytest

from app.models import OFFER_TYPES, Campaign
from tests.conftest import make_demo_campaign


def test_create_campaign(repos, session):
    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()

    fetched = repos["campaigns"].get(campaign.id)
    assert fetched.name == "Muse Invite"
    assert fetched.service == "Muse"
    assert fetched.offer_type == "invite"
    assert fetched.code == "DEMO-MUSE-CODE"
    assert fetched.active is True
    assert fetched.created_at is not None


def test_offer_type_must_be_known(repos):
    with pytest.raises(ValueError):
        make_demo_campaign(repos["campaigns"], offer_type="teleportation")


def test_offer_types_are_extensible_and_platform_neutral():
    assert "invite" in OFFER_TYPES
    assert "referral" in OFFER_TYPES
    assert "discount" in OFFER_TYPES
    # Nothing Muse-specific or Reddit-specific in the supported types.
    assert not any("muse" in t or "reddit" in t for t in OFFER_TYPES)


def test_list_active_only(repos, session):
    make_demo_campaign(repos["campaigns"], name="Active One")
    make_demo_campaign(repos["campaigns"], name="Inactive One", active=False)
    session.commit()

    active = repos["campaigns"].list(active_only=True)
    assert {c.name for c in active} == {"Active One"}

    all_campaigns = repos["campaigns"].list(active_only=False)
    assert {c.name for c in all_campaigns} == {"Active One", "Inactive One"}


def test_deactivate(repos, session):
    campaign = make_demo_campaign(repos["campaigns"])
    session.commit()

    repos["campaigns"].deactivate(campaign)
    session.commit()

    assert repos["campaigns"].get(campaign.id).active is False
    assert repos["campaigns"].list(active_only=True) == []


def test_get_by_name(repos, session):
    make_demo_campaign(repos["campaigns"], name="Muse Invite")
    session.commit()
    assert repos["campaigns"].get_by_name("Muse Invite") is not None
    assert repos["campaigns"].get_by_name("Nope") is None
