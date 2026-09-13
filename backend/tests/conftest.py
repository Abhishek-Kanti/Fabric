from typing import Generator
import pytest
from starlette.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing testing settings."""
    return Settings(
        app_name="Company Brain (Test)",
        environment="testing",
        debug=True,
        log_level="WARNING",
    )


@pytest.fixture
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    """Fixture providing a TestClient configured with test settings."""
    app = create_app(settings=test_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as test_client:
        yield test_client
