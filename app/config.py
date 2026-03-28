"""Settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

OrchestratorMode = Literal["direct", "n8n", "auto"]


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


@dataclass(frozen=True)
class Settings:
    orchestrator_mode: OrchestratorMode
    galaxy_api_url: str
    galaxy_api_key: str
    n8n_webhook_url: str
    openai_model: str
    openai_api_key: str
    cors_origins: list[str]

    @classmethod
    def from_env(cls) -> Settings:
        mode = _env("ORCHESTRATOR_MODE", "auto").lower()
        orchestrator_mode: OrchestratorMode
        if mode == "n8n":
            orchestrator_mode = "n8n"
        elif mode == "direct":
            orchestrator_mode = "direct"
        else:
            orchestrator_mode = "auto"

        galaxy_api_url = _env("GALAXY_API_URL", "http://localhost:8000").rstrip("/")
        galaxy_api_key = _env("GALAXY_API_KEY", "")
        n8n_webhook_url = _env("N8N_WEBHOOK_URL", "").rstrip("/")
        openai_model = _env("OPENAI_MODEL", "gpt-4.1-mini")
        openai_api_key = _env("OPENAI_API_KEY", "")

        raw = _env("CORS_ORIGINS", "http://localhost:5173")
        cors_origins = [o.strip() for o in raw.split(",") if o.strip()] or ["http://localhost:5173"]

        return cls(
            orchestrator_mode=orchestrator_mode,
            galaxy_api_url=galaxy_api_url,
            galaxy_api_key=galaxy_api_key,
            n8n_webhook_url=n8n_webhook_url,
            openai_model=openai_model,
            openai_api_key=openai_api_key,
            cors_origins=cors_origins,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
