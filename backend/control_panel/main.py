from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from httpx_oauth.clients.github import GitHubOAuth2
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import admin_router, public_router, worker_router
from .community import community_router
from .config import Settings, get_settings
from .database import Base, create_database_engine, create_session_factory
from .identity import AuthBase, build_auth, create_auth_engine
from .providers import EC2Provider, provider_from_settings

STATIC_DIR = Path(__file__).with_name("static")
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
        "name": "plans",
        "description": "Execution-plan creation and optimistic-lock approval.",
    },
    {
        "name": "runs",
        "description": "Run queueing, cancellation and event inspection. Mutations accept `Idempotency-Key`.",
    },
    {
        "name": "operations",
        "description": "Administrator view of asynchronous database-backed jobs.",
    },
    {
        "name": "worker",
        "description": "Worker-agent lifecycle endpoints authenticated with each run's worker token.",
    },
]


def create_app(settings: Settings | None = None, provider: EC2Provider | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = create_database_engine(resolved)
    session_factory = create_session_factory(engine)
    auth_engine = create_auth_engine(resolved)
    auth_session_factory = async_sessionmaker(auth_engine, expire_on_commit=False)
    if resolved.auto_create_schema:
        Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if resolved.auto_create_schema:
            async with auth_engine.begin() as connection:
                await connection.run_sync(AuthBase.metadata.create_all)
        yield
        engine.dispose()
        await auth_engine.dispose()

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
    app.state.auth_engine = auth_engine
    app.state.auth_session_factory = auth_session_factory
    app.state.provider = provider or provider_from_settings(resolved)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved.allowed_hosts))
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
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
        return response

    app.include_router(public_router)
    app.include_router(admin_router)
    app.include_router(worker_router)
    app.include_router(community_router)
    auth_backend, fastapi_users = build_auth(resolved)
    if resolved.github_oauth_client_id and resolved.github_oauth_client_secret:
        github_oauth = GitHubOAuth2(
            resolved.github_oauth_client_id,
            resolved.github_oauth_client_secret.get_secret_value(),
            scopes=["read:user", "user:email", "public_repo"],
        )
        app.include_router(
            fastapi_users.get_oauth_router(
                github_oauth,
                auth_backend,
                resolved.auth_jwt_secret,
                associate_by_email=True,
                is_verified_by_default=True,
                csrf_token_cookie_secure=resolved.environment == "production",
            ),
            prefix="/auth/github",
            tags=["authentication"],
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
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="admin")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("control_panel.main:app", host=settings.host, port=settings.port, factory=False)


if __name__ == "__main__":
    run()
