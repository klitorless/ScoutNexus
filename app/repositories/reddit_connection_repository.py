"""Data access for Reddit OAuth connections.

Stage 4 keeps a single active local connection (no multi-user accounts);
the model is a small table rather than a hardcoded global token so later
stages can grow toward multi-user support without a rewrite.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import RedditConnection


class RedditConnectionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_active(self) -> RedditConnection | None:
        """The current connection, if any (most recently updated)."""
        return (
            self.session.query(RedditConnection)
            .order_by(RedditConnection.updated_at.desc())
            .first()
        )

    def upsert(
        self,
        reddit_username: str,
        access_token: str,
        refresh_token: str | None,
        token_expires_at: datetime | None,
        scopes: str,
    ) -> RedditConnection:
        """Create or update the connection for a Reddit username."""
        connection = (
            self.session.query(RedditConnection)
            .filter_by(reddit_username=reddit_username)
            .first()
        )
        if connection is None:
            connection = RedditConnection(reddit_username=reddit_username)
            self.session.add(connection)
        connection.access_token = access_token
        connection.refresh_token = refresh_token
        connection.token_expires_at = token_expires_at
        connection.scopes = scopes
        connection.updated_at = datetime.now()
        self.session.flush()
        return connection

    def delete(self, connection: RedditConnection) -> None:
        self.session.delete(connection)
        self.session.flush()
