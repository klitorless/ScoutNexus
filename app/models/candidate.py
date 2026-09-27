"""Candidate / opportunity model.

Critical distinction: a Source is a post that exists on a platform; a
Candidate is the system's determination that a source *may represent an
opportunity for a particular campaign*. One source can back candidates for
many campaigns, which is why the model references both.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from app.core.database import Base


class CandidateStatus(str, enum.Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"
    # Set when a human decides this candidate is worth acting on.
    # (Response drafting itself arrives in a later stage.)
    RESPOND = "RESPOND"


class Confidence(str, enum.Enum):
    """Deliberately coarse: evidence and reasoning matter more than a fake
    precise percentage."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id"), nullable=False, index=True
    )

    status: Mapped[CandidateStatus] = mapped_column(
        SqlEnum(CandidateStatus), default=CandidateStatus.NEW, nullable=False, index=True
    )

    # Human-readable reasoning captured at discovery time.
    user_need: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_found: Mapped[str | None] = mapped_column(Text, nullable=True)
    potential_connection: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_context: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence: Mapped[Confidence] = mapped_column(
        SqlEnum(Confidence), default=Confidence.MEDIUM, nullable=False
    )
    evidence_quality: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="candidates")
    source: Mapped["Source"] = relationship("Source", back_populates="candidates")

    def mark_reviewed(self) -> None:
        self.status = CandidateStatus.REVIEWED
        self.reviewed_at = datetime.now()

    def mark_dismissed(self) -> None:
        self.status = CandidateStatus.DISMISSED
        self.reviewed_at = datetime.now()

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Candidate id={self.id} campaign_id={self.campaign_id} "
            f"source_id={self.source_id} status={self.status.value}>"
        )
