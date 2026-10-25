"""BFF API request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ImageRegion(BaseModel):
    left: float = Field(ge=0, le=1, allow_inf_nan=False)
    top: float = Field(ge=0, le=1, allow_inf_nan=False)
    right: float = Field(ge=0, le=1, allow_inf_nan=False)
    bottom: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def nonempty(self):
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Empty image region")
        return self


class SourcePoint(BaseModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class ChatMessage(BaseModel):
    role: str
    content: str


class UploadedImage(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    data: str = Field(min_length=1, max_length=11184912, repr=False)
    hdu_index: int | None = Field(default=None, ge=0, le=63)
    psf_hdu_index: int | None = Field(default=None, ge=0, le=63)
    error_hdu_index: int | None = Field(default=None, ge=0, le=63)
    error_kind: Literal["sigma", "variance", "inverse_variance"] = "sigma"
    source_point: SourcePoint | None = None
    region: ImageRegion | None = None
    uncertainty_samples: Literal[0, 16] = 0

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        value = value.replace("\\", "/").rsplit("/", 1)[-1]
        if any(ord(c) < 32 for c in value) or value.rsplit(".", 1)[-1].lower() not in {
            "png",
            "jpg",
            "jpeg",
            "tif",
            "tiff",
            "fits",
            "fit",
            "fts",
        }:
            raise ValueError("Supported files: PNG, JPEG, TIFF and 2D FITS")
        return value


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
    uploaded_image: UploadedImage | None = None
    view_metadata: dict[str, Any] = Field(default_factory=dict)
    # Language chosen in the frontend ("es" | "en"). It is only the default: the answer
    # follows the language of the message (see app/lang.py). Anything else is ignored.
    language: str | None = None

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower()
        return value if value in ("es", "en") else None


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
