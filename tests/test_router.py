from __future__ import annotations

import pytest

from app.router import IntentClassifier, is_solar_system_request


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


@pytest.mark.asyncio
async def test_solar_system_classification_bypasses_llm() -> None:
    classifier = IntentClassifier.__new__(IntentClassifier)

    async def fail_if_called() -> str:
        raise AssertionError("LLM should not be called for Solar System targets")

    classifier._call_openai = fail_if_called  # type: ignore[method-assign]

    assert await classifier.classify("quiero ver Saturno") == "observation_planning"
