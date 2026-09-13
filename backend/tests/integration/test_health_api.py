from starlette.testclient import TestClient

from app.main import create_app
from app.shared.exceptions import AppException


def test_health_check_endpoint(client: TestClient):
    """Verify GET /api/v1/health returns 200 with expected schema fields."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "0.1.0"
    assert data["environment"] == "testing"
    assert "timestamp" in data


def test_openapi_schema(client: TestClient):
    """Verify OpenAPI JSON specification is generated properly."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Company Brain (Test)"
    assert "/api/v1/health" in schema["paths"]


def test_custom_exception_handler():
    """Verify AppException returns expected JSON format through FastAPI handler."""
    app = create_app()

    @app.get("/test-error")
    def trigger_error():
        raise AppException(message="Test domain failure", status_code=400, details={"reason": "test"})

    with TestClient(app) as test_client:
        response = test_client.get("/test-error")
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["message"] == "Test domain failure"
        assert data["error"]["details"] == {"reason": "test"}
