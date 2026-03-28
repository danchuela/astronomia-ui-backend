"""BFF API request/response models."""

from __future__ import annotations

from typing import Any

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
