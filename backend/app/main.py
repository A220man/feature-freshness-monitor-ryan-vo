"""Production application factory: uvicorn app.main:create_app --factory."""
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.features import create_router
from app.api.advisory import create_advisory_router
from app.services.llm_advisory import LLMSettings
from app.security.oidc import OIDCAuthenticator, OIDCSettings
from app.services.freshness_store import FreshnessStore


@dataclass(frozen=True)
class Settings:
    identity: OIDCSettings
    database_path: Path
    frontend_origin: str = ""
    llm: LLMSettings | None = None

    @classmethod
    def from_environment(cls):
        required = ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL")
        if any(not os.environ.get(name) for name in required):
            raise ValueError("Configure OIDC_ISSUER, OIDC_AUDIENCE and OIDC_JWKS_URL before starting")
        llm = None
        if os.environ.get("LLM_MODEL"):
            provider = os.environ.get("LLM_PROVIDER", "openai")
            defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1",
                        "gemini": "https://generativelanguage.googleapis.com/v1beta", "ollama": "http://127.0.0.1:11434"}
            base_url = os.environ.get("LLM_BASE_URL", defaults.get(provider, ""))
            llm = LLMSettings(provider, os.environ["LLM_MODEL"], base_url, os.environ.get("LLM_API_KEY", ""))
        return cls(OIDCSettings(*(os.environ[name] for name in required), allow_local_http=os.environ.get("OIDC_LOCAL_HTTP") == "true"),
                   Path(os.environ.get("DATABASE_PATH", "data/freshness.sqlite")), os.environ.get("FRONTEND_ORIGIN", ""), llm)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    store = FreshnessStore(settings.database_path)
    authenticate = OIDCAuthenticator(settings.identity)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(title="Feature Freshness Monitor", version="1.0.0", lifespan=lifespan)
    if settings.frontend_origin:
        app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type"])
    app.include_router(create_router(store, authenticate))
    app.include_router(create_advisory_router(store, authenticate, settings.llm))

    @app.get("/healthz", tags=["operations"])
    def health():
        with store.connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.get("/api/me", tags=["identity"])
    def identity(principal=Depends(authenticate)):
        return {"subject": principal.subject, "roles": sorted(principal.roles)}

    return app
