"""Routing of open questions and conversation follow-ups.

Real cases from the usage log (September 2026):
- "que puedo observar esta noche desde bogota?" must go to n8n (planning).
- Its follow-up "no se sugiereme algo tu" used to reach the Galaxy API because the
  classifier only looked at the last message. It must stay in planning.
"""

from __future__ import annotations

import json

import pytest

from app.main import _previous_assistant_message, _previous_user_message
from app.router import (
    IntentClassifier,
    is_observation_intent_request,
    is_planning_conversation,
    is_planning_question,
)
from app.schemas import AnalyzeRequest


def _classifier_without_llm(llm_answer: str | None = None) -> IntentClassifier:
    """Classifier whose LLM either fails the test or returns a fixed intent."""
    classifier = IntentClassifier.__new__(IntentClassifier)

    def fake_call(message: str) -> str:
        if llm_answer is None:
            raise AssertionError(f"LLM should not be called for: {message!r}")
        return json.dumps({"intent": llm_answer})

    classifier._call_openai = fake_call  # type: ignore[method-assign]
    return classifier


# ---------------------------------------------------------------- open questions


@pytest.mark.parametrize(
    "message",
    [
        "que puedo observar esta noche desde bogota?",
        "¿Qué puedo observar hoy desde Córdoba?",
        "Que puedo observar en este momento en Buenos Aires?",
        "¿Qué puedo ver a simple vista desde Madrid mañana?",
        "que hay para ver hoy en el cielo",
        "no se sugiereme algo tu",
        "sugerime algo lindo",
        "recomendame algo para ver con binoculares",
        "¿qué me recomiendas observar?",
        "¿Y qué puedo observar a simple vista, sin telescopio?",
    ],
)
def test_open_questions_are_observation_intent(message: str) -> None:
    assert is_observation_intent_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "quiero ver M87",
        "muestrame NGC 1300",
        "analiza M51",
        "morfologia de M87",
        "solo quiero ver Andromeda",
        "que es UGC10214 ?",
    ],
)
def test_viewer_analysis_and_info_are_not_open_questions(message: str) -> None:
    assert not is_observation_intent_request(message)


@pytest.mark.asyncio
async def test_open_question_bypasses_llm() -> None:
    classifier = _classifier_without_llm()
    assert (
        await classifier.classify("que puedo observar esta noche desde bogota?")
        == "observation_planning"
    )


# ---------------------------------------------------------------- conversation context


@pytest.mark.asyncio
async def test_real_bogota_follow_up_stays_in_planning() -> None:
    classifier = _classifier_without_llm()
    intent = await classifier.classify(
        "no se sugiereme algo tu", previous_message="que puedo observar esta noche desde bogota?"
    )
    assert intent == "observation_planning"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("Quiero ver M31 esta noche desde Barranquilla", "y mañana?"),
        ("quiero ver m101 desde calar alto", "y desde Madrid?"),
        ("Quiero ver Saturno el 11 de Junio desde Buenos Aires", "ok, gracias. y NGC 253?"),
        ("es visible UGC10214 esta noche", "a que hora es mejor?"),
    ],
)
async def test_short_follow_ups_of_planning_stay_in_planning(previous: str, current: str) -> None:
    classifier = _classifier_without_llm()
    assert await classifier.classify(current, previous_message=previous) == "observation_planning"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current",
    ["muestrame M31 en infrarrojo", "analiza la morfologia de M31", "quiero la imagen de M31"],
)
async def test_explicit_image_request_after_planning_is_not_forced(current: str) -> None:
    # The context rule must not hijack an explicit request to see/analyse an image:
    # the normal classification decides (here the fake LLM says galaxy_analysis).
    classifier = _classifier_without_llm(llm_answer="galaxy_analysis")
    intent = await classifier.classify(
        current, previous_message="Quiero ver M31 esta noche desde Barranquilla"
    )
    assert intent == "galaxy_analysis"


@pytest.mark.asyncio
async def test_follow_up_of_galaxy_analysis_is_not_forced() -> None:
    classifier = _classifier_without_llm(llm_answer="galaxy_analysis")
    assert await classifier.classify("y M81?", previous_message="analiza M51") == "galaxy_analysis"


@pytest.mark.parametrize(
    ("previous", "expected"),
    [
        ("que puedo observar esta noche desde bogota?", True),
        ("Quiero ver M31 esta noche desde Barranquilla", True),
        ("Jupiter", True),
        ("analiza M51 desde el visor", False),
        ("muestrame NGC 1300", False),
        (None, False),
        ("", False),
    ],
)
def test_is_planning_conversation(previous: str | None, expected: bool) -> None:
    assert is_planning_conversation(previous) is expected


# ---------------------------------------------------------------- previous message extraction


def _req(message: str | None, history: list[tuple[str, str]]) -> AnalyzeRequest:
    return AnalyzeRequest(
        request_id="r",
        message=message,
        messages=[{"role": r, "content": c} for r, c in history],
    )


def test_previous_user_message_when_history_includes_current() -> None:
    req = _req(
        "no se sugiereme algo tu",
        [
            ("user", "que puedo observar desde bogota?"),
            ("assistant", "¿Tienes algo en mente?"),
            ("user", "no se sugiereme algo tu"),
        ],
    )
    assert _previous_user_message(req) == "que puedo observar desde bogota?"


def test_previous_user_message_when_history_excludes_current() -> None:
    req = _req("y mañana?", [("user", "Saturno desde Córdoba"), ("assistant", "(plan)")])
    assert _previous_user_message(req) == "Saturno desde Córdoba"


def test_previous_user_message_without_message_field() -> None:
    req = _req(
        None, [("user", "Saturno desde Córdoba"), ("assistant", "(plan)"), ("user", "y mañana?")]
    )
    assert _previous_user_message(req) == "Saturno desde Córdoba"


def test_previous_user_message_first_turn() -> None:
    assert _previous_user_message(_req("hola", [("user", "hola")])) is None
    assert _previous_user_message(_req("hola", [])) is None


# ---------------------------------------------- answer to a planner's question
# Real case (2026-09-26): "Para ver a Sirio" -> n8n asked for the date -> "Hoy"
# was classified by the LLM and sent to the Galaxy API.

_ASKS_DATE = (
    "Para planificar la observación de Sirio, necesito saber en qué fecha te gustaría "
    "observarla. ¿Me puedes indicar la fecha?"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["Hoy", "mañana", "el sábado", "Córdoba", "desde Rosario"])
async def test_answer_to_a_planning_question_stays_in_planning(current: str) -> None:
    classifier = _classifier_without_llm()
    intent = await classifier.classify(
        current, previous_message="Para ver a Sirio", previous_assistant_message=_ASKS_DATE
    )
    assert intent == "observation_planning"


@pytest.mark.asyncio
async def test_image_request_after_a_planning_question_is_not_forced() -> None:
    classifier = _classifier_without_llm("galaxy_analysis")
    intent = await classifier.classify(
        "muéstrame la imagen de Sirio",
        previous_message="Para ver a Sirio",
        previous_assistant_message=_ASKS_DATE,
    )
    assert intent == "galaxy_analysis"


@pytest.mark.parametrize(
    ("assistant", "expected"),
    [
        (_ASKS_DATE, True),
        ("¡Con gusto te sugiero qué observar! ¿Desde qué ciudad vas a mirar el cielo?", True),
        ("¿Desde dónde quieres observar M31, y cuándo?", True),
        ("No pude encontrar esa ubicación. ¿Me dices la ciudad más cercana?", True),
        # Galaxy API replies: no planning question
        (
            "Aquí tienes Sirius. Ajusta el encuadre, el zoom y la banda en el visor como "
            "prefieras. Cuando estés listo, dime qué quieres analizar.",
            False,
        ),
        ("¿Qué quieres analizar: morfología, segmentación o fotometría?", False),
        # A statement with a date but no question
        ("La noche del 30 de septiembre Saturno se ve a las 01:00. Fecha: 30/09.", False),
        # Independent review (2026-09-27): galaxy replies that mention a date/position
        ("Imagen DSS2 (fecha de adquisición 1993). ¿Quieres que la segmente?", False),
        ("Su ubicación en el cielo es la constelación de Virgo. ¿Quieres ver otra banda?", False),
        # ...and planner questions phrased in other ways
        ("¿Para cuándo quieres observar Sirio?", True),
        ("¿Qué día te gustaría observarlo?", True),
        ("Necesito saber desde dónde vas a observar.", True),
        ("¿Cuándo quieres observarla?", True),
        # "cuándo" about something else (Galaxy API)
        ("¿Quieres saber cuándo fue descubierta?", False),
        ("¿Quieres ver cuándo se tomó la imagen?", False),
        (None, False),
        ("", False),
    ],
)
def test_is_planning_question(assistant: str | None, expected: bool) -> None:
    assert is_planning_question(assistant) is expected


def test_previous_assistant_message() -> None:
    req = _req(
        "Hoy",
        [
            ("user", "Que puedo ver hoy en cordoba"),
            ("assistant", "(sugerencias)"),
            ("user", "Para ver a Sirio"),
            ("assistant", _ASKS_DATE),
            ("user", "Hoy"),
        ],
    )
    assert _previous_assistant_message(req) == _ASKS_DATE
    assert _previous_assistant_message(_req("hola", [("user", "hola")])) is None
    assert _previous_assistant_message(_req("hola", [])) is None
