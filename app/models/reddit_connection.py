"""RedditConnection model — a user-authorized Reddit OAuth connection.

This is Stage 4's user-authorized connection (authorization-code flow
against a *web* Reddit app), distinct from Stage 2's application-level
credentials (client_credentials grant against a *script* app) that
DiscoveryService keeps using for read-only discovery.

Tokens live server-side in SQLite. Plaintext SQLite storage is NOT
equivalent to encrypted secret storage — see the README's security
notes. Tokens must never be rendered into templates, logs, or API
responses (the __repr__ below redacts them).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from app.core.database import Base


class RedditConnection(Base):
    __tablename__ = "reddit_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Reddit username, taken from the authenticated /api/v1/me response —
    # never from query parameters or client input.
    reddit_username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the access token expires (UTC, naive). None if unknown.
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Space-separated scope string as granted by Reddit, e.g. "identity".
    scopes: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now, nullable=False
    )

    def is_expired(self, at: datetime | None = None) -> bool:
        """True when the access token is known to be expired."""
        if self.token_expires_at is None:
            return False
        return (at or datetime.now()) >= self.token_expires_at

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        # Tokens are deliberately redacted: never log secrets.
        return (
            f"<RedditConnection id={self.id} "
            f"reddit_username={self.reddit_username!r} "
            f"access_token='<redacted>' refresh_token='<redacted>'>"
        )
