"""Raw upstream errors never reach the user; they are reported instead (app/errors.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app import errors
from app.gateways import direct as direct_module
from app.gateways.direct import DirectGalaxyGateway, _GalaxyEventFilter
from app.gateways.n8n import N8nGateway
from app.schemas import AnalyzeRequest

REQ = AnalyzeRequest(request_id="req-1", message="Quiero ver Saturno esta noche desde Madrid")


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def fake_report(module, error_type, detail, request_id=None, endpoint=""):
        calls.append((module, error_type, detail, request_id))

    monkeypatch.setattr(errors, "report_failure", fake_report)
    return calls


def _events(chunks: list[bytes]) -> list[dict[str, Any]]:
    out = []
    for block in b"".join(chunks).decode().split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


# ── n8n ──────────────────────────────────────────────────────────────


class _N8nResponse:
    def __init__(self, status_code: int = 200, body: str = "") -> None:
        self.status_code = status_code
        self.text = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://n8n.test/webhook/astronomia")
            raise httpx.HTTPStatusError(
                "server error", request=req, response=httpx.Response(self.status_code)
            )

    def json(self) -> Any:
        return json.loads(self.text)


def _patch_n8n(monkeypatch: pytest.MonkeyPatch, response: _N8nResponse | Exception) -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, *a: Any, **k: Any) -> _N8nResponse:
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr("app.gateways.n8n.httpx.AsyncClient", _Client)


async def _n8n_stream(gateway: N8nGateway) -> list[dict[str, Any]]:
    return _events([chunk async for chunk in gateway.analyze_stream(REQ)])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (_N8nResponse(200, ""), "invalid_response"),  # failed execution, empty body
        (_N8nResponse(200, "[1, 2]"), "invalid_response"),
        (_N8nResponse(200, '{"status": "success", "summary": ""}'), "empty_response"),
        (_N8nResponse(500, '{"message": "Error in workflow"}'), "http_500"),
        (httpx.ConnectError("connection refused"), "unreachable"),
        (httpx.ReadTimeout("timed out"), "timeout"),
    ],
)
async def test_n8n_stream_failures_are_friendly(monkeypatch, reports, response, error_type) -> None:
    _patch_n8n(monkeypatch, response)
    events = await _n8n_stream(N8nGateway("https://n8n.test/webhook/astronomia"))

    error = next(e for e in events if e["type"] == "error")
    end = events[-1]
    assert error["message"] == errors.friendly_message("n8n")
    assert end["type"] == "end" and end["status"] == "error" and end["request_id"] == "req-1"
    assert end["summary"] == errors.friendly_message("n8n")
    assert "n8n.test" not in json.dumps(events)  # no technical detail for the user
    assert reports == [("n8n", error_type, reports[0][2], "req-1")]


@pytest.mark.asyncio
async def test_n8n_stream_success_is_unchanged(monkeypatch, reports) -> None:
    _patch_n8n(
        monkeypatch,
        _N8nResponse(200, json.dumps({"status": "success", "summary": "Saturno esta noche…"})),
    )
    events = await _n8n_stream(N8nGateway("https://n8n.test/webhook/astronomia"))
    assert [e["type"] for e in events] == ["status", "summary", "end"]
    assert events[-1]["status"] == "success"
    assert reports == []


@pytest.mark.asyncio
async def test_n8n_analyze_failure_is_friendly(monkeypatch, reports) -> None:
    _patch_n8n(monkeypatch, httpx.ReadTimeout("timed out"))
    response = await N8nGateway("https://n8n.test/webhook/astronomia").analyze(REQ)
    assert response.status == "error"
    assert response.summary == errors.friendly_message("n8n")
    assert reports[0][:2] == ("n8n", "timeout")


@pytest.mark.asyncio
async def test_n8n_not_configured_is_friendly(reports) -> None:
    events = await _n8n_stream(N8nGateway(""))
    assert events[-1]["summary"] == errors.friendly_message("n8n")
    assert reports[0][:2] == ("n8n", "not_configured")


# ── Galaxy API ───────────────────────────────────────────────────────


def _sse(payload: dict[str, Any]) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()


def _run_filter(chunks: list[bytes]) -> list[bytes]:
    filt = _GalaxyEventFilter("req-1")
    out: list[bytes] = []
    for chunk in chunks:
        out += filt.feed(chunk)
    return out + filt.finish()


def test_filter_passes_normal_events_byte_for_byte(reports) -> None:
    stream = (
        _sse({"type": "status", "message": "Buscando…"})
        + _sse({"type": "summary", "summary": "Aquí tienes M51."})
        + _sse(
            {
                "type": "end",
                "status": "success",
                "summary": "Aquí tienes M51.",
                "object_name": "M51",
            }
        )
    )
    assert b"".join(_run_filter([stream])) == stream
    assert reports == []


def test_filter_handles_events_split_across_chunks(reports) -> None:
    stream = _sse({"type": "status", "message": "Buscando…"}) + _sse(
        {"type": "end", "status": "success"}
    )
    chunks = [stream[i : i + 7] for i in range(0, len(stream), 7)]
    assert b"".join(_run_filter(chunks)) == stream


def test_filter_replaces_error_and_end(reports) -> None:
    raw = "HTTPSConnectionPool(host='skyserver.sdss.org'): Max retries exceeded"
    stream = (
        _sse({"type": "status", "message": "Descargando imagen…"})
        + _sse({"type": "error", "message": raw})
        + _sse(
            {
                "type": "end",
                "request_id": "req-1",
                "status": "error",
                "summary": "Internal stream error.",
            }
        )
    )
    events = _events(_run_filter([stream]))
    assert events[0]["message"] == "Descargando imagen…"
    assert events[1] == {"type": "error", "message": errors.friendly_message("galaxy")}
    assert events[2]["summary"] == errors.friendly_message("galaxy")
    assert events[2]["request_id"] == "req-1" and events[2]["status"] == "error"
    assert "sdss" not in json.dumps(events)
    assert reports == [("galaxy", "agent_error", raw, "req-1")]  # reported once


def test_filter_adds_end_after_error_without_end(reports) -> None:
    events = _events(_run_filter([_sse({"type": "error", "message": "boom"})]))
    assert [e["type"] for e in events] == ["error", "end"]
    assert events[1]["status"] == "error" and events[1]["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_galaxy_unreachable_is_friendly(monkeypatch, reports) -> None:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        def stream(self, *a: Any, **k: Any) -> Any:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(direct_module.httpx, "AsyncClient", _Client)
    events = _events(
        [c async for c in DirectGalaxyGateway("http://galaxy:8000").analyze_stream(REQ)]
    )
    assert events[0]["message"] == errors.friendly_message("galaxy")
    assert events[-1]["type"] == "end" and events[-1]["status"] == "error"
    assert reports[0][:2] == ("galaxy", "unreachable")


# ── Alerts ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alerts_are_throttled_and_carry_no_user_text(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    async def fake_post(url, token, payload):
        sent.append(payload)

    monkeypatch.setattr(errors, "_post_alert", fake_post)
    monkeypatch.setattr(errors, "_last_alert", {})
    monkeypatch.setattr(errors, "_suppressed", {})
    monkeypatch.setattr(
        errors,
        "get_settings",
        lambda: SimpleNamespace(
            n8n_alert_webhook_url="https://n8n.test/webhook/alert", n8n_alert_token="t"
        ),
    )
    clock = [1000.0]
    monkeypatch.setattr(errors.time, "monotonic", lambda: clock[0])

    for _ in range(3):
        errors.report_failure("galaxy", "timeout", "ReadTimeout", request_id="req-1")
    errors.report_failure(
        "n8n", "timeout", "ReadTimeout", request_id="req-2"
    )  # other module: own throttle
    clock[0] += errors.ALERT_THROTTLE_SECONDS + 1
    errors.report_failure("galaxy", "timeout", "ReadTimeout", request_id="req-3")
    for task in list(errors._pending_tasks):
        await task

    assert [(p["module"], p["request_id"]) for p in sent] == [
        ("galaxy", "req-1"),
        ("n8n", "req-2"),
        ("galaxy", "req-3"),
    ]
    assert sent[2]["suppressed_since_last"] == 2
    assert set(sent[0]) == {
        "source",
        "module",
        "error_type",
        "detail",
        "endpoint",
        "request_id",
        "time_utc",
        "suppressed_since_last",
    }


def test_report_failure_without_webhook_does_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        errors,
        "get_settings",
        lambda: SimpleNamespace(n8n_alert_webhook_url="", n8n_alert_token=""),
    )
    errors.report_failure("n8n", "timeout", "ReadTimeout")  # must not raise
