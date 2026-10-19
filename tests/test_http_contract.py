"""HTTP contracts for the BFF, independent of OpenAI, n8n and Galaxy API."""

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.schemas import AnalyzeResponse

STREAM = b'event: end\ndata: {"type":"end","status":"success"}\n\n'


class _Gateway:
    async def analyze(self, request):
        return AnalyzeResponse(
            request_id=request.request_id, status="success", summary="Contract response"
        )

    async def analyze_stream(self, request):
        yield STREAM


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app import config, main, usage_log

    monkeypatch.setenv("ORCHESTRATOR_MODE", "direct")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    config._settings = None
    importlib.reload(main)

    def init():
        main._galaxy_gateway = _Gateway()
        main._observation_gateway = _Gateway()

    monkeypatch.setattr(main, "_init_gateways", init)
    monkeypatch.setattr(usage_log, "send_event", lambda *args: None)
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
        config._settings = None


def test_health_contract(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_analysis_contract(client: TestClient) -> None:
    response = client.post("/analyze", json={"request_id": "contract", "message": "M51"})
    assert response.status_code == 200
    assert response.json()["request_id"] == "contract"
    assert response.json()["status"] == "success"


def test_analysis_stream_contract(client: TestClient) -> None:
    response = client.post("/analyze/stream", json={"request_id": "contract", "message": "M51"})
    assert response.status_code == 200
    assert response.content == STREAM
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"


def test_analysis_invalid_request_contract(client: TestClient) -> None:
    assert client.post("/analyze", json={"message": "M51"}).status_code == 422


def test_cors_preflight_contract(client: TestClient) -> None:
    response = client.options(
        "/analyze",
        headers={"origin": "https://app.example.com", "access-control-request-method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
