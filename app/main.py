"""BFF: routes requests from the frontend to the Galaxy API or n8n.

Uses an optional LLM classifier in auto mode to select the gateway per request.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv

load_dotenv(override=False)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from app.config import get_settings
from app.gateways import DirectGalaxyGateway, N8nGateway
from app.gateways.base import AnalysisGateway
from app.router import IntentClassifier
from app.schemas import AnalyzeRequest, AnalyzeResponse, FeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)

_galaxy_gateway: DirectGalaxyGateway | None = None
_observation_gateway: N8nGateway | None = None
_classifier: IntentClassifier | None = None


def _init_gateways() -> None:
    global _galaxy_gateway, _observation_gateway, _classifier
    settings = get_settings()
    _galaxy_gateway = DirectGalaxyGateway(
        base_url=settings.galaxy_api_url,
        api_key=settings.galaxy_api_key,
    )
    _observation_gateway = N8nGateway(webhook_url=settings.n8n_webhook_url)
    if settings.orchestrator_mode == "auto":
        if not settings.openai_api_key:
            raise RuntimeError(
                "ORCHESTRATOR_MODE=auto requires OPENAI_API_KEY to be set. "
                "Set the variable or switch to ORCHESTRATOR_MODE=direct/n8n."
            )
        _classifier = IntentClassifier(model=settings.openai_model)


def _last_user_message(request: AnalyzeRequest) -> str:
    """Extract the last user message from the request."""
    if request.message:
        return request.message
    if request.messages:
        for msg in reversed(request.messages):
            if msg.role == "user":
                return msg.content
    return ""


async def _select_gateway(request: AnalyzeRequest) -> AnalysisGateway:
    """Select the appropriate gateway based on the orchestration mode."""
    settings = get_settings()
    assert _galaxy_gateway is not None
    assert _observation_gateway is not None

    if settings.orchestrator_mode == "direct":
        return _galaxy_gateway
    if settings.orchestrator_mode == "n8n":
        return _observation_gateway

    assert _classifier is not None
    intent = await _classifier.classify(_last_user_message(request))
    logger.info(
        "request_routed",
        extra={"intent": intent, "request_id": request.request_id},
    )
    if intent == "observation_planning":
        return _observation_gateway
    return _galaxy_gateway


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _init_gateways()
    logger.info(
        "bff_started",
        extra={"orchestrator_mode": settings.orchestrator_mode, "event": "startup"},
    )
    yield
    global _galaxy_gateway, _observation_gateway, _classifier
    _galaxy_gateway = None
    _observation_gateway = None
    _classifier = None


app = FastAPI(
    title="astronomIA UI BFF",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    try:
        gateway = await _select_gateway(request)
        return await gateway.analyze(request)
    except Exception as e:
        logger.exception("analyze_failed", extra={"request_id": request.request_id})
        raise HTTPException(
            status_code=502,
            detail={
                "request_id": request.request_id,
                "status": "error",
                "summary": f"Error del backend: {str(e)}",
            },
        ) from e


@app.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: FeedbackRequest) -> FeedbackResponse:
    """Recibe el feedback del usuario y lo reenvia al webhook de n8n.

    El webhook de n8n se encarga de escribir la fila en la pestania "feedback"
    de la hoja de Google Sheets. El BFF solo actua como proxy para evitar
    exponer la URL del webhook al frontend (y por consistencia con /analyze).
    """
    settings = get_settings()
    if not settings.n8n_feedback_webhook_url:
        # Si la URL no esta configurada respondemos 503 para que el frontend
        # sepa que el feedback no se esta capturando (estado degradado pero
        # no critico).
        raise HTTPException(
            status_code=503,
            detail="Feedback endpoint no esta configurado (falta N8N_FEEDBACK_WEBHOOK_URL).",
        )

    # Solo enviamos a n8n los campos que realmente necesita guardar.
    # exclude_none deja fuera los opcionales que no rellena el usuario.
    payload = request.model_dump(exclude_none=True)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.n8n_feedback_webhook_url, json=payload)
        resp.raise_for_status()
    except Exception as e:
        logger.exception(
            "feedback_forward_failed",
            extra={"request_id": request.request_id},
        )
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo registrar el feedback: {e}",
        ) from e

    logger.info(
        "feedback_recorded",
        extra={
            "request_id": request.request_id,
            "rating": request.rating,
            "has_comment": request.comment is not None,
            "has_email": request.user_email is not None,
        },
    )
    return FeedbackResponse(status="success")


@app.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest) -> StreamingResponse:
    gateway = await _select_gateway(request)
    return StreamingResponse(
        gateway.analyze_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/artifacts/{request_id}/image")
async def get_artifact_image(request_id: str):
    settings = get_settings()
    if settings.orchestrator_mode == "n8n":
        raise HTTPException(
            status_code=404,
            detail="Artifact proxy not available when ORCHESTRATOR_MODE=n8n.",
        )
    url = f"{settings.galaxy_api_url}/artifacts/{request_id}/image"
    headers = {}
    if settings.galaxy_api_key:
        headers["X-API-Key"] = settings.galaxy_api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Image not found for this request.")
    resp.raise_for_status()
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
    )


@app.get("/artifacts/{request_id}/plot/{plot_name}")
async def get_artifact_plot(request_id: str, plot_name: str):
    settings = get_settings()
    if settings.orchestrator_mode == "n8n":
        raise HTTPException(
            status_code=404,
            detail="Artifact proxy not available when ORCHESTRATOR_MODE=n8n.",
        )
    url = f"{settings.galaxy_api_url}/artifacts/{request_id}/plot/{plot_name}"
    headers = {}
    if settings.galaxy_api_key:
        headers["X-API-Key"] = settings.galaxy_api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Plot not found for this request.")
    resp.raise_for_status()
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/png"),
    )
