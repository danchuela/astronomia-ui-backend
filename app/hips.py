"""Versioned access to known HiPS products, without an online registry dependency."""

from __future__ import annotations

import asyncio
import configparser
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

CDS = "https://alasky.cds.unistra.fr/"
CDS_MIRROR = "https://alaskybis.cds.unistra.fr/"
IRSA = "https://irsa.ipac.caltech.edu/data/hips/CDS/"
MAST = "https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds/"
logger = logging.getLogger(__name__)


def cds(path: str) -> tuple[str, str]:
    return CDS + path, CDS_MIRROR + path


# Explicit product roots, not user-provided URLs or automatically discovered hosts.
# Replicas must identify the same creator_did and compatible release/geometry.
SURVEYS: dict[str, tuple[str, ...]] = {
    "CDS/P/DSS2/color": (MAST + "DSSColor", IRSA + "DSS2/color", *cds("DSS/DSSColor")),
    "CDS/P/SDSS9/color": (IRSA + "SDSS/DR9/color", *cds("SDSS/DR9/color")),
    "CDS/P/PanSTARRS/DR1/color-i-r-g": (MAST + "PanSTARRS", *cds("Pan-STARRS/DR1/color-i-r-g")),
    "CDS/P/DECaLS/DR5/color": (
        "https://healpix.ias.u-psud.fr/CDS_P_DECaLS_DR5_color",
        *cds("DECaLS/DR5/color"),
    ),
    "CDS/P/2MASS/color": (IRSA + "2MASS/Color", *cds("2MASS/Color")),
    "CDS/P/allWISE/color": (IRSA + "AllWISE/RGB-W4-W2-W1", *cds("AllWISE/RGB-W4-W2-W1")),
    "CDS/P/GALEXGR6_7/FUV": cds("GALEX/GALEXGR6_7_FUV"),
    "CDS/P/GALEXGR6_7/NUV": cds("GALEX/GALEXGR6_7_NUV"),
    "CDS/P/RASS": cds("RASS"),
    "CDS/P/NVSS": ("https://casda.csiro.au/hips/NVSS", *cds("NVSS/intensity")),
    "xcatdb/P/XMM/PN/color": cds("SSC/xcatdb_P_XMM_PN_color"),
}
MAX_BODY = 8 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHE_ENTRIES = 256
LOOKUP_SECONDS = 12.0
RESOURCE_RE = re.compile(
    r"Norder([0-9]{1,2})/(?:Allsky\.(jpg|png)|"
    r"Dir([0-9]{1,12})/Npix([0-9]{1,12})\.(jpg|png|fits))"
)


class HipsUnavailable(RuntimeError):
    pass


class HipsBusy(HipsUnavailable):
    pass


@dataclass(frozen=True)
class Product:
    id: str
    release: str
    frame: str
    order: int
    width: int
    formats: tuple[str, ...]
    properties: bytes = field(repr=False, compare=False)

    @property
    def revision(self) -> str:
        identity = [self.id, self.release, self.frame, self.order, self.width, self.formats]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]

    def public(self, provider: str) -> dict:
        return {
            "id": self.id,
            "release": self.release,
            "frame": self.frame,
            "max_order": self.order,
            "tile_width": self.width,
            "formats": self.formats,
            "revision": self.revision,
            "path": f"/hips/{self.revision}/{self.id}",
            "provider": provider,
        }


def parse_properties(data: bytes, survey: str) -> Product:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string("[hips]\n" + data.decode("utf-8", errors="strict"))
    props = parser["hips"]
    if props.get("dataproduct_type") != "image":
        raise ValueError("Only image HiPS products are supported")
    if props.get("creator_did") != "ivo://" + survey:
        raise ValueError("Provider returned a different HiPS product")
    release = props.get("hips_release_date", "")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?Z", release):
        raise ValueError("Missing or invalid HiPS release")
    frame = props.get("hips_frame", "")
    order, width = props.getint("hips_order"), props.getint("hips_tile_width", fallback=512)
    formats = tuple(sorted(set(props.get("hips_tile_format", "").replace(",", " ").split())))
    if frame not in {"equatorial", "galactic", "ecliptic"} or order is None or not 0 <= order <= 14:
        raise ValueError("Invalid HiPS geometry")
    if (
        width not in {256, 512, 1024}
        or not formats
        or not set(formats) <= {"jpeg", "png", "fits", "webp"}
    ):
        raise ValueError("Invalid HiPS tiles")
    return Product(survey, release, frame, order, width, formats, data)


def valid_resource(path: str, product: Product) -> bool:
    if path == "Moc.fits":
        return True
    match = RESOURCE_RE.fullmatch(path)
    if not match:
        return False
    order, allsky, directory, pixel, extension = match.groups()
    if str(int(order)) != order or int(order) > product.order:
        return False
    fmt = allsky or extension
    if ("jpeg" if fmt == "jpg" else fmt) not in product.formats:
        return False
    if allsky:
        return True
    assert directory is not None and pixel is not None
    return (
        str(int(pixel)) == pixel
        and str(int(directory)) == directory
        and int(pixel) < 12 * 4 ** int(order)
        and int(directory) == int(pixel) // 10000 * 10000
    )


def image_type(data: bytes, resource: str) -> str:
    if resource.endswith(".jpg") and data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if resource.endswith(".png") and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if resource.endswith(".fits") and data.startswith(b"SIMPLE  ="):
        return "application/fits"
    raise ValueError("Provider returned invalid image bytes")


class HipsService:
    def __init__(self) -> None:
        self._slots = asyncio.Semaphore(8)
        self._metadata: dict[str, tuple[float, Product]] = {}
        self._failed: dict[str, float] = {}
        self._cache: OrderedDict[tuple[str, str], tuple[float, bytes, str, int]] = OrderedDict()
        self._bytes = 0
        self._waiting = 0
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _read(self, url: str, maximum: int) -> tuple[int, bytes]:
        if self._waiting >= 64:
            raise HipsBusy("HiPS download queue full")
        self._waiting += 1
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=8)
        except TimeoutError as exc:
            raise HipsBusy("HiPS download queue busy") from exc
        finally:
            self._waiting -= 1
        try:
            async with asyncio.timeout(6):
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(4, connect=2, pool=2),
                        follow_redirects=False,
                        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
                    )
                async with self._client.stream("GET", url) as response:
                    if response.status_code == 404:
                        return 404, b""
                    if 300 <= response.status_code < 400:
                        raise ValueError("HiPS redirects are not allowed")
                    response.raise_for_status()
                    size = response.headers.get("content-length")
                    if size and int(size) > maximum:
                        raise ValueError("HiPS response exceeds limit")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > maximum:
                            raise ValueError("HiPS response exceeds limit")
                        data.extend(chunk)
                    return response.status_code, bytes(data)
        finally:
            self._slots.release()

    async def _product(self, root: str, survey: str) -> Product:
        now = time.monotonic()
        cached = self._metadata.get(root)
        if cached and cached[0] > now:
            return cached[1]
        status, data = await self._read(root + "/properties", 128 * 1024)
        if status != 200:
            raise ValueError("HiPS properties unavailable")
        product = parse_properties(data, survey)
        self._metadata[root] = (now + 300, product)
        return product

    async def locate(self, survey: str, revision: str | None = None) -> tuple[Product, str]:
        if survey not in SURVEYS:
            raise HTTPException(404, detail="Unknown HiPS product")
        try:
            async with asyncio.timeout(LOOKUP_SECONDS):
                for root in SURVEYS[survey]:
                    if self._failed.get(root, 0) > time.monotonic():
                        continue
                    try:
                        product = await self._product(root, survey)
                        if revision is None or product.revision == revision:
                            return product, root
                    except (httpx.HTTPError, TimeoutError, ValueError, configparser.Error):
                        self._failed[root] = time.monotonic() + 30
        except TimeoutError:
            pass
        raise HipsUnavailable("No verified provider for this HiPS release")

    def _remember(self, key: tuple[str, str], data: bytes, mime: str, status: int) -> None:
        prior = self._cache.pop(key, None)
        if prior:
            self._bytes -= len(prior[1])
        ttl = 3600 if status == 200 else 15
        self._cache[key] = (time.monotonic() + ttl, data, mime, status)
        self._bytes += len(data)
        while self._bytes > MAX_CACHE_BYTES or len(self._cache) > MAX_CACHE_ENTRIES:
            _, old = self._cache.popitem(last=False)
            self._bytes -= len(old[1])

    async def resource(self, revision: str, path: str) -> tuple[bytes, str, int]:
        if not re.fullmatch(r"[a-f0-9]{20}", revision):
            raise HTTPException(404)
        survey = next((key for key in SURVEYS if path.startswith(key + "/")), None)
        if survey is None:
            raise HTTPException(404)
        resource = path[len(survey) + 1 :]
        # Validate syntax before any upstream request, including unknown revisions.
        if len(resource) > 100 or (
            resource not in {"properties", "Moc.fits"} and not RESOURCE_RE.fullmatch(resource)
        ):
            raise HTTPException(404)
        key = (revision, path)
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._cache.move_to_end(key)
            return cached[1:]
        try:
            async with asyncio.timeout(LOOKUP_SECONDS):
                for root in SURVEYS[survey]:
                    if self._failed.get(root, 0) > time.monotonic():
                        continue
                    try:
                        product = await self._product(root, survey)
                    except (httpx.HTTPError, TimeoutError, ValueError, configparser.Error) as exc:
                        self._failed[root] = time.monotonic() + 30
                        logger.warning("HiPS metadata failed: %s (%s)", root, type(exc).__name__)
                        continue
                    if product.revision != revision:
                        continue
                    if resource == "properties":
                        return product.properties, "text/plain", 200
                    if not valid_resource(resource, product):
                        raise HTTPException(404)
                    try:
                        if resource == "Moc.fits":
                            async with asyncio.timeout(2):
                                status, data = await self._read(root + "/" + resource, MAX_BODY)
                        else:
                            status, data = await self._read(root + "/" + resource, MAX_BODY)
                        mime = image_type(data, resource) if status == 200 else "text/plain"
                        self._remember(key, data, mime, status)
                        return data, mime, status
                    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                        if resource == "Moc.fits":
                            # Coverage maps are optional in HiPS. An absent map is
                            # not an absent image; tiles still require full validation.
                            self._remember(key, b"", "text/plain", 404)
                            return b"", "text/plain", 404
                        # One slow or missing file must not black out a healthy origin.
                        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                            self._failed[root] = time.monotonic() + 30
                        logger.warning(
                            "HiPS file failed: %s/%s (%s)", root, resource, type(exc).__name__
                        )
        except TimeoutError:
            pass
        raise HipsUnavailable("HiPS tiles temporarily unavailable")


router = APIRouter(prefix="/hips")
service = HipsService()


def unavailable() -> HTTPException:
    return HTTPException(503, detail={"code": "hips_unavailable"}, headers={"Retry-After": "5"})


@router.get("/metadata")
async def metadata(survey_id: str) -> dict:
    try:
        product, root = await service.locate(survey_id)
        return product.public(root)
    except HipsUnavailable as exc:
        raise unavailable() from exc


@router.api_route("/{revision}/{path:path}", methods=["GET", "HEAD"])
async def tile(revision: str, path: str, request: Request) -> Response:
    try:
        data, mime, status = await service.resource(revision, path)
        return Response(
            b"" if request.method == "HEAD" else data,
            status_code=status,
            media_type=mime,
            headers={
                "Cache-Control": "public, max-age=3600" if status == 200 else "public, max-age=15",
                "Timing-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff",
                "Content-Length": str(len(data)),
                "Server-Timing": f'hips;desc="{status}"',
            },
        )
    except HipsUnavailable as exc:
        raise unavailable() from exc
