"""Gateway to Galaxy API (ORCHESTRATOR_MODE=direct)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app import analysis_actions, errors
from app.gateways.base import AnalysisGateway
from app.lang import request_language
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
        lang = request_language(request)
        filt = _GalaxyEventFilter(request.request_id, lang)
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
                            for out in filt.feed(chunk):
                                yield out
            for out in filt.finish():
                yield out
        except Exception as exc:
            timed_out = isinstance(exc, httpx.TimeoutException)
            errors.report_failure(
                "galaxy",
                _error_type(exc),
                errors.describe_exception(exc),
                request_id=request.request_id,
                endpoint="/analyze/stream",
            )
            if timed_out:
                message = errors.timeout_message(lang)
            else:
                message = errors.friendly_message("galaxy", lang)
            for event in self._failure_events(request.request_id, message):
                yield event


def _error_type(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "unreachable"
    return "unexpected"


class _GalaxyEventFilter:
    """Passes the Galaxy API SSE stream through, with two rewrites.

    The Galaxy API reports internal failures (SDSS, SkyView, the LLM agent...)
    as an `error` event with the exception text, or as an `end` event with
    status "error". Those are reported and replaced by a friendly message;
    every other event is forwarded byte for byte. If the stream breaks after an
    error without its `end` event, one is added so the frontend and the usage
    log see a proper ending.

    When the object is only placed in the viewer, the summary's list of analyses
    becomes `suggested_actions` on the `end` event (see app/analysis_actions.py).
    """

    def __init__(self, request_id: str, lang: str = "es") -> None:
        self._request_id = request_id
        self._lang = lang
        self._buffer = b""
        self._saw_error = False
        self._saw_end = False

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer += chunk.replace(b"\r\n", b"\n")
        out = []
        while b"\n\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n\n", 1)
            out.append(self._process(raw))
        return out

    def finish(self) -> list[bytes]:
        out = [self._buffer] if self._buffer else []
        self._buffer = b""
        if self._saw_error and not self._saw_end:
            message = errors.friendly_message("galaxy", self._lang)
            out.append(AnalysisGateway._failure_events(self._request_id, message)[1])  # end only
        return out

    def _process(self, raw: bytes) -> bytes:
        data_lines = [line[5:].strip() for line in raw.split(b"\n") if line.startswith(b"data:")]
        try:
            payload = json.loads(b"\n".join(data_lines)) if data_lines else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return raw + b"\n\n"
        kind = payload.get("type")
        if kind == "end":
            self._saw_end = True
        if kind == "error" or (kind == "end" and payload.get("status") == "error"):
            if not self._saw_error:
                self._saw_error = True
                detail = str(payload.get("message") or payload.get("summary") or "no detail")
                errors.report_failure(
                    "galaxy",
                    "agent_error",
                    detail,
                    request_id=self._request_id,
                    endpoint="/analyze/stream",
                )
            message = errors.friendly_message("galaxy", self._lang)
            if kind == "error":
                return AnalysisGateway._sse_event("error", {"type": "error", "message": message})
            return AnalysisGateway._sse_event("end", {**payload, "summary": message})
        if kind in ("summary", "end") and analysis_actions.is_viewer_ready(payload.get("summary")):
            summary = payload["summary"]
            rewritten = {**payload, "summary": analysis_actions.shorten_summary(summary)}
            if kind == "end":
                rewritten["suggested_actions"] = analysis_actions.suggested_actions(
                    summary, payload.get("object_name")
                )
            return AnalysisGateway._sse_event(kind, rewritten)
        return raw + b"\n\n"
