"""Domain models: Campaign, Source, Candidate, RedditConnection."""

from app.models.campaign import OFFER_TYPES, Campaign
from app.models.candidate import Candidate, CandidateStatus, Confidence
from app.models.reddit_connection import RedditConnection
from app.models.source import Source

__all__ = [
    "OFFER_TYPES",
    "Campaign",
    "Candidate",
    "CandidateStatus",
    "Confidence",
    "RedditConnection",
    "Source",
]
