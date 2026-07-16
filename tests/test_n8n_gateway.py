from __future__ import annotations

from typing import Any

import pytest

from app.gateways.n8n import N8nGateway
from app.schemas import AnalyzeRequest


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


def _patch_http_client(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> None:
    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(data)

    monkeypatch.setattr("app.gateways.n8n.httpx.AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_analyze_normalizes_null_webhook_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http_client(
        monkeypatch,
        {
            "request_id": "from-n8n",
            "status": "success",
            "summary": "UGC10214 es una galaxia.",
            "results": None,
            "artifacts": None,
            "warnings": None,
        },
    )
    gateway = N8nGateway("https://example.test/webhook")

    response = await gateway.analyze(
        AnalyzeRequest(request_id="req-1", message="que es UGC10214?")
    )

    assert response.request_id == "from-n8n"
    assert response.status == "success"
    assert response.summary == "UGC10214 es una galaxia."
    assert response.results == {}
    assert response.artifacts == []
    assert response.warnings == []


@pytest.mark.asyncio
async def test_stream_normalizes_null_webhook_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http_client(
        monkeypatch,
        {
            "request_id": "from-n8n",
            "status": "success",
            "summary": "UGC10214 es una galaxia.",
            "results": None,
            "artifacts": None,
            "warnings": None,
        },
    )
    gateway = N8nGateway("https://example.test/webhook")

    chunks = [
        chunk
        async for chunk in gateway.analyze_stream(
            AnalyzeRequest(request_id="req-1", message="que es UGC10214?")
        )
    ]
    stream = b"".join(chunks).decode()

    assert "event: summary" in stream
    assert "UGC10214 es una galaxia." in stream
    assert "event: end" in stream
    assert '"status": "success"' in stream
