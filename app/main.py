"""ScoutNexus web application.

Routes:
    GET  /                        -> redirect to the opportunity inbox
    GET  /candidates              -> opportunity inbox (filterable by status)
    GET  /candidates/{id}         -> candidate detail
    POST /candidates/{id}/review  -> mark candidate REVIEWED
    POST /candidates/{id}/dismiss -> mark candidate DISMISSED
    GET  /campaigns               -> campaign list

The web layer only talks to the repositories/services — never directly to
the database engine, and never to platform APIs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, make_engine, make_session_factory
from app.models import Candidate, CandidateStatus
from app.platforms import reddit_status
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.campaign_repository import CampaignRepository

APP_DIR = Path(__file__).resolve().parent

engine = make_engine(settings.database_url)
SessionLocal = make_session_factory(engine)

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.filters["timeago"] = lambda value: timeago(value)


def timeago(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    seconds = int((datetime.now() - value).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{days // 365}y ago"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_candidate_repo(db: Session = Depends(get_db)) -> CandidateRepository:
    return CandidateRepository(db)


def get_campaign_repo(db: Session = Depends(get_db)) -> CampaignRepository:
    return CampaignRepository(db)


def _shared_context() -> dict:
    """Template variables needed on every page."""
    return {"demo_mode": settings.demo_mode, "reddit_status": reddit_status()}


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse(url="/candidates", status_code=303)

    @app.get("/candidates")
    def candidate_inbox(
        request: Request,
        status: str | None = None,
        repo: CandidateRepository = Depends(get_candidate_repo),
    ):
        status_filter: CandidateStatus | None = None
        if status:
            try:
                status_filter = CandidateStatus[status.upper()]
            except KeyError:
                raise HTTPException(status_code=400, detail="Unknown status filter")
        candidates = repo.list(status=status_filter)
        counts = repo.count_by_status()
        return templates.TemplateResponse(
            request,
            "candidates.html",
            {
                "candidates": candidates,
                "counts": counts,
                "active_filter": status_filter.value if status_filter else "ALL",
                "statuses": [s.value for s in CandidateStatus],
                **_shared_context(),
            },
        )

    @app.get("/candidates/{candidate_id}")
    def candidate_detail(
        request: Request,
        candidate_id: int,
        repo: CandidateRepository = Depends(get_candidate_repo),
    ):
        candidate: Candidate | None = repo.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return templates.TemplateResponse(
            request,
            "candidate_detail.html",
            {"candidate": candidate, **_shared_context()},
        )

    @app.post("/candidates/{candidate_id}/review")
    def review_candidate(
        candidate_id: int,
        db: Session = Depends(get_db),
        repo: CandidateRepository = Depends(get_candidate_repo),
    ):
        candidate = repo.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        repo.mark_reviewed(candidate)
        db.commit()
        return RedirectResponse(url=f"/candidates/{candidate_id}", status_code=303)

    @app.post("/candidates/{candidate_id}/dismiss")
    def dismiss_candidate(
        candidate_id: int,
        db: Session = Depends(get_db),
        repo: CandidateRepository = Depends(get_candidate_repo),
    ):
        candidate = repo.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        repo.mark_dismissed(candidate)
        db.commit()
        return RedirectResponse(url="/candidates", status_code=303)

    @app.get("/campaigns")
    def campaign_list(
        request: Request,
        repo: CampaignRepository = Depends(get_campaign_repo),
    ):
        campaigns = repo.list(active_only=False)
        return templates.TemplateResponse(
            request,
            "campaigns.html",
            {"campaigns": campaigns, **_shared_context()},
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request, "404.html", _shared_context(), status_code=404
        )

    return app


app = create_app()
Base.metadata.create_all(engine)
