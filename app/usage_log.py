"""Usage logging for astronomIA.

The BFF is the single entry point for every user message, whichever module
answers it (Galaxy API or n8n), so this is where usage is recorded.

Each request produces two events that are POSTed to an n8n webhook
(N8N_LOG_WEBHOOK_URL). n8n writes them to Postgres and sends an alert if the
write fails:

- "received":  the user's message arrived (text, turn, gateway, target...).
- "completed": the answer finished (status, duration, detected object).

Logging is fire-and-forget: events are sent in background tasks with a short
timeout, and any failure is swallowed and logged locally. A logging problem
must never delay or break the answer the user is waiting for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings
from app.schemas import AnalyzeRequest

logger = logging.getLogger(__name__)

# Keeps references to in-flight tasks so they are not garbage-collected
# before they finish (asyncio only keeps weak references to tasks).
_pending_tasks: set[asyncio.Task] = set()

_LOG_TIMEOUT_SECONDS = 5.0


async def _post_event(url: str, payload: dict[str, Any]) -> None:
    """Send one event to n8n. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=_LOG_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception:
        logger.warning(
            "usage_log_failed",
            extra={"event": payload.get("event"), "request_id": payload.get("request_id")},
            exc_info=True,
        )


def send_event(payload: dict[str, Any]) -> None:
    """Schedule an event to be sent in the background. No-op if not configured."""
    url = get_settings().n8n_log_webhook_url
    if not url:
        return
    try:
        task = asyncio.get_running_loop().create_task(_post_event(url, payload))
    except RuntimeError:
        # No running event loop (should not happen inside FastAPI); skip silently.
        return
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


def _last_user_message(request: AnalyzeRequest) -> str | None:
    if request.message:
        return request.message
    for msg in reversed(request.messages or []):
        if msg.role == "user":
            return msg.content
    return None


def _target_payload(request: AnalyzeRequest) -> dict[str, Any] | None:
    """Galaxy target and sky-viewer position, if the UI sent any."""
    target: dict[str, Any] = dict(request.target or {})
    view = {
        "view_ra_deg": request.view_ra_deg,
        "view_dec_deg": request.view_dec_deg,
        "view_size_arcmin": request.view_size_arcmin,
        "view_hips_id": request.view_hips_id,
    }
    target.update({k: v for k, v in view.items() if v is not None})
    return target or None


def received_event(
    request: AnalyzeRequest, gateway: str, user_agent: str | None
) -> dict[str, Any]:
    """Build the event sent when a user message arrives."""
    target = _target_payload(request)
    return {
        "event": "received",
        "request_id": request.request_id,
        "user_message": _last_user_message(request),
        # Same convention as the old Sheets log: previous messages + this one.
        "conversation_turn": len(request.messages or []) + 1,
        "task": request.task,
        "gateway": gateway,
        "target": target,
        "object_name": (request.target or {}).get("name") if request.target else None,
        "has_image": bool(request.image_data or request.image_url),
        "user_agent": user_agent,
    }


def completed_event(
    request_id: str,
    gateway: str,
    status: str,
    duration_ms: int,
    object_name: str | None = None,
) -> dict[str, Any]:
    """Build the event sent when the answer has finished."""
    return {
        "event": "completed",
        "request_id": request_id,
        "gateway": gateway,
        "status": status,
        "duration_ms": duration_ms,
        "object_name": object_name,
    }


class EndEventSniffer:
    """Reads the SSE stream as it passes through and remembers the final "end" event.

    Both gateways finish their stream with `event: end` carrying the final
    status (and, for the Galaxy API, the resolved object_name). Chunks can cut
    an event in half, so we buffer until a full event (blank line) arrives.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.end_payload: dict[str, Any] | None = None

    def feed(self, chunk: bytes | str) -> None:
        try:
            text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
            self._buffer += text.replace("\r\n", "\n")
            while "\n\n" in self._buffer:
                raw_event, self._buffer = self._buffer.split("\n\n", 1)
                self._parse(raw_event)
        except Exception:
            # Sniffing is best effort; never interfere with the stream.
            logger.debug("sse_sniff_failed", exc_info=True)

    def _parse(self, raw_event: str) -> None:
        name, data_lines = None, []
        for line in raw_event.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if name == "end" and data_lines:
            try:
                self.end_payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                self.end_payload = {}


async def logged_stream(
    stream: AsyncIterator[bytes],
    request_id: str,
    gateway: str,
    started_at: float,
) -> AsyncIterator[bytes]:
    """Pass the gateway stream through unchanged and log a "completed" event at the end."""
    sniffer = EndEventSniffer()
    # "cancelled" covers the user closing the tab mid-answer; overwritten below.
    status = "cancelled"
    try:
        async for chunk in stream:
            sniffer.feed(chunk)
            yield chunk
        if sniffer.end_payload is not None:
            status = sniffer.end_payload.get("status") or "success"
        else:
            status = "unknown"  # stream ended without an "end" event
    except Exception:
        status = "error"
        raise
    finally:
        end = sniffer.end_payload or {}
        send_event(
            completed_event(
                request_id=request_id,
                gateway=gateway,
                status=status,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                object_name=end.get("object_name"),
            )
        )
