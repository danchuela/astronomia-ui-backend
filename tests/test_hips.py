from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import hips

ID = "CDS/P/SDSS9/color"
ROOTS = ("https://irsa.ipac.caltech.edu/sdss", "https://alasky.cds.unistra.fr/sdss")
PROPERTIES = b"""creator_did = ivo://CDS/P/SDSS9/color
hips_release_date = 2019-05-05T06:57Z
hips_frame = equatorial
hips_order = 10
hips_tile_width = 512
hips_tile_format = jpeg
dataproduct_type = image
"""
JPEG = b"\xff\xd8\xff" + b"pixel" * 10
CLIENT = httpx.AsyncClient


@pytest.fixture
def service(monkeypatch):
    value = hips.HipsService()
    monkeypatch.setattr(hips, "service", value)
    monkeypatch.setattr(hips, "SURVEYS", {ID: ROOTS})
    return value


def mock_transport(monkeypatch, handler):
    monkeypatch.setattr(
        hips.httpx, "AsyncClient", lambda **kw: CLIENT(transport=httpx.MockTransport(handler), **kw)
    )


@pytest.mark.asyncio
async def test_metadata_validates_product_and_pins_release(service):
    service._read = AsyncMock(return_value=(200, PROPERTIES))
    product, root = await service.locate(ID)
    assert product.id == ID and product.release == "2019-05-05T06:57Z"
    assert root == ROOTS[0]
    assert product.public(root)["path"] == f"/hips/{product.revision}/{ID}"
    first = product.revision
    changed = hips.parse_properties(PROPERTIES.replace(b"2019-05-05", b"2020-05-05"), ID)
    assert first != changed.revision


@pytest.mark.asyncio
async def test_replica_is_rejected_if_it_is_another_product(service):
    service._read = AsyncMock(
        side_effect=[
            (200, PROPERTIES.replace(b"SDSS9/color", b"DSS2/color")),
            (200, PROPERTIES),
        ]
    )
    product, root = await service.locate(ID)
    assert root == ROOTS[1] and product.id == ID


@pytest.mark.asyncio
async def test_failover_never_mixes_versions(service):
    product = hips.parse_properties(PROPERTIES, ID)
    service._read = AsyncMock(
        side_effect=[
            httpx.ConnectTimeout("offline"),
            (200, PROPERTIES.replace(b"2019-05-05", b"2020-05-05")),
        ]
    )
    with pytest.raises(hips.HipsUnavailable):
        await service.locate(ID, product.revision)


@pytest.mark.asyncio
async def test_failed_provider_is_not_queried_for_every_tile(service):
    service._read = AsyncMock(side_effect=[httpx.ConnectTimeout("offline"), (200, PROPERTIES)])
    assert (await service.locate(ID))[1] == ROOTS[1]
    assert (await service.locate(ID))[1] == ROOTS[1]
    assert service._read.await_count == 2


@pytest.mark.parametrize(
    "resource",
    [
        "../../etc/passwd",
        "http://127.0.0.1",
        "Norder10/Dir0/Npix1.svg",
        "Norder10/Dir0/Npix1.jpg/../secret",
        "Norder10/Dir0/Npix1.jpg%00",
    ],
)
@pytest.mark.asyncio
async def test_invalid_paths_never_reach_the_network(service, resource):
    service._read = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await service.resource("0" * 20, ID + "/" + resource)
    assert error.value.status_code == 404
    service._read.assert_not_awaited()


@pytest.mark.parametrize(
    "resource",
    [
        "Norder11/Dir0/Npix1.jpg",
        "Norder10/Dir10000/Npix1.jpg",
        "Norder0/Dir0/Npix12.jpg",
        "Norder1/Dir0/Npix01.jpg",
        "Norder1/Dir0/Npix1.fits",
    ],
)
def test_invalid_tile_geometry_and_formats(resource):
    assert not hips.valid_resource(resource, hips.parse_properties(PROPERTIES, ID))


@pytest.mark.asyncio
async def test_unconfigured_survey_and_bad_revision_are_rejected(service):
    service._read = AsyncMock()
    with pytest.raises(HTTPException):
        await service.locate("http://127.0.0.1/admin")
    with pytest.raises(HTTPException):
        await service.resource("../../", ID + "/Norder0/Dir0/Npix0.jpg")
    service._read.assert_not_awaited()


@pytest.mark.asyncio
async def test_images_are_cached_and_missing_coverage_stays_404(service):
    service._read = AsyncMock(side_effect=[(200, PROPERTIES), (200, JPEG), (404, b"")])
    product, _ = await service.locate(ID)
    path = ID + "/Norder0/Dir0/Npix0.jpg"
    assert await service.resource(product.revision, path) == (JPEG, "image/jpeg", 200)
    assert await service.resource(product.revision, path) == (JPEG, "image/jpeg", 200)
    assert (await service.resource(product.revision, ID + "/Norder0/Dir0/Npix1.jpg"))[2] == 404
    assert service._read.await_count == 3


@pytest.mark.asyncio
async def test_html_or_wrong_file_bytes_are_not_served_as_a_tile(service):
    service._read = AsyncMock(
        side_effect=[
            (200, PROPERTIES),
            (200, b"<html>error</html>"),
            (200, PROPERTIES),
            (200, JPEG),
        ]
    )
    product, _ = await service.locate(ID)
    assert (await service.resource(product.revision, ID + "/Norder0/Dir0/Npix0.jpg"))[0] == JPEG


def test_cache_has_byte_and_entry_limits(monkeypatch, service):
    monkeypatch.setattr(hips, "MAX_CACHE_BYTES", 10)
    monkeypatch.setattr(hips, "MAX_CACHE_ENTRIES", 2)
    for i in range(20):
        service._remember(("r", str(i)), b"123456", "image/jpeg", 200)
    assert service._bytes <= 10 and len(service._cache) <= 2
    for i in range(20):
        service._remember(("r", str(i)), b"", "text/plain", 404)
    assert len(service._cache) <= 2


@pytest.mark.asyncio
async def test_total_deadline_cancels_fetch_and_releases_slots(monkeypatch, service):
    async def handle(request):
        await asyncio.sleep(3600)

    mock_transport(monkeypatch, handle)
    monkeypatch.setattr(hips, "LOOKUP_SECONDS", 0.03)
    with pytest.raises(hips.HipsUnavailable):
        await service.locate(ID)
    assert service._slots._value == 8
    await service.close()
    mock_transport(monkeypatch, lambda req: httpx.Response(200, content=PROPERTIES))
    assert (await service.locate(ID))[0].id == ID


@pytest.mark.asyncio
async def test_redirects_are_never_followed_and_large_bodies_are_bounded(monkeypatch, service):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    mock_transport(monkeypatch, handle)
    with pytest.raises(ValueError):
        await service._read(ROOTS[0] + "/properties", 20)
    assert len(calls) == 1
    await service.close()
    mock_transport(monkeypatch, lambda req: httpx.Response(200, content=b"x" * 21))
    with pytest.raises(ValueError):
        await service._read(ROOTS[0] + "/properties", 20)
    assert service._slots._value == 8


def test_public_routes_are_binary_safe_and_support_legacy_browser_checks(service):
    service._read = AsyncMock(side_effect=[(200, PROPERTIES), (200, JPEG)])
    app = FastAPI()
    app.include_router(hips.router)
    with TestClient(app) as client:
        meta = client.get("/hips/metadata", params={"survey_id": ID}).json()
        url = meta["path"] + "/Norder0/Dir0/Npix0.jpg"
        image = client.get(url)
        assert image.status_code == 200 and image.content == JPEG
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.headers["timing-allow-origin"] == "*"
        head = client.head(url + "?verify=1")
        assert head.status_code == 200 and not head.content
        assert head.headers["content-length"] == str(len(JPEG))
        assert client.get("/health").status_code == 404


def test_unavailable_provider_returns_retryable_error_without_private_detail(service):
    service.locate = AsyncMock(side_effect=hips.HipsUnavailable("private hostname trace"))
    app = FastAPI()
    app.include_router(hips.router)
    with TestClient(app) as client:
        response = client.get("/hips/metadata", params={"survey_id": ID})
        assert response.status_code == 503 and response.headers["retry-after"] == "5"
        assert response.json() == {"detail": {"code": "hips_unavailable"}}


@pytest.mark.asyncio
async def test_optional_coverage_failure_does_not_disable_valid_tiles(service):
    service._read = AsyncMock(
        side_effect=[(200, PROPERTIES), httpx.ReadTimeout("coverage slow"), (200, JPEG)]
    )
    product, _ = await service.locate(ID)
    assert (await service.resource(product.revision, ID + "/Moc.fits"))[2] == 404
    assert not service._failed
    assert (await service.resource(product.revision, ID + "/Norder0/Dir0/Npix0.jpg"))[0] == JPEG


@pytest.mark.asyncio
async def test_temporary_file_failure_does_not_black_out_origin_metadata(service):
    service._read = AsyncMock(
        side_effect=[
            (200, PROPERTIES),
            httpx.ReadTimeout("tile slow"),
            httpx.ConnectError("second origin offline"),
        ]
    )
    product, _ = await service.locate(ID)
    with pytest.raises(hips.HipsUnavailable):
        await service.resource(product.revision, ID + "/Norder0/Dir0/Npix0.jpg")
    assert ROOTS[0] not in service._failed
    assert (await service.locate(ID))[1] == ROOTS[0]


@pytest.mark.asyncio
async def test_local_backpressure_is_not_classified_as_a_dead_provider(service):
    service._read = AsyncMock(side_effect=hips.HipsBusy("busy locally"))
    with pytest.raises(hips.HipsBusy):
        await service.locate(ID)
    assert not service._failed


def test_full_image_properties_are_preserved_for_aladin(service):
    service._read = AsyncMock(return_value=(200, PROPERTIES))
    app = FastAPI()
    app.include_router(hips.router)
    with TestClient(app) as client:
        meta = client.get("/hips/metadata", params={"survey_id": ID}).json()
        response = client.get(meta["path"] + "/properties")
        assert response.status_code == 200 and response.content == PROPERTIES
        assert b"dataproduct_type = image" in response.content
