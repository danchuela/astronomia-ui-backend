"""Tests for the usage log (app/usage_log.py).

What must hold:
- Every /analyze/stream request sends a "received" and a "completed" event.
- The stream reaches the user byte-for-byte unchanged.
- The final status and object_name are read from the SSE "end" event,
  even when the event arrives split across chunks.
- If the log webhook fails or is not configured, the user's answer is unaffected.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import usage_log
from app.schemas import AnalyzeRequest

LOG_URL = "https://n8n.example.com/webhook/log"


def _sse(name: str, payload: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


# ---------------------------------------------------------------- unit tests


def test_sniffer_reads_end_event_split_across_chunks():
    end = _sse("end", {"type": "end", "status": "success", "object_name": "M51"})
    sniffer = usage_log.EndEventSniffer()
    sniffer.feed(_sse("status", {"type": "status", "message": "Procesando…"}))
    sniffer.feed(end[:17])  # event cut in the middle of the JSON
    assert sniffer.end_payload is None
    sniffer.feed(end[17:])
    assert sniffer.end_payload == {"type": "end", "status": "success", "object_name": "M51"}


def test_sniffer_ignores_garbage():
    sniffer = usage_log.EndEventSniffer()
    sniffer.feed(b"\xff\xfe not an event\n\n")
    sniffer.feed("event: end\ndata: {broken json\n\n")
    assert sniffer.end_payload == {}


def test_received_event_contents():
    req = AnalyzeRequest(
        request_id="r1",
        messages=[
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "¿qué querés ver?"},
            {"role": "user", "content": "analiza M51"},
        ],
        target={"name": "M51"},
        view_ra_deg=202.47,
        image_data="base64...",
    )
    ev = usage_log.received_event(req, "galaxy", "Mozilla/5.0")
    assert ev["event"] == "received"
    assert ev["user_message"] == "analiza M51"
    assert ev["conversation_turn"] == 4
    assert ev["gateway"] == "galaxy"
    assert ev["object_name"] == "M51"
    assert ev["target"] == {"name": "M51", "view_ra_deg": 202.47}
    assert ev["has_image"] is True
    assert ev["user_agent"] == "Mozilla/5.0"


def test_send_event_is_noop_without_url(monkeypatch):
    from app import config

    monkeypatch.delenv("N8N_LOG_WEBHOOK_URL", raising=False)
    config._settings = None
    called = []
    monkeypatch.setattr(usage_log, "_post_event", lambda *a: called.append(a))

    async def run():
        usage_log.send_event({"event": "received"})

    asyncio.run(run())
    assert called == []
    config._settings = None


def test_post_event_never_raises(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, *a, **k):
            raise httpx.ConnectError("n8n down")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    asyncio.run(usage_log._post_event(LOG_URL, {"event": "received"}))  # must not raise


# ---------------------------------------------------------- endpoint tests


class _FakeGateway:
    """Stands in for DirectGalaxyGateway / N8nGateway."""

    def __init__(self, chunks: list[bytes], fail: bool = False) -> None:
        self.chunks = chunks
        self.fail = fail

    async def analyze_stream(self, request):  # noqa: ARG002
        for c in self.chunks:
            yield c
        if self.fail:
            raise RuntimeError("upstream exploded")


@pytest.fixture
def app_with(monkeypatch):
    """Build the app with a fake gateway and capture the log events sent."""

    def _build(gateway: _FakeGateway, log_url: str | None = LOG_URL):
        monkeypatch.setenv("ORCHESTRATOR_MODE", "direct")
        monkeypatch.setenv("GALAXY_API_URL", "http://localhost:8000")
        if log_url:
            monkeypatch.setenv("N8N_LOG_WEBHOOK_URL", log_url)
        else:
            monkeypatch.delenv("N8N_LOG_WEBHOOK_URL", raising=False)

        from app import config
        from app import main as main_module

        config._settings = None
        importlib.reload(main_module)

        sent: list[dict] = []

        async def fake_post(url, payload):
            assert url == log_url
            sent.append(payload)

        monkeypatch.setattr(usage_log, "_post_event", fake_post)
        client = TestClient(main_module.app)
        client.__enter__()  # runs lifespan -> _init_gateways()
        main_module._galaxy_gateway = gateway
        return client, sent

    yield _build
    from app import config

    config._settings = None


def test_stream_logs_received_and_completed(app_with):
    chunks = [
        _sse("status", {"type": "status", "message": "Procesando…"}),
        _sse("end", {"type": "end", "status": "success", "object_name": "NGC 1300"}),
    ]
    client, sent = app_with(_FakeGateway(chunks))
    resp = client.post(
        "/analyze/stream",
        json={"request_id": "abc", "message": "analiza NGC 1300"},
        headers={"user-agent": "pytest-browser"},
    )
    assert resp.status_code == 200
    assert resp.content == b"".join(chunks)  # user sees exactly the same stream

    events = {e["event"]: e for e in sent}
    assert events["received"]["user_message"] == "analiza NGC 1300"
    assert events["received"]["gateway"] == "galaxy"
    assert events["received"]["user_agent"] == "pytest-browser"
    assert events["completed"]["status"] == "success"
    assert events["completed"]["object_name"] == "NGC 1300"
    assert events["completed"]["duration_ms"] >= 0


def test_stream_logs_error_status_from_end_event(app_with):
    chunks = [_sse("end", {"type": "end", "status": "error", "summary": "timeout"})]
    client, sent = app_with(_FakeGateway(chunks))
    client.post("/analyze/stream", json={"request_id": "e1", "message": "x"})
    completed = [e for e in sent if e["event"] == "completed"][0]
    assert completed["status"] == "error"


def test_stream_unchanged_when_logging_disabled(app_with):
    chunks = [_sse("end", {"type": "end", "status": "success"})]
    client, sent = app_with(_FakeGateway(chunks), log_url=None)
    resp = client.post("/analyze/stream", json={"request_id": "n1", "message": "x"})
    assert resp.status_code == 200
    assert resp.content == b"".join(chunks)
    assert sent == []
