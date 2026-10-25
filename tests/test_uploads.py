import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.gateways.base import AnalysisGateway
from app.schemas import AnalyzeRequest, UploadedImage
from app.usage_log import received_event


def test_gateway_preserves_uploaded_bytes_and_selected_hdu():
    request = AnalyzeRequest(
        request_id="upload",
        message="CAS",
        uploaded_image=UploadedImage(filename="source.fits", data="privatepixels", hdu_index=2),
    )
    body = AnalysisGateway._body(request)
    assert body["uploaded_image"] == {
        "filename": "source.fits",
        "data": "privatepixels",
        "hdu_index": 2,
        "error_kind": "sigma",
        "uncertainty_samples": 0,
    }
    event = received_event(request, "galaxy", None)
    assert event["has_image"] is True
    assert "privatepixels" not in repr(event) and "source.fits" not in repr(event)
    assert "privatepixels" not in repr(request)


@pytest.mark.parametrize("mode", ["direct", "n8n"])
def test_file_requests_are_never_sent_to_planning_or_a_webhook(monkeypatch, mode):
    from app import main

    galaxy, planning = object(), object()
    monkeypatch.setattr(main, "_galaxy_gateway", galaxy)
    monkeypatch.setattr(main, "_observation_gateway", planning)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(orchestrator_mode=mode))
    request = AnalyzeRequest(
        request_id="upload",
        message="Analiza esta imagen",
        uploaded_image=UploadedImage(filename="source.png", data="AAAA"),
    )
    assert asyncio.run(main._select_gateway(request)) is galaxy


def test_auto_mode_classifies_upload_context_but_planning_never_receives_bytes(monkeypatch):
    from app import main
    from app.gateways.n8n import N8nGateway
    from unittest.mock import AsyncMock

    galaxy, planning = object(), object()
    classifier = SimpleNamespace(classify=AsyncMock(return_value="observation_planning"))
    monkeypatch.setattr(main, "_galaxy_gateway", galaxy)
    monkeypatch.setattr(main, "_observation_gateway", planning)
    monkeypatch.setattr(main, "_classifier", classifier)
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(orchestrator_mode="auto"))
    request = AnalyzeRequest(
        request_id="planning",
        message="Planifica M81 desde Madrid",
        uploaded_image=UploadedImage(filename="source.png", data="privatepixels"),
    )
    assert asyncio.run(main._select_gateway(request)) is planning
    assert classifier.classify.call_args.kwargs["has_uploaded_image"] is True
    assert "privatepixels" not in repr(classifier.classify.call_args)
    assert "uploaded_image" not in N8nGateway._body(request)


def test_invalid_upload_metadata_is_rejected():
    with pytest.raises(ValidationError):
        UploadedImage(filename="image.svg", data="AAAA")
    with pytest.raises(ValidationError):
        UploadedImage(filename="image.png", data="A" * 11184913)
    assert UploadedImage(filename=r"C:\private\a.fits", data="AAAA").filename == "a.fits"


def test_request_limit_applies_before_gateway_selection(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_init_gateways", lambda: None)
    with TestClient(main.app) as client:
        response = client.post("/analyze/stream", content=b"x" * (12 * 1024 * 1024 + 1))
    assert response.status_code == 413
