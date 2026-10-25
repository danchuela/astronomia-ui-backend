"""Bound request memory while leaving room for planning during a science run."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestCapacity:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._slots = asyncio.Semaphore(4)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path")
            not in {"/analyze", "/analyze/stream", "/images/inspect"}
        ):
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=0.01)
        except TimeoutError:
            await JSONResponse(
                {"detail": {"code": "request_busy"}},
                status_code=503,
                headers={"Retry-After": "3"},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._slots.release()
