"""Answer language (app/lang.py). Same cases as n8n test_lang_detect.js (astronomia-infra)."""

from __future__ import annotations

import pytest

from app.lang import answer_language, detect_language, request_language
from app.schemas import AnalyzeRequest, ChatMessage

ENGLISH = [
    "I wanna see Uranus from London tonight",  # real, 2026-10-20
    "I wanna see Uranus from Miami tonight",  # real, 2026-10-20
    "What can I see tonight from Madrid?",
    "and from Buenos Aires?",
    "what about tomorrow?",
    "Show me the visibility chart of M31 from Barcelona tomorrow",
    "What is a black hole?",
    "Hello!",
    "Saturn tomorrow from London",
    "Can I see the Orion Nebula on Saturday?",
    "Analyze the morphology of M51",
    "thanks!",
]
SPANISH = [
    "Quiero ver Saturno esta noche desde Madrid",
    "¿Qué puedo observar esta noche?",
    "y desde Buenos Aires?",
    "Hoy",
    "Que es un agujero negro?",
    "Hola!",
    "no se sugiereme algo tu",
    "Para ver a Sirio",
    "Analiza la morfología de M51",
    "mañana",
    "dame la carta de visualizacion para esta noche en cordoba",
    "Jupiter desde córdoba, argentina",
]


@pytest.mark.parametrize("message", ENGLISH)
def test_english(message: str) -> None:
    assert detect_language(message) == "en"


@pytest.mark.parametrize("message", SPANISH)
def test_spanish(message: str) -> None:
    assert detect_language(message) == "es"


@pytest.mark.parametrize("message", ["M31", "Sirius", "UGC 12158", "ok", "3", "", None])
def test_ambiguous(message: str | None) -> None:
    assert detect_language(message) is None


def test_the_message_wins_over_the_button() -> None:
    assert answer_language("I wanna see Uranus from London tonight", None, "es") == "en"
    assert answer_language("Quiero ver Júpiter esta noche desde Córdoba", None, "en") == "es"


def test_ambiguous_message_uses_history_then_button_then_spanish() -> None:
    assert answer_language("M31", ["What can I see tonight from Madrid?", "M31"], "es") == "en"
    assert answer_language("Sirius", ["Que puedo ver hoy en cordoba", "Sirius"], "en") == "es"
    assert answer_language("M31", ["M31"], "en") == "en"
    assert answer_language("M31", None, None) == "es"


def test_request_language() -> None:
    history = [
        ChatMessage(role="user", content="What can I see tonight from Madrid?"),
        ChatMessage(role="assistant", content="Tonight from Madrid: Saturn…"),
        ChatMessage(role="user", content="M31"),
    ]
    assert request_language(AnalyzeRequest(request_id="r", messages=history)) == "en"
    assert request_language(AnalyzeRequest(request_id="r", message="M31", messages=history)) == "en"
    assert request_language(AnalyzeRequest(request_id="r", message="Hoy", language="en")) == "es"
    assert request_language(AnalyzeRequest(request_id="r", message="M31", language="EN")) == "en"


def test_unknown_language_values_are_ignored() -> None:
    assert AnalyzeRequest(request_id="r", message="x", language="fr").language is None
    assert AnalyzeRequest(request_id="r", message="x", language=" en ").language == "en"
