"""Gateway: direct Galaxy API or n8n webhook."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.schemas import AnalyzeRequest, AnalyzeResponse


class AnalysisGateway(ABC):
    @abstractmethod
    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        ...

    @abstractmethod
    async def analyze_stream(self, request: AnalyzeRequest) -> AsyncIterator[bytes]:
        ...

    @staticmethod
    def _sse_event(event_name: str, payload: dict) -> bytes:
        return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n".encode()

    @staticmethod
    def _body(request: AnalyzeRequest) -> dict:
        body = request.model_dump(exclude_none=True)
        if request.messages and not request.message:
            last_user = next(
                (m.content for m in reversed(request.messages) if m.role == "user"),
                None,
            )
            body.setdefault("message", last_user or "")
        if request.message and not request.messages:
            body.setdefault("messages", [{"role": "user", "content": request.message}])
        return body
