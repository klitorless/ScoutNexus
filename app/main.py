"""ScoutNexus web application.

Routes:
    GET  /                        -> redirect to the opportunity inbox
    GET  /candidates              -> opportunity inbox (filterable by status)
    GET  /candidates/{id}         -> candidate detail
    POST /candidates/{id}/review  -> mark candidate REVIEWED
    POST /candidates/{id}/dismiss -> mark candidate DISMISSED
    GET  /campaigns               -> campaign list
    GET  /discovery               -> discovery console (configured targets)
    POST /discovery/run           -> run discovery manually (dev operation)
    GET  /sources                 -> discovered sources list
    GET  /sources/{id}            -> source detail
    GET  /auth/reddit             -> start Reddit OAuth (redirect to Reddit)
    GET  /auth/reddit/callback    -> Reddit OAuth callback (code exchange)
    POST /auth/reddit/disconnect  -> remove the Reddit OAuth connection

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
from app.models import Candidate, CandidateStatus, Source
from app.platforms import PlatformAdapter, RedditAdapter, RedditCredentialsError, RedditError, reddit_status
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.reddit_connection_repository import RedditConnectionRepository
from app.repositories.source_repository import SourceRepository
from app.services.discovery import DEFAULT_DISCOVERY_TARGETS, DiscoveryService
from app.services.reddit_oauth import (
    OAuthStateStore,
    RedditOAuthClient,
    RedditOAuthConfig,
    RedditOAuthConfigError,
    refresh_connection,
    token_needs_refresh,
)

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


def get_source_repo(db: Session = Depends(get_db)) -> SourceRepository:
    return SourceRepository(db)


def get_discovery_adapter() -> PlatformAdapter | None:
    """Reddit adapter for manual discovery runs, or None when unconfigured.

    Returns None instead of raising so the discovery console can explain
    missing credentials with a clean page rather than a 500.
    """
    try:
        return RedditAdapter()
    except RedditCredentialsError:
        return None


# Single-process server-side OAuth state store (see reddit_oauth.py).
_oauth_state_store = OAuthStateStore()


def get_oauth_config() -> RedditOAuthConfig | None:
    """User-OAuth config, or None when not configured (clean page, not a 500)."""
    try:
        return RedditOAuthConfig.from_env()
    except RedditOAuthConfigError:
        return None


def get_oauth_client(
    config: RedditOAuthConfig | None = Depends(get_oauth_config),
) -> RedditOAuthClient | None:
    return RedditOAuthClient(config) if config is not None else None


def get_oauth_state_store() -> OAuthStateStore:
    return _oauth_state_store


def get_connection_repo(db: Session = Depends(get_db)) -> RedditConnectionRepository:
    return RedditConnectionRepository(db)


def _oauth_setup_hint() -> str:
    return (
        "Register a *web* app at https://www.reddit.com/prefs/apps with redirect URI "
        "'http://127.0.0.1:8000/auth/reddit/callback' (it must match exactly), then "
        "copy .env.example to .env and set SCOUTNEXUS_REDDIT_CLIENT_ID, "
        "SCOUTNEXUS_REDDIT_CLIENT_SECRET and SCOUTNEXUS_REDDIT_USER_AGENT."
    )


def _reddit_account_context(
    db: Session,
    repo: RedditConnectionRepository,
    oauth_client: RedditOAuthClient | None,
    oauth_config: RedditOAuthConfig | None,
) -> dict:
    """Template context for the Reddit account section of the console."""
    context: dict = {
        "connected": False,
        "oauth_configured": oauth_config is not None,
    }
    connection = repo.get_active()
    if connection is None:
        return context
    context["connected"] = True
    context["username"] = connection.reddit_username
    context["scopes"] = connection.scopes
    context["token_state"] = "expired" if connection.is_expired() else "valid"
    # Minimal refresh behavior: refresh only at/near expiry, then persist.
    # A healthy token triggers no Reddit traffic at all.
    if token_needs_refresh(connection) and oauth_client is not None:
        try:
            connection = refresh_connection(connection, repo, oauth_client)
            db.commit()
            context["token_state"] = (
                "expired" if connection.is_expired() else "valid"
            )
        except RedditError:
            db.rollback()
    return context


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

    @app.get("/discovery")
    def discovery_console(
        request: Request,
        db: Session = Depends(get_db),
        adapter: PlatformAdapter | None = Depends(get_discovery_adapter),
        connection_repo: RedditConnectionRepository = Depends(get_connection_repo),
        oauth_client: RedditOAuthClient | None = Depends(get_oauth_client),
        oauth_config: RedditOAuthConfig | None = Depends(get_oauth_config),
    ):
        """Developer verification console: configured targets + manual run."""
        return templates.TemplateResponse(
            request,
            "discovery.html",
            {
                "targets": DEFAULT_DISCOVERY_TARGETS,
                "adapter_configured": adapter is not None,
                "reddit_account": _reddit_account_context(
                    db, connection_repo, oauth_client, oauth_config
                ),
                **_shared_context(),
            },
        )

    @app.post("/discovery/run")
    def discovery_run(
        request: Request,
        db: Session = Depends(get_db),
        adapter: PlatformAdapter | None = Depends(get_discovery_adapter),
    ):
        """Manual discovery run (dev operation — no scheduling, no workers).

        Persists discovered posts as Sources only. Never creates
        candidates and never runs campaign analysis.
        """
        if adapter is None:
            return templates.TemplateResponse(
                request,
                "discovery_result.html",
                {
                    "result": None,
                    "config_error": (
                        "Reddit is not configured. Copy .env.example to .env "
                        "and fill in REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET "
                        "and REDDIT_USER_AGENT, then run again."
                    ),
                    **_shared_context(),
                },
            )
        service = DiscoveryService(
            adapter, SourceRepository(db), CandidateRepository(db)
        )
        result = service.run_discovery(DEFAULT_DISCOVERY_TARGETS)
        db.commit()
        return templates.TemplateResponse(
            request,
            "discovery_result.html",
            {"result": result, "config_error": None, **_shared_context()},
        )

    @app.get("/sources")
    def source_list(
        request: Request,
        repo: SourceRepository = Depends(get_source_repo),
    ):
        sources = repo.list()
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"sources": sources, **_shared_context()},
        )

    @app.get("/sources/{source_id}")
    def source_detail(
        request: Request,
        source_id: int,
        repo: SourceRepository = Depends(get_source_repo),
    ):
        source: Source | None = repo.get(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return templates.TemplateResponse(
            request,
            "source_detail.html",
            {"source": source, **_shared_context()},
        )

    # -- Reddit OAuth account connection (Stage 4) ---------------------------

    def _auth_error(
        request: Request, message: str, detail: str | None, status_code: int
    ):
        return templates.TemplateResponse(
            request,
            "auth_error.html",
            {"message": message, "detail": detail, **_shared_context()},
            status_code=status_code,
        )

    @app.get("/auth/reddit")
    def reddit_auth_start(
        request: Request,
        config: RedditOAuthConfig | None = Depends(get_oauth_config),
        store: OAuthStateStore = Depends(get_oauth_state_store),
        repo: RedditConnectionRepository = Depends(get_connection_repo),
    ):
        """Start the OAuth flow: issue state server-side, redirect to Reddit."""
        if config is None:
            return _auth_error(
                request,
                "Reddit connection is not configured.",
                _oauth_setup_hint(),
                400,
            )
        if repo.get_active() is not None:
            return RedirectResponse(url="/discovery", status_code=303)
        state = store.issue()
        return RedirectResponse(
            url=RedditOAuthClient(config).authorization_url(state),
            status_code=302,
        )

    @app.get("/auth/reddit/callback")
    def reddit_auth_callback(
        request: Request,
        db: Session = Depends(get_db),
        client: RedditOAuthClient | None = Depends(get_oauth_client),
        store: OAuthStateStore = Depends(get_oauth_state_store),
        repo: RedditConnectionRepository = Depends(get_connection_repo),
    ):
        """Handle Reddit's redirect: validate state, exchange code, store."""
        params = request.query_params
        if client is None:
            return _auth_error(
                request,
                "Reddit connection is not configured.",
                _oauth_setup_hint(),
                400,
            )
        if params.get("error"):
            # e.g. access_denied — the user declined on Reddit's page.
            return _auth_error(
                request,
                "Reddit connection was not completed.",
                "Authorization was not granted on Reddit.",
                400,
            )
        code, state = params.get("code"), params.get("state")
        if not code or not state:
            return _auth_error(
                request,
                "Reddit connection was not completed.",
                "The callback was missing required parameters.",
                400,
            )
        if not store.consume(state):
            # Missing, mismatched, expired, or reused state: no exchange.
            return _auth_error(
                request,
                "Reddit connection was not completed.",
                "The authorization request could not be verified. "
                "Please try connecting again.",
                400,
            )
        try:
            tokens = client.exchange_code(code)
            # Identity comes from Reddit's authenticated endpoint, never
            # from callback query parameters.
            identity = client.fetch_identity(tokens.access_token)
        except RedditError as exc:
            # OAuth error messages are secret-free by construction.
            return _auth_error(
                request, "Reddit connection was not completed.", str(exc), 502
            )
        repo.upsert(
            reddit_username=identity["name"],
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )
        db.commit()
        return RedirectResponse(url="/discovery", status_code=303)

    @app.post("/auth/reddit/disconnect")
    def reddit_disconnect(
        db: Session = Depends(get_db),
        repo: RedditConnectionRepository = Depends(get_connection_repo),
    ):
        """Remove the stored Reddit connection (tokens are discarded)."""
        connection = repo.get_active()
        if connection is not None:
            repo.delete(connection)
            db.commit()
        return RedirectResponse(url="/discovery", status_code=303)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request, "404.html", _shared_context(), status_code=404
        )

    return app


app = create_app()
Base.metadata.create_all(engine)
