"""Data access for campaigns."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Campaign


class CampaignRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **fields) -> Campaign:
        campaign = Campaign(**fields)
        self.session.add(campaign)
        self.session.flush()
        return campaign

    def get(self, campaign_id: int) -> Campaign | None:
        return self.session.get(Campaign, campaign_id)

    def get_by_name(self, name: str) -> Campaign | None:
        return self.session.scalar(select(Campaign).where(Campaign.name == name))

    def list(self, active_only: bool = True) -> list[Campaign]:
        stmt = select(Campaign).order_by(Campaign.created_at.desc())
        if active_only:
            stmt = stmt.where(Campaign.active.is_(True))
        return list(self.session.scalars(stmt))

    def deactivate(self, campaign: Campaign) -> Campaign:
        campaign.active = False
        self.session.flush()
        return campaign
