from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.shared.exceptions import register_exception_handlers
from app.shared.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI application factory."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Company Brain - Organizational Memory and Intelligence Platform",
        docs_url="/docs" if settings.debug or settings.environment != "production" else None,
        redoc_url="/redoc" if settings.debug or settings.environment != "production" else None,
        lifespan=lifespan,
    )

    # Register minimal exception handlers
    register_exception_handlers(app)

    # Include API v1 router
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
