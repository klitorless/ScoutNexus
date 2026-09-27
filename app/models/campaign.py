"""Campaign / offer model.

A campaign is platform-independent: it describes an offer (invite, referral,
discount, ...) that OpportunityScout tries to match against conversations
found on any platform. Muse is just the first campaign; the model knows
nothing Muse-specific.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base

# Extensible set of supported offer types. Add new types here as the product
# grows; nothing else in the codebase branches on specific values.
OFFER_TYPES = frozenset(
    {
        "invite",
        "referral",
        "discount",
        "promo",
        "coupon",
        "beta_invite",
        "free_trial",
        "referral_link",
    }
)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service: Mapped[str] = mapped_column(String(200), nullable=False)
    offer_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Demo/test values only in Stage 1. Real codes are entered via
    # configuration/UI in a later stage — never hardcode them here.
    code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    disclosure: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now, nullable=False)

    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate", back_populates="campaign", cascade="all, delete-orphan"
    )

    @validates("offer_type")
    def _validate_offer_type(self, key: str, value: str) -> str:
        if value not in OFFER_TYPES:
            raise ValueError(
                f"Unknown offer_type {value!r}. Supported types: {sorted(OFFER_TYPES)}"
            )
        return value

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Campaign id={self.id} name={self.name!r} offer_type={self.offer_type!r}>"
