"""User-facing error messages and failure alerts.

Raw errors from n8n, the Galaxy API or the external services behind them
(SDSS, SkyView, SIMBAD, Horizons, Open-Meteo, OpenAI...) must never reach the
user. The gateways replace them with a friendly message that suggests another
feature, and report the technical details here.

Reports are logged locally and, if N8N_ALERT_WEBHOOK_URL is set, POSTed to the
n8n "alerts" workflow, which forwards them to Telegram. Alerts never include
the user's message or any personal data: the request_id is enough to find the
interaction in the database. The same (module, error_type) is alerted at most
once every ALERT_THROTTLE_SECONDS, so an outage does not flood the chat.
Like usage logging, alerts are fire-and-forget and never raise.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

Module = Literal["n8n", "galaxy"]

FRIENDLY_MESSAGES: dict[str, str] = {
    "n8n": (
        "La planificación de observaciones y la información general están teniendo "
        "problemas en este momento. Mientras lo revisamos, puedes analizar una galaxia "
        '(por ejemplo: "Muéstrame M51") o volver a intentarlo en unos minutos.'
    ),
    "galaxy": (
        "El análisis de galaxias está teniendo problemas en este momento. Mientras lo "
        "revisamos, puedes planificar una observación (por ejemplo: "
        '"¿Qué puedo observar esta noche desde Madrid?") o volver a intentarlo en unos minutos.'
    ),
}

GALAXY_TIMEOUT_MESSAGE = (
    "El análisis está tardando demasiado. Inténtalo de nuevo en unos minutos; mientras "
    "tanto, puedes planificar una observación o preguntar por otro objeto."
)

ALERT_THROTTLE_SECONDS = 300.0
_ALERT_TIMEOUT_SECONDS = 5.0
_DETAIL_MAX_CHARS = 300

# (module, error_type) -> monotonic time of the last alert sent
_last_alert: dict[tuple[str, str], float] = {}
# (module, error_type) -> failures not alerted because of the throttle
_suppressed: dict[tuple[str, str], int] = {}
_pending_tasks: set[asyncio.Task] = set()


def friendly_message(module: str) -> str:
    return FRIENDLY_MESSAGES.get(module, FRIENDLY_MESSAGES["n8n"])


def describe_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


async def _post_alert(url: str, token: str, payload: dict[str, Any]) -> None:
    try:
        headers = {"X-Alert-Token": token} if token else {}
        async with httpx.AsyncClient(timeout=_ALERT_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
    except Exception:
        logger.warning(
            "failure_alert_failed", extra={"request_id": payload.get("request_id")}, exc_info=True
        )


def report_failure(
    module: Module,
    error_type: str,
    detail: str,
    request_id: str | None = None,
    endpoint: str = "",
) -> None:
    """Log a failure and alert it (throttled). Never raises."""
    try:
        logger.warning(
            "upstream_failure",
            extra={
                "failed_module": module,
                "error_type": error_type,
                "request_id": request_id,
                "detail": detail,
            },
        )
        settings = get_settings()
        if not settings.n8n_alert_webhook_url:
            return
        key = (module, error_type)
        now = time.monotonic()
        last = _last_alert.get(key)
        if last is not None and now - last < ALERT_THROTTLE_SECONDS:
            _suppressed[key] = _suppressed.get(key, 0) + 1
            return
        _last_alert[key] = now
        payload = {
            "source": "bff",
            "module": module,
            "error_type": error_type,
            "detail": detail[:_DETAIL_MAX_CHARS],
            "endpoint": endpoint,
            "request_id": request_id,
            "time_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            # Failures of the same kind that were not alerted since the previous alert.
            "suppressed_since_last": _suppressed.pop(key, 0),
        }
        task = asyncio.get_running_loop().create_task(
            _post_alert(settings.n8n_alert_webhook_url, settings.n8n_alert_token, payload)
        )
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)
    except Exception:
        logger.warning("report_failure_failed", exc_info=True)
