from datetime import datetime, timezone
from fastapi import APIRouter

from app.dependencies import SettingsDep
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the current operational status of the service.",
)
async def health_check(settings: SettingsDep) -> HealthResponse:
    """Check application health and return status information."""
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        environment=settings.environment,
        timestamp=datetime.now(timezone.utc),
    )
