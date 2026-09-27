"""Data access for sources, including idempotent upsert by platform ID."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source


class SourceRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, **fields) -> Source:
        source = Source(**fields)
        self.session.add(source)
        self.session.flush()
        return source

    def get(self, source_id: int) -> Source | None:
        return self.session.get(Source, source_id)

    def get_by_platform_id(self, platform: str, source_id: str) -> Source | None:
        return self.session.scalar(
            select(Source).where(
                Source.platform == platform, Source.source_id == source_id
            )
        )

    def upsert(self, platform: str, source_id: str, **fields) -> tuple[Source, bool]:
        """Insert or update a source identified by (platform, source_id).

        Returns (source, created). Re-running discovery over the same posts
        must not create duplicates.
        """
        existing = self.get_by_platform_id(platform, source_id)
        if existing is None:
            source = self.create(platform=platform, source_id=source_id, **fields)
            return source, True
        for key, value in fields.items():
            setattr(existing, key, value)
        self.session.flush()
        return existing, False

    def list(self, platform: str | None = None) -> list[Source]:
        stmt = select(Source).order_by(Source.created_at.desc())
        if platform is not None:
            stmt = stmt.where(Source.platform == platform)
        return list(self.session.scalars(stmt))
