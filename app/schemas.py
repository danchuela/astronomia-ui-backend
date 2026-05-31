"""BFF API request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class AnalyzeRequest(BaseModel):
    request_id: str
    message: str | None = None
    messages: list[ChatMessage] | None = None
    target: dict[str, Any] | None = None
    task: str | None = None
    image_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    view_ra_deg: float | None = None
    view_dec_deg: float | None = None
    view_size_arcmin: float | None = None
    view_hips_id: str | None = None
    image_data: str | None = None


class AnalyzeResponse(BaseModel):
    request_id: str
    status: str
    summary: str
    results: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """Datos de feedback enviados por el usuario desde el frontend.

    - request_id: identificador de la interaccion a la que se refiere el feedback
      (mismo id que se usa en /analyze; permite cruzar con el log de Sheets).
    - rating: valoracion binaria "up" / "down" (pulgares).
    - comment: comentario libre opcional (cuando el usuario abre el modal grande).
    - user_email: email opcional; solo si la persona quiere ser contactada.
    """

    request_id: str
    rating: Literal["up", "down"]
    comment: str | None = None
    user_email: str | None = None


class FeedbackResponse(BaseModel):
    """Respuesta del endpoint de feedback."""

    status: str
