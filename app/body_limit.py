"""Bound analysis JSON before parsing it, including chunked requests."""

import asyncio
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 12 * 1024 * 1024
BODY_IDLE_SECONDS = 15.0
BODY_TOTAL_SECONDS = 60.0


class AnalysisBodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path")
            not in {"/analyze", "/analyze/stream", "/images/inspect"}
        ):
            await self.app(scope, receive, send)
            return
        chunks = []
        size = 0
        deadline = monotonic() + BODY_TOTAL_SECONDS
        while True:
            try:
                event = await asyncio.wait_for(
                    receive(),
                    timeout=max(0, min(BODY_IDLE_SECONDS, deadline - monotonic())),
                )
            except TimeoutError:
                await JSONResponse(
                    {"detail": {"code": "upload_timeout"}}, status_code=408
                )(scope, receive, send)
                return
            if event["type"] == "http.disconnect":
                return
            data = event.get("body", b"")
            size += len(data)
            if size > MAX_BODY_BYTES:
                await JSONResponse(
                    {"detail": "Analysis request exceeds 12 MiB"}, status_code=413
                )(scope, receive, send)
                return
            chunks.append(data)
            if not event.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": b"".join(chunks),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay, send)
