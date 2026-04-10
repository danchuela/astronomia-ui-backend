"""Gateway to Galaxy API (ORCHESTRATOR_MODE=direct)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.gateways.base import AnalysisGateway
from app.schemas import AnalyzeRequest, AnalyzeResponse


class DirectGalaxyGateway(AnalysisGateway):
    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        url = f"{self.base_url}/analyze"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=self._body(request), headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return AnalyzeResponse(
            request_id=data.get("request_id", request.request_id),
            status=data.get("status", "error"),
            summary=data.get("summary", ""),
            results=data.get("results", {}),
            artifacts=data.get("artifacts", []),
            warnings=data.get("warnings", []),
        )

    async def analyze_stream(self, request: AnalyzeRequest) -> AsyncIterator[bytes]:
        url = f"{self.base_url}/analyze/stream"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        timeout = httpx.Timeout(connect=15.0, read=None, write=30.0, pool=30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=self._body(request),
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.TimeoutException:
            yield self._sse_event(
                "error",
                {
                    "type": "error",
                    "message": "El análisis tardó demasiado. Inténtalo de nuevo.",
                },
            )
            yield self._sse_event(
                "end",
                {
                    "type": "end",
                    "request_id": request.request_id,
                    "status": "error",
                    "summary": "El análisis tardó demasiado. Inténtalo de nuevo.",
                },
            )
        except httpx.HTTPError as exc:
            yield self._sse_event(
                "error",
                {
                    "type": "error",
                    "message": f"Error al comunicar con Galaxy API: {exc}",
                },
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

