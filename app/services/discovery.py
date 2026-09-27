"""Discovery service: adapter -> normalized sources -> persisted sources.

Two separate concerns, kept explicit:

1. discover_sources(): platform discovery -> stored Source rows.
   Needs no campaign. A Source is "a thing someone posted somewhere".

2. create_candidate_for_source(): evaluate one stored Source for one
   Campaign -> Candidate. A Candidate is "a source that may represent an
   opportunity for a particular campaign". Discovering a source must NOT
   inherently mean creating a candidate.

The deterministic keyword analysis below is Stage 1 scaffolding. A future
stage replaces it with real relevance analysis; the surrounding pipeline
does not change.

The service only talks to the PlatformAdapter interface — it knows nothing
about Reddit's API (or any platform's API).
"""

from __future__ import annotations

from app.models import Campaign, Candidate, Confidence, Source
from app.platforms.base import NormalizedSource, PlatformAdapter
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.source_repository import SourceRepository

# Deterministic keyword signals used by the Stage 1 mock analysis.
_HIGH_SIGNALS = (
    "looking for",
    "alternatives to",
    "recommend",
    "recommendation",
    "suggest",
    "anyone using",
    "what is everyone",
    "what's everyone",
)
_MEDIUM_SIGNALS = (
    "ai",
    "assistant",
    "coding",
    "developer",
    "tool",
    "invite",
    "referral",
)


def analyze_source_for_campaign(
    source: NormalizedSource, campaign: Campaign
) -> dict:
    """Deterministic mock analysis of a source for a campaign.

    Returns the reasoning fields for a Candidate. Pure function — no I/O,
    no network, no AI. Confidence is a coarse HIGH/MEDIUM/LOW bucket with
    the matched evidence spelled out, never a fake precise percentage.
    """
    text = f"{source.title or ''}\n{source.content or ''}".lower()
    high_hits = [s for s in _HIGH_SIGNALS if s in text]
    medium_hits = [s for s in _MEDIUM_SIGNALS if s in text]

    if high_hits:
        confidence = Confidence.HIGH
        reason = (
            "The author explicitly asks for recommendations or alternatives "
            f"({', '.join(high_hits)})."
        )
    elif medium_hits:
        confidence = Confidence.MEDIUM
        reason = (
            "The conversation is about a related topic "
            f"({', '.join(medium_hits[:3])}) but has no explicit ask."
        )
    else:
        confidence = Confidence.LOW
        reason = "Only a loose topical overlap; no clear signal of need."

    community = f"r/{source.community}" if source.community else source.platform
    return {
        "user_need": (
            f"Participants in {community} are discussing: "
            f"\"{source.title or '(untitled)'}\". "
            "They appear to be evaluating tools or looking for advice."
        ),
        "why_found": (
            f"Keyword match during discovery. {reason} "
            f"Evidence: title/content contained {high_hits + medium_hits[:3] or ['(none)']}."
        ),
        "potential_connection": (
            f"The '{campaign.name}' campaign ({campaign.offer_type} for "
            f"{campaign.service}) may be relevant to this conversation, since "
            "participants are actively comparing options. A human should judge "
            "fit before any response is drafted."
        ),
        "conversation_context": (source.content or "")[:400],
        "confidence": confidence,
        "evidence_quality": (
            f"{'Strong' if high_hits else 'Moderate' if medium_hits else 'Weak'}: "
            f"{len(high_hits)} explicit-ask signals, {len(medium_hits)} topical "
            "signals matched deterministically (Stage 1 mock analysis — no AI)."
        ),
    }


def source_to_normalized(source: Source) -> NormalizedSource:
    """Rebuild the platform-agnostic view of a persisted source."""
    return NormalizedSource(
        platform=source.platform,
        source_id=source.source_id,
        community=source.community,
        author=source.author,
        title=source.title,
        content=source.content,
        url=source.url,
        posted_at=source.posted_at,
        engagement=dict(source.engagement) if source.engagement else {},
    )


class DiscoveryService:
    """Separates source discovery from campaign evaluation."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        source_repo: SourceRepository,
        candidate_repo: CandidateRepository,
    ):
        self.adapter = adapter
        self.source_repo = source_repo
        self.candidate_repo = candidate_repo

    def discover_sources(
        self,
        queries: list[str],
        limit_per_query: int = 10,
    ) -> list[Source]:
        """Discover posts and persist them as Sources. No campaign required.

        Idempotent: re-running the same queries will not duplicate sources
        for the same (platform, source_id) pair.
        """
        sources: list[Source] = []
        seen_ids: set[int] = set()
        for query in queries:
            for normalized in self.adapter.discover(query, limit=limit_per_query):
                source, _ = self.source_repo.upsert(
                    platform=normalized.platform,
                    source_id=normalized.source_id,
                    community=normalized.community,
                    author=normalized.author,
                    title=normalized.title,
                    content=normalized.content,
                    url=normalized.url,
                    posted_at=normalized.posted_at,
                    engagement=normalized.engagement,
                )
                if source.id not in seen_ids:
                    seen_ids.add(source.id)
                    sources.append(source)
        return sources

    def create_candidate_for_source(
        self,
        source: Source,
        campaign: Campaign,
    ) -> Candidate | None:
        """Evaluate one stored source for one campaign.

        Returns the new Candidate, or None if this (campaign, source) pair
        already has one. Evaluation is a separate, explicit step — discovery
        alone never creates candidates.
        """
        if self.candidate_repo.get_for_campaign_source(campaign.id, source.id):
            return None
        analysis = analyze_source_for_campaign(
            source_to_normalized(source), campaign
        )
        return self.candidate_repo.create(
            campaign_id=campaign.id,
            source_id=source.id,
            **analysis,
        )

    def discover_for_campaign(
        self,
        campaign: Campaign,
        queries: list[str],
        limit_per_query: int = 10,
    ) -> list[Candidate]:
        """Convenience: discover sources, then evaluate each for the campaign.

        Idempotent: re-running the same queries creates nothing new.
        """
        candidates: list[Candidate] = []
        for source in self.discover_sources(queries, limit_per_query):
            candidate = self.create_candidate_for_source(source, campaign)
            if candidate is not None:
                candidates.append(candidate)
        return candidates
