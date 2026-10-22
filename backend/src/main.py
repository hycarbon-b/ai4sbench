from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from httpx_oauth.clients.github import GitHubOAuth2
from pydantic import BaseModel, ConfigDict
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api_docs import install_scoped_docs
from src.community.account_email import router as account_email_router
from src.community.admin_routes import admin_router as community_admin_router
from src.community.ai_reviews import ai_review_router
from src.community.routes import community_router
from src.control_panel.api.v1.routes import admin_router, public_router, worker_router
from src.control_panel.api.v2.routes import router as harbor_v2_router
from src.control_panel.providers import EC2Provider, provider_from_settings
from src.core.config import Settings, get_settings
from src.core.identity import build_auth
from src.core.temporary_auth import admin_temporary_router, auth_temporary_router
from src.db.database import Base, create_database_engine, create_session_factory
from src.mailing.admin_routes import admin_mailing_router
from src.mailing.routes import mailing_router


class GitHubOAuth2WithAccountPicker(GitHubOAuth2):
    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str | None = None,
        scope: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        extras_params: dict[str, str] | None = None,
    ) -> str:
        return await super().get_authorization_url(
            redirect_uri,
            state,
            scope,
            code_challenge,
            code_challenge_method,
            extras_params={**(extras_params or {}), "prompt": "select_account"},
        )


STATIC_DIR = Path(__file__).with_name("static")
WEBSITE_DIST_DIR = Path(__file__).with_name("website_dist")
DOCUMENT_DIR = Path(__file__).with_name("document")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Unauthenticated liveness and database-readiness checks for load balancers.",
    },
    {
        "name": "authentication",
        "description": "GitHub OAuth endpoints. The browser Website starts at `/auth/github/start`.",
    },
    {
        "name": "community",
        "description": "Website sign-in, proposal previews and GitHub Discussion submission.",
    },
    {
        "name": "settings",
        "description": "Authenticated control-plane configuration visible to administrators.",
    },
    {
        "name": "dashboard",
        "description": "Aggregated data used by the Dashboard overview.",
    },
    {
        "name": "database",
        "description": "Administrator-only consistent SQLite snapshot creation and download.",
    },
    {
        "name": "tasks",
        "description": "Immutable benchmark task-revision registration and GitHub synchronization.",
    },
    {
        "name": "public proposals",
        "description": "Unauthenticated proposal, review and task-revision data for the Website board.",
    },
    {
        "name": "reviewers",
        "description": (
            "GitHub-authenticated reviewer applications plus administrator-only review and access decisions."
        ),
    },
    {
        "name": "plans",
        "description": "Execution-plan creation and optimistic-lock approval.",
    },
    {
        "name": "cloud profiles",
        "description": "Administrator-managed EC2 allocation profiles.",
    },
    {
        "name": "runs",
        "description": "Run queueing, cancellation and event inspection. Mutations accept `Idempotency-Key`.",
    },
    {
        "name": "operations",
        "description": ("Administrator view of asynchronous database-backed jobs and outbound deliveries."),
    },
    {
        "name": "mailing",
        "description": "Service-authenticated asynchronous email delivery creation, status and retry.",
    },
    {
        "name": "worker",
        "description": "Worker-agent lifecycle endpoints authenticated with each run's worker token.",
    },
    {
        "name": "AI reviews",
        "description": "Service-submitted AI reviews and publication recovery.",
    },
    {
        "name": "Harbor EC2 v2",
        "description": "Harbor CLI managed EC2 jobs, with independent v2 persistence and runner.",
    },
]


def create_app(settings: Settings | None = None, provider: EC2Provider | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = create_database_engine(resolved)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if resolved.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        yield
        await engine.dispose()

    app = FastAPI(
        title="AI4S-Bench Control Plane API",
        summary="Control plane for benchmark operations and community task proposals.",
        description=(
            "The AI4S-Bench Control Plane manages task revisions, execution plans, runs, "
            "worker callbacks, database snapshots, GitHub OAuth and scientific task proposals.\n\n"
            "Public documentation is available to inspect contracts. Endpoints marked with the "
            "cookie security scheme still require an authenticated Dashboard session; worker "
            "endpoints require their run-scoped token."
        ),
        version="0.3.0",
        contact={"name": "AI4S-Bench", "url": "https://ai4sbench.org"},
        license_info={"name": "MIT", "identifier": "MIT"},
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.provider = provider or provider_from_settings(resolved)
    auth_backend, fastapi_users = build_auth(resolved)
    app.state.auth_backend = auth_backend
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved.allowed_hosts))
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/document" or request.url.path.startswith("/document/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.include_router(public_router)
    app.include_router(admin_router)
    app.include_router(worker_router)
    ec2_routes = [*public_router.routes, *admin_router.routes, *worker_router.routes]
    app.include_router(harbor_v2_router)
    v2_routes = list(harbor_v2_router.routes)
    app.include_router(community_router)
    app.include_router(community_admin_router)
    app.include_router(admin_temporary_router)
    app.include_router(auth_temporary_router)
    app.include_router(ai_review_router)
    app.include_router(mailing_router)
    app.include_router(account_email_router)
    app.include_router(admin_mailing_router)
    mailing_routes = list(mailing_router.routes)
    community_routes = [
        *community_router.routes,
        *community_admin_router.routes,
        *admin_temporary_router.routes,
        *auth_temporary_router.routes,
        *account_email_router.routes,
        *ai_review_router.routes,
    ]
    if resolved.github_oauth_client_id and resolved.github_oauth_client_secret:
        github_oauth = GitHubOAuth2WithAccountPicker(
            resolved.github_oauth_client_id,
            resolved.github_oauth_client_secret.get_secret_value(),
            scopes=["read:user", "user:email", "public_repo"],
        )
        oauth_router = fastapi_users.get_oauth_router(
            github_oauth,
            auth_backend,
            resolved.auth_jwt_secret,
            associate_by_email=True,
            is_verified_by_default=True,
            csrf_token_cookie_secure=resolved.environment == "production",
        )
        app.include_router(oauth_router, prefix="/auth/github", tags=["authentication"])

        class AssociatedUser(BaseModel):
            model_config = ConfigDict(from_attributes=True)
            id: uuid.UUID
            email: str
            github_login: str | None

        associate_router = fastapi_users.get_oauth_associate_router(
            github_oauth,
            AssociatedUser,
            resolved.auth_jwt_secret,
            csrf_token_cookie_secure=resolved.environment == "production",
        )
        # A subpath of the existing callback keeps older OAuth App wildcard rules usable.
        app.include_router(
            associate_router, prefix="/auth/github/callback/associate", tags=["authentication"]
        )

        @app.get("/auth/github/connect", include_in_schema=False)
        async def github_connect_page() -> HTMLResponse:
            return HTMLResponse(
                """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Connect GitHub · AI4S-Bench</title><meta name="referrer" content="no-referrer">
<body style="margin:12vh auto;max-width:520px;padding:24px;color:#0d1730;font:16px Arial,sans-serif">
<h1>Connect GitHub</h1><p>Your AI4S-Bench account stays available without GitHub. Connect it only
when you want to publish a scientific review under your own GitHub identity.</p>
<p>If this GitHub identity belongs to another account, the connection is refused.</p>
<a href="/auth/github/associate/start">Continue with GitHub</a></body></html>"""
            )

        @app.get("/auth/github/associate/start", include_in_schema=False)
        async def github_associate_start() -> HTMLResponse:
            return HTMLResponse(
                """<!doctype html><title>Connecting GitHub</title>
<p>Connecting GitHub…</p><script>
fetch('/auth/github/callback/associate/authorize', {credentials: 'same-origin'})
  .then((response) => response.ok ? response.json() : Promise.reject(response))
  .then(({authorization_url}) => location.replace(authorization_url))
  .catch(() => { document.body.textContent = 'Could not connect GitHub. Close this window and try again.'; });
</script>"""
            )

        @app.get("/auth/github/start", include_in_schema=False)
        async def github_start() -> HTMLResponse:
            """Start OAuth in the Dashboard origin so its CSRF cookie is first-party."""

            return HTMLResponse(
                """<!doctype html><title>Opening GitHub sign-in</title>
<p>Opening GitHub sign-in…</p><script>
fetch('/auth/github/authorize', {credentials: 'same-origin'})
  .then((response) => response.ok ? response.json() : Promise.reject(response))
  .then(({authorization_url}) => location.replace(authorization_url))
  .catch(() => {
    document.body.textContent = 'Could not start GitHub sign-in. Close this window and try again.';
  });
</script>"""
            )

    @app.get("/auth/invite", include_in_schema=False, name="invite_page")
    async def invite_page() -> HTMLResponse:
        profile_url = "/website-preview/profile/"
        return HTMLResponse(
            """<!doctype html><html lang="en"><meta charset="utf-8"><title>AI4S-Bench sign in</title>
<meta name="referrer" content="no-referrer">
<body style="margin:0;background:#f7f9fc;color:#0d1730;font:16px Arial,sans-serif">
<main style="max-width:520px;margin:12vh auto;padding:32px;background:white;border:1px solid #dde5f0">
<strong style="display:block;margin-bottom:22px;color:#2474ff">AI4S-Bench</strong>
<h1 style="font-size:24px">Signing you in</h1><p id="state">Checking your personal link…</p></main>
<script>
const token = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
if (!token) document.getElementById('state').textContent = 'This sign-in link is missing its token.';
else fetch('/api/v1/auth/invites/redeem', {method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/json'}, body:JSON.stringify({token})})
  .then(r => {if (!r.ok) throw Error('This link is invalid, expired, or already used. Request a new link.');
    location.replace(PROFILE_URL);})
  .catch(e => {document.getElementById('state').textContent=e.message;});
</script></body></html>""".replace("PROFILE_URL", json.dumps(profile_url))
        )

    install_scoped_docs(
        app,
        ec2_routes=ec2_routes,
        v2_routes=v2_routes,
        community_routes=community_routes,
        mailing_routes=mailing_routes,
    )

    @app.get("/document", include_in_schema=False)
    async def document_root() -> RedirectResponse:
        return RedirectResponse("/document/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    app.mount("/document", StaticFiles(directory=DOCUMENT_DIR, html=True), name="document")

    @app.get("/website-preview", include_in_schema=False)
    async def website_preview_root() -> RedirectResponse:
        return RedirectResponse(
            "/website-preview/", status_code=status.HTTP_307_TEMPORARY_REDIRECT
        )

    app.mount(
        "/website-preview",
        StaticFiles(directory=WEBSITE_DIST_DIR, html=True),
        name="website_preview",
    )
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="admin")
    return app


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("src.main:create_app", host=settings.host, port=settings.port, factory=True)


if __name__ == "__main__":
    run()
