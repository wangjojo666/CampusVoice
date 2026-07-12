from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_service_metadata() -> None:
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "CampusVoice API",
        "version": "0.1.0",
        "environment": "development",
    }


def test_root_health_is_available_for_process_probes() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
