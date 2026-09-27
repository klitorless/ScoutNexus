"""Source model — a normalized, platform-agnostic representation of a post.

A Source is "a thing someone posted somewhere". The `platform` field says
where it came from ("reddit" today; "youtube", "x", ... tomorrow) and
`engagement` holds flexible platform-specific metadata as JSON. Core
searchable fields stay structured columns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from app.core.database import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("platform", "source_id", name="uq_source_platform_source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Platform identifier, e.g. "reddit". Never assume a single platform.
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Platform-native ID (e.g. Reddit post id). Unique per platform.
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Community the post appeared in (subreddit, channel, group, ...).
    community: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # When the post was created on the platform (may be unknown for demos).
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Flexible platform-specific metadata, e.g. {"comments": 18, "score": 42}.
    engagement: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # When ScoutNexus ingested this source.
    created_at: Mapped[datetime] = mapped_column(default=datetime.now, nullable=False)

    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate", back_populates="source", cascade="all, delete-orphan"
    )

    def comment_count(self) -> int | None:
        if isinstance(self.engagement, dict):
            value = self.engagement.get("comments")
            return int(value) if value is not None else None
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Source id={self.id} platform={self.platform!r} source_id={self.source_id!r}>"
