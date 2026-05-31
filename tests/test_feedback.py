"""Tests del endpoint /feedback del BFF.

Verifica que:
- El endpoint reenvia correctamente los datos al webhook de n8n.
- Los campos opcionales se excluyen del payload cuando no se proveen.
- El endpoint devuelve 503 cuando la URL del webhook no esta configurada.
- El endpoint devuelve 502 si el webhook de n8n falla.
"""

from __future__ import annotations

import importlib
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


class _FakeAsyncClient:
    """Stub completo de httpx.AsyncClient para los tests.

    Reemplaza la clase real para evitar inicializar el cliente HTTP (que detecta
    proxies SOCKS del entorno y puede fallar). Captura el ultimo POST en
    variables de clase para que los tests puedan inspeccionarlo.
    """

    last_url: str | None = None
    last_payload: dict[str, Any] | None = None
    next_exception: Exception | None = None
    next_status_code: int = 200

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def post(self, url, json=None, **kwargs):  # noqa: ARG002
        type(self).last_url = url
        type(self).last_payload = json
        if type(self).next_exception is not None:
            raise type(self).next_exception
        request = httpx.Request("POST", url)
        return httpx.Response(
            type(self).next_status_code,
            json={"status": "success"},
            request=request,
        )


@pytest.fixture
def client_factory(monkeypatch):
    """Construye un TestClient con una URL de webhook de feedback configurable.

    Se hace dentro de una fixture porque el modulo de config cachea Settings,
    asi que para cada test reseteamos la cache despues de cambiar el env.
    """

    def _build(feedback_url: str | None = "https://n8n.example.com/webhook/feedback"):
        # Limpiar proxies del entorno: el sandbox CI puede tener SOCKS configurado,
        # lo que rompe httpx.AsyncClient al construirlo. En produccion no hay
        # proxies, asi que esto solo afecta a la ejecucion de los tests.
        for proxy_var in (
            "HTTP_PROXY", "http_proxy",
            "HTTPS_PROXY", "https_proxy",
            "ALL_PROXY", "all_proxy",
            "NO_PROXY", "no_proxy",
            "FTP_PROXY", "ftp_proxy",
            "grpc_proxy",
        ):
            monkeypatch.delenv(proxy_var, raising=False)

        # Variables minimas que main.py necesita al arrancar.
        monkeypatch.setenv("ORCHESTRATOR_MODE", "direct")
        monkeypatch.setenv("GALAXY_API_URL", "http://localhost:8000")
        monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.com/webhook/astronomia")
        if feedback_url is None:
            monkeypatch.delenv("N8N_FEEDBACK_WEBHOOK_URL", raising=False)
        else:
            monkeypatch.setenv("N8N_FEEDBACK_WEBHOOK_URL", feedback_url)

        # Reset del estado del fake antes de cada test.
        _FakeAsyncClient.last_url = None
        _FakeAsyncClient.last_payload = None
        _FakeAsyncClient.next_exception = None
        _FakeAsyncClient.next_status_code = 200

        # Reload de los modulos para que cojan los nuevos env.
        from app import config as cfg_module

        cfg_module._settings = None
        # Reemplazamos httpx.AsyncClient ANTES de reload main para evitar la
        # construccion real (que puede fallar por proxies del entorno).
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        from app import main as main_module

        importlib.reload(main_module)
        return TestClient(main_module.app)

    return _build


def test_feedback_forwards_full_payload_to_n8n(client_factory):
    """Cuando el usuario manda rating + comentario + email, todos los campos
    deben llegar a n8n y la respuesta debe ser 200 con status=success."""
    client = client_factory()
    resp = client.post(
        "/feedback",
        json={
            "request_id": "abc-123",
            "rating": "up",
            "comment": "Excelente respuesta sobre M31",
            "user_email": "test@example.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    assert _FakeAsyncClient.last_url == "https://n8n.example.com/webhook/feedback"
    assert _FakeAsyncClient.last_payload == {
        "request_id": "abc-123",
        "rating": "up",
        "comment": "Excelente respuesta sobre M31",
        "user_email": "test@example.com",
    }


def test_feedback_omits_optional_fields_when_missing(client_factory):
    """Solo rating + request_id deben llegar a n8n cuando el usuario no manda
    comment ni user_email. Esto evita guardar 'null' en celdas vacias."""
    client = client_factory()
    resp = client.post(
        "/feedback",
        json={"request_id": "def-456", "rating": "down"},
    )
    assert resp.status_code == 200
    # comment y user_email deben quedar fuera del payload por exclude_none.
    assert _FakeAsyncClient.last_payload == {
        "request_id": "def-456",
        "rating": "down",
    }


def test_feedback_returns_503_when_webhook_not_configured(client_factory):
    """Si N8N_FEEDBACK_WEBHOOK_URL no esta configurado, el endpoint debe
    responder 503 (degradado) en vez de fallar silenciosamente."""
    client = client_factory(feedback_url=None)
    resp = client.post(
        "/feedback",
        json={"request_id": "ghi-789", "rating": "up"},
    )
    assert resp.status_code == 503
    assert "N8N_FEEDBACK_WEBHOOK_URL" in resp.json()["detail"]


def test_feedback_returns_502_when_n8n_fails(client_factory):
    """Si n8n responde con error, devolvemos 502 para que el frontend pueda
    avisar al usuario."""
    client = client_factory()
    _FakeAsyncClient.next_exception = httpx.ConnectError("n8n unreachable")
    resp = client.post(
        "/feedback",
        json={"request_id": "jkl-012", "rating": "up"},
    )
    assert resp.status_code == 502


def test_feedback_rejects_invalid_rating(client_factory):
    """Pydantic debe rechazar ratings que no sean up/down con un 422."""
    client = client_factory()
    resp = client.post(
        "/feedback",
        json={"request_id": "mno-345", "rating": "maybe"},
    )
    assert resp.status_code == 422
