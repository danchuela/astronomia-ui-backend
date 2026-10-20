"""English answers in the BFF's own texts: friendly errors, analysis buttons, gateway body."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import analysis_actions, errors
from app.gateways import direct as direct_module
from app.gateways.base import AnalysisGateway
from app.gateways.direct import DirectGalaxyGateway, _GalaxyEventFilter
from app.gateways.n8n import N8nGateway
from app.schemas import AnalyzeRequest, ChatMessage

EN = AnalyzeRequest(request_id="req-1", message="I wanna see Uranus from London tonight")
ES = AnalyzeRequest(request_id="req-1", message="Quiero ver Saturno esta noche desde Madrid")

RESOLVE_EN = (
    "Here is M51. Adjust the framing, zoom and band in the viewer as you like. "
    "When you are ready, tell me what you want to analyse: full morphology, segmentation "
    "and isophotes, basic photometry, or brightness profile."
)


@pytest.fixture(autouse=True)
def no_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(errors, "report_failure", lambda *a, **k: None)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()


def _events(chunks: list[bytes]) -> list[dict[str, Any]]:
    out = []
    for block in b"".join(chunks).decode().split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def _failing_client(exc: Exception) -> type:
    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, *a: Any, **k: Any) -> Any:
            raise exc

        def stream(self, *a: Any, **k: Any) -> Any:
            raise exc

    return _Client


# ── Friendly errors ──────────────────────────────────────────────────


def test_messages_exist_in_both_languages() -> None:
    for module in ("n8n", "galaxy"):
        assert errors.friendly_message(module, "es") == errors.FRIENDLY_MESSAGES[module]
        assert errors.friendly_message(module, "en") == errors.FRIENDLY_MESSAGES_EN[module]
    assert errors.friendly_message("galaxy") == errors.FRIENDLY_MESSAGES["galaxy"]  # default
    assert errors.timeout_message("en") == errors.GALAXY_TIMEOUT_MESSAGE_EN
    assert errors.timeout_message() == errors.GALAXY_TIMEOUT_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_", "lang"), [(EN, "en"), (ES, "es")])
async def test_n8n_failure_follows_the_message_language(monkeypatch, request_, lang) -> None:
    client = _failing_client(httpx.ConnectError("x"))
    monkeypatch.setattr("app.gateways.n8n.httpx.AsyncClient", client)
    gateway = N8nGateway("https://n8n.test/webhook/astronomia")
    events = _events([c async for c in gateway.analyze_stream(request_)])
    assert events[-1]["summary"] == errors.friendly_message("n8n", lang)
    response = await gateway.analyze(request_)
    assert response.summary == errors.friendly_message("n8n", lang)


@pytest.mark.asyncio
async def test_galaxy_failures_in_english(monkeypatch) -> None:
    client = _failing_client(httpx.ConnectError("x"))
    monkeypatch.setattr(direct_module.httpx, "AsyncClient", client)
    gateway = DirectGalaxyGateway("http://galaxy:8000")
    events = _events([c async for c in gateway.analyze_stream(EN)])
    assert events[-1]["summary"] == errors.FRIENDLY_MESSAGES_EN["galaxy"]

    monkeypatch.setattr(direct_module.httpx, "AsyncClient", _failing_client(httpx.ReadTimeout("x")))
    events = _events([c async for c in gateway.analyze_stream(EN)])
    assert events[-1]["summary"] == errors.GALAXY_TIMEOUT_MESSAGE_EN


def test_filter_rewrites_agent_errors_in_english() -> None:
    filt = _GalaxyEventFilter("req-1", "en")
    out = filt.feed(_sse({"type": "error", "message": "boom"})) + filt.finish()
    events = _events(out)
    assert events[0]["message"] == errors.FRIENDLY_MESSAGES_EN["galaxy"]
    assert events[-1]["summary"] == errors.FRIENDLY_MESSAGES_EN["galaxy"]


# ── Analysis buttons ─────────────────────────────────────────────────


def test_english_resolve_text_gets_english_buttons() -> None:
    assert analysis_actions.is_viewer_ready(RESOLVE_EN)
    assert analysis_actions.shorten_summary(RESOLVE_EN) == (
        "Here is M51. Adjust the framing, zoom and band in the viewer as you like. "
        + analysis_actions.SHORT_PROMPT_EN
    )
    actions = analysis_actions.suggested_actions(RESOLVE_EN)
    assert [a["label"] for a in actions] == [
        "Full morphology",
        "Segmentation and isophotes",
        "Basic photometry",
        "Brightness profile",
    ]
    assert actions[0]["message"] == "Analyse the full morphology of M51"
    generic = analysis_actions.suggested_actions(analysis_actions.VIEWER_READY_MARKER_EN)
    assert generic[2]["message"] == "Do the basic photometry of this object"


# ── Body sent to n8n and the Galaxy API ──────────────────────────────


def test_body_carries_the_answer_language() -> None:
    assert AnalysisGateway._body(EN)["answer_language"] == "en"
    assert AnalysisGateway._body(ES)["answer_language"] == "es"
    history = [
        ChatMessage(role="user", content="What can I see tonight from Madrid?"),
        ChatMessage(role="assistant", content="Tonight…"),
        ChatMessage(role="user", content="M31"),
    ]
    body = AnalysisGateway._body(AnalyzeRequest(request_id="r", messages=history, language="es"))
    assert body["answer_language"] == "en"  # the conversation wins over the button
    body = AnalysisGateway._body(AnalyzeRequest(request_id="r", message="M31", language="en"))
    assert body["answer_language"] == "en" and body["language"] == "en"
