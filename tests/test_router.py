from __future__ import annotations

import pytest

from app.router import IntentClassifier, is_general_information_request, is_solar_system_request


@pytest.mark.parametrize(
    "message",
    [
        "quiero ver Saturno",
        "donde esta Marte esta noche",
        "quiero observar la Luna",
        "cuando puedo ver Jupiter desde Madrid",
        "ver el cometa Halley",
        "lluvia de meteoros",
        "manchas solares",
    ],
)
def test_detects_solar_system_requests(message: str) -> None:
    assert is_solar_system_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "quiero ver M87",
        "quiero ver las Pleyades",
        "muestrame NGC 1300",
        "analiza M51",
        "solo quiero ver Andromeda",
    ],
)
def test_deep_sky_requests_are_not_solar_system(message: str) -> None:
    assert not is_solar_system_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "que es UGC10214 ?",
        "con que caracteristicas especiales cuenta UGC10214 ?",
        "cuentame sobre M81",
        "dame informacion de las Pleyades",
        "cual es la distancia de M87",
        "por que es especial la galaxia del Renacuajo",
        "que caracteristicas especiales tiene?",
    ],
)
def test_detects_general_information_requests(message: str) -> None:
    assert is_general_information_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "quiero ver M87",
        "quiero ver las Pleyades",
        "muestrame NGC 1300",
        "busca NGC 1300",
        "analiza M51",
        "morfologia de M87",
        "haz el analisis morfologico completo de UGC10214",
    ],
)
def test_viewer_and_analysis_requests_are_not_general_information(message: str) -> None:
    assert not is_general_information_request(message)


@pytest.mark.asyncio
async def test_solar_system_classification_bypasses_llm() -> None:
    classifier = IntentClassifier.__new__(IntentClassifier)

    async def fail_if_called() -> str:
        raise AssertionError("LLM should not be called for Solar System targets")

    classifier._call_openai = fail_if_called  # type: ignore[method-assign]

    assert await classifier.classify("quiero ver Saturno") == "observation_planning"


@pytest.mark.asyncio
async def test_general_information_classification_bypasses_llm() -> None:
    classifier = IntentClassifier.__new__(IntentClassifier)

    async def fail_if_called() -> str:
        raise AssertionError("LLM should not be called for general information questions")

    classifier._call_openai = fail_if_called  # type: ignore[method-assign]

    assert await classifier.classify("que es UGC10214?") == "observation_planning"
