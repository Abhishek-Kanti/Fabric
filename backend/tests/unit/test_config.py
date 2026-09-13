from app.config import Settings
from app.shared.exceptions import AppException


def test_default_settings():
    """Verify default settings values."""
    settings = Settings()
    assert settings.app_name == "Company Brain"
    assert settings.environment == "development"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.port == 8000
    assert settings.debug is False


def test_settings_override():
    """Verify settings can be initialized with custom values."""
    settings = Settings(
        app_name="Custom Brain",
        environment="testing",
        debug=True,
        port=9000,
    )
    assert settings.app_name == "Custom Brain"
    assert settings.environment == "testing"
    assert settings.debug is True
    assert settings.port == 9000


def test_app_exception():
    """Verify AppException carries message, status code, and details."""
    exc = AppException(message="Resource invalid", status_code=422, details={"field": "test"})
    assert exc.message == "Resource invalid"
    assert exc.status_code == 422
    assert exc.details == {"field": "test"}
