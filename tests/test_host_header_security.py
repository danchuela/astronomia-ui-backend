"""Regression coverage for CVE-2026-48710 (Starlette BadHost)."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "example.com:8000",
        "example.com/health?ignored=",
        "example.com?ignored=",
        "example.com#ignored=",
    ],
)
def test_host_header_cannot_override_routed_path(host: str) -> None:
    app = FastAPI()

    @app.get("/protected/resource")
    def resource(request: Request) -> dict[str, str]:
        return {"routed_path": request.scope["path"], "url_path": request.url.path}

    with TestClient(app) as client:
        response = client.get("/protected/resource", headers={"host": host})

    assert response.status_code == 200
    assert response.json() == {
        "routed_path": "/protected/resource",
        "url_path": "/protected/resource",
    }
