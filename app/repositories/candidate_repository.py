"""Data access for candidates, including the review lifecycle."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Candidate, CandidateStatus


class CandidateRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **fields) -> Candidate:
        candidate = Candidate(**fields)
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def get(self, candidate_id: int) -> Candidate | None:
        return self.session.get(Candidate, candidate_id)

    def get_for_campaign_source(self, campaign_id: int, source_id: int) -> Candidate | None:
        """Find an existing candidate linking this campaign to this source."""
        return self.session.scalar(
            select(Candidate).where(
                Candidate.campaign_id == campaign_id,
                Candidate.source_id == source_id,
            )
        )

    def list(
        self,
        campaign_id: int | None = None,
        status: CandidateStatus | None = None,
    ) -> list[Candidate]:
        stmt = select(Candidate)
        if campaign_id is not None:
            stmt = stmt.where(Candidate.campaign_id == campaign_id)
        if status is not None:
            stmt = stmt.where(Candidate.status == status)
        # Inbox order: unreviewed first, newest first.
        stmt = stmt.order_by(
            (Candidate.status == CandidateStatus.NEW).desc(),
            Candidate.created_at.desc(),
        )
        return list(self.session.scalars(stmt))

    def mark_reviewed(self, candidate: Candidate) -> Candidate:
        candidate.mark_reviewed()
        self.session.flush()
        return candidate

    def mark_dismissed(self, candidate: Candidate) -> Candidate:
        candidate.mark_dismissed()
        self.session.flush()
        return candidate

    def count_by_status(self, campaign_id: int | None = None) -> dict[str, int]:
        stmt = select(Candidate.status, func.count(Candidate.id))
        if campaign_id is not None:
            stmt = stmt.where(Candidate.campaign_id == campaign_id)
        stmt = stmt.group_by(Candidate.status)
        return {status.value: count for status, count in self.session.execute(stmt)}
