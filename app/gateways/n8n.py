"""n8n webhook gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app import errors
from app.gateways.base import AnalysisGateway
from app.lang import request_language
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


class N8nResponseError(Exception):
    """n8n answered, but with something the BFF cannot show (empty, not JSON...)."""

    def __init__(self, error_type: str, detail: str) -> None:
        super().__init__(detail)
        self.error_type = error_type


def _error_type(exc: Exception) -> str:
    if isinstance(exc, N8nResponseError):
        return exc.error_type
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "unreachable"
    return "unexpected"


class N8nGateway(AnalysisGateway):
    @staticmethod
    def _body(request: AnalyzeRequest) -> dict:
        body = AnalysisGateway._body(request)
        body.pop("uploaded_image", None)
        return body

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url.rstrip("/")

    async def _call(self, request: AnalyzeRequest) -> dict[str, Any]:
        """POST to the n8n webhook. Raises N8nResponseError if the answer is unusable."""
        if not self.webhook_url:
            raise N8nResponseError("not_configured", "N8N_WEBHOOK_URL is empty")
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(self.webhook_url, json=self._body(request))
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                # A failed n8n execution can answer 200 with an empty body.
                raise N8nResponseError(
                    "invalid_response", f"HTTP {resp.status_code}, body: {resp.text[:120]!r}"
                ) from exc
        if not isinstance(data, dict):
            kind = type(data).__name__
            raise N8nResponseError("invalid_response", f"JSON is a {kind}, not an object")
        if not data.get("summary") and not data.get("artifacts"):
            raise N8nResponseError("empty_response", f"status={data.get('status')!r}, no summary")
        return data

    def _report(self, exc: Exception, request: AnalyzeRequest) -> None:
        errors.report_failure(
            "n8n",
            _error_type(exc),
            errors.describe_exception(exc),
            request_id=request.request_id,
            endpoint="n8n webhook",
        )

    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        try:
            data = await self._call(request)
        except Exception as exc:
            self._report(exc, request)
            return AnalyzeResponse(
                request_id=request.request_id,
                status="error",
                summary=errors.friendly_message("n8n", request_language(request)),
                results={},
                artifacts=[],
                warnings=[],
            )
        return _normalize_response(data, request)

    async def analyze_stream(self, request: AnalyzeRequest) -> AsyncIterator[bytes]:
        yield self._sse_event("status", {"type": "status", "message": "Procesando…"})

        try:
            data = await self._call(request)
        except Exception as exc:
            self._report(exc, request)
            message = errors.friendly_message("n8n", request_language(request))
            for event in self._failure_events(request.request_id, message):
                yield event
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
