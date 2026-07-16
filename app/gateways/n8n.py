"""n8n webhook gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.gateways.base import AnalysisGateway
from app.schemas import AnalyzeRequest, AnalyzeResponse


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_warnings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _normalize_response(data: dict[str, Any], request: AnalyzeRequest) -> AnalyzeResponse:
    return AnalyzeResponse(
        request_id=data.get("request_id") or request.request_id,
        status=data.get("status") or "success",
        summary=data.get("summary") or "",
        results=_as_dict(data.get("results")),
        artifacts=_as_artifacts(data.get("artifacts")),
        warnings=_as_warnings(data.get("warnings")),
    )


class N8nGateway(AnalysisGateway):
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url.rstrip("/")

    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        if not self.webhook_url:
            return AnalyzeResponse(
                request_id=request.request_id,
                status="error",
                summary="N8N_WEBHOOK_URL no configurado.",
                results={},
                artifacts=[],
                warnings=[],
            )

        body = self._body(request)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(self.webhook_url, json=body)
            resp.raise_for_status()
            data = resp.json()

        return _normalize_response(data, request)

    async def analyze_stream(self, request: AnalyzeRequest) -> AsyncIterator[bytes]:
        if not self.webhook_url:
            yield self._sse_event(
                "error",
                {"type": "error", "message": "N8N_WEBHOOK_URL no configurado."},
            )
            yield self._sse_event(
                "end",
                {
                    "type": "end",
                    "request_id": request.request_id,
                    "status": "error",
                    "summary": "N8N_WEBHOOK_URL no configurado.",
                },
            )
            return

        yield self._sse_event("status", {"type": "status", "message": "Procesando…"})

        try:
            body = self._body(request)
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(self.webhook_url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            yield self._sse_event(
                "error",
                {"type": "error", "message": f"Error al comunicar con n8n: {exc}"},
            )
            yield self._sse_event(
                "end",
                {
                    "type": "end",
                    "request_id": request.request_id,
                    "status": "error",
                    "summary": "No se pudo completar el análisis por un error de comunicación.",
                },
            )
            return

        response = _normalize_response(data, request)
        request_id = response.request_id
        status = response.status
        summary = response.summary
        artifacts = response.artifacts

        if summary:
            yield self._sse_event("summary", {"type": "summary", "summary": summary})

        html_artifact = next(
            (a for a in artifacts if a.get("format") == "html" or a.get("type") == "html"),
            None,
        )
        if html_artifact:
            yield self._sse_event("artifacts", {
                "type": "artifacts",
                "request_id": request_id,
                "html_chart": html_artifact.get("content", ""),
            })

        yield self._sse_event("end", {
            "type": "end",
            "request_id": request_id,
            "status": status,
            "summary": summary,
        })
