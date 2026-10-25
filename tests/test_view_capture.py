"""The gateway must preserve the atomic image/capture metadata pair."""

from app.gateways.base import AnalysisGateway
from app.schemas import AnalyzeRequest


def test_gateway_preserves_rectangular_view_capture():
    metadata = {
        "ra_deg": 210,
        "dec_deg": 54,
        "fov_x_deg": 0.5,
        "fov_y_deg": 0.25,
        "width_px": 1200,
        "height_px": 600,
        "projection": "SIN",
        "hips_id": "SDSS",
    }
    request = AnalyzeRequest(
        request_id="capture",
        message="Analiza el campo",
        image_data="data:image/png;base64,pixels",
        view_ra_deg=210,
        view_dec_deg=54,
        view_size_arcmin=30,
        view_hips_id="SDSS",
        view_metadata=metadata,
    )
    body = AnalysisGateway._body(request)
    assert body["view_metadata"] == metadata
    assert body["image_data"] == request.image_data


def test_gateway_preserves_capture_required_without_synthesizing_pixels():
    request = AnalyzeRequest(
        request_id="missing",
        message="Analiza",
        view_metadata={"capture_required": True},
    )
    body = AnalysisGateway._body(request)
    assert body["view_metadata"]["capture_required"] is True
    assert "image_data" not in body
