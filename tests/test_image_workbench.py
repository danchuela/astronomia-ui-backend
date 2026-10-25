import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateways.base import AnalysisGateway
from app.gateways.direct import DirectGalaxyGateway
from app.gateways.n8n import N8nGateway
from app.schemas import AnalyzeRequest, UploadedImage


def test_extended_upload_is_forwarded_to_science_but_never_to_n8n():
    request = AnalyzeRequest(
        request_id="selection",
        message="CAS",
        uploaded_image={
            "filename": "science.fits",
            "data": "AAAA",
            "source_point": {"x": 0.3, "y": 0.7},
            "region": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9},
            "psf_hdu_index": 2,
            "error_hdu_index": 3,
            "error_kind": "variance",
            "uncertainty_samples": 16,
        },
    )
    body = AnalysisGateway._body(request)["uploaded_image"]
    assert body["psf_hdu_index"] == 2 and body["error_kind"] == "variance"
    assert body["source_point"] == {"x": 0.3, "y": 0.7}
    assert "uploaded_image" not in N8nGateway._body(request)


def test_inspection_uses_direct_gateway_not_classifier_or_planning(monkeypatch):
    from app import main

    inspect = AsyncMock(return_value=httpx.Response(200, json={"width": 128}))
    monkeypatch.setattr(main, "_init_gateways", lambda: None)
    monkeypatch.setattr(main, "_galaxy_gateway", SimpleNamespace(inspect_image=inspect))
    with TestClient(main.app) as client:
        response = client.post(
            "/images/inspect", json={"filename": "source.fits", "data": "AAAA"}
        )
        assert response.status_code == 200 and response.json() == {"width": 128}
        assert (
            client.post(
                "/images/inspect", content=b"x" * (12 * 1024 * 1024 + 1)
            ).status_code
            == 413
        )
    inspect.assert_awaited_once()


def test_inspection_preserves_validation_and_capacity_codes(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_init_gateways", lambda: None)
    with TestClient(main.app) as client:
        for status in (408, 422, 503):
            monkeypatch.setattr(
                main,
                "_galaxy_gateway",
                SimpleNamespace(
                    inspect_image=AsyncMock(
                        return_value=httpx.Response(
                            status, json={"detail": {"code": "test"}}
                        )
                    )
                ),
            )
            response = client.post(
                "/images/inspect", json={"filename": "source.fits", "data": "AAAA"}
            )
            assert response.status_code == status


def test_capacity_response_is_not_reported_as_a_broken_science_module(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(503)

    monkeypatch.setattr("app.gateways.direct.httpx.AsyncClient", Client)
    request = AnalyzeRequest(request_id="busy", message="CAS", language="es")
    response = asyncio.run(DirectGalaxyGateway("http://science").analyze(request))
    assert response.status == "error" and "otro análisis" in response.summary
    assert "problemas" not in response.summary


@pytest.mark.parametrize("idle,total", [(0.01, 1.0), (1.0, 0.01)])
def test_incomplete_upload_releases_bff_capacity(monkeypatch, idle, total):
    from app import body_limit
    from app.capacity import RequestCapacity

    monkeypatch.setattr(body_limit, "BODY_IDLE_SECONDS", idle)
    monkeypatch.setattr(body_limit, "BODY_TOTAL_SECONDS", total)

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = RequestCapacity(body_limit.AnalysisBodyLimit(app))

    async def check():
        messages = []

        async def send(message):
            messages.append(message)

        async def incomplete():
            await asyncio.sleep(3600)

        async def complete():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        scope = {"type": "http", "method": "POST", "path": "/images/inspect"}
        await wrapped(scope, incomplete, send)
        assert messages[0]["status"] == 408 and wrapped._slots._value == 4
        messages.clear()
        await wrapped(scope, complete, send)
        assert messages[0]["status"] == 200

    asyncio.run(check())
