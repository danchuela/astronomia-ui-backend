"""Routing of English messages (phase 2, 2026-10-20): same rules as the Spanish ones."""

from __future__ import annotations

import json

import pytest

from app.router import (
    IntentClassifier,
    is_general_information_request,
    is_observation_intent_request,
    is_planning_conversation,
    is_planning_question,
    is_solar_system_request,
    is_viewer_or_analysis_request,
)


def _classifier(llm_answer: str | None = None) -> IntentClassifier:
    classifier = IntentClassifier.__new__(IntentClassifier)

    def fake_call(message: str) -> str:
        if llm_answer is None:
            raise AssertionError(f"LLM should not be called for: {message!r}")
        return json.dumps({"intent": llm_answer})

    classifier._call_openai = fake_call  # type: ignore[method-assign]
    return classifier


@pytest.mark.parametrize(
    "message",
    ["I wanna see Uranus from London tonight", "When is the next full Moon?", "the Sun today"],
)
def test_solar_system(message: str) -> None:
    assert is_solar_system_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "What can I see tonight from Madrid?",
        "What should I observe this weekend?",
        "Show me the visibility chart of M31 from Barcelona tomorrow",
        "Is M31 visible from Buenos Aires?",
        "When can I see the Orion Nebula?",
        "I want to plan an observation of M13",
        "Suggest something nice to see with binoculars",
        "What do you recommend?",
        "What is there to see with the naked eye?",
    ],
)
def test_observation_intent(message: str) -> None:
    assert is_observation_intent_request(message)


@pytest.mark.parametrize(
    "message", ["Show me M51", "Analyze the morphology of M51", "Show me NGC 1300 in infrared"]
)
def test_viewer_and_analysis_are_not_observation_or_info(message: str) -> None:
    assert not is_observation_intent_request(message)
    assert not is_general_information_request(message)
    assert is_viewer_or_analysis_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "What is a black hole?",
        "What's UGC 10214?",
        "Tell me about M81",
        "What is the distance to M87?",
        "What do you know about the Andromeda galaxy?",
    ],
)
def test_general_information(message: str) -> None:
    assert is_general_information_request(message)


def test_analysis_wins_over_information() -> None:
    assert not is_general_information_request("What is the photometry of M51? Measure it")


def test_planning_conversation_and_questions() -> None:
    assert is_planning_conversation("Saturn tomorrow from London")
    assert is_planning_conversation("what can I see tonight")
    assert not is_planning_conversation("Show me M51 from SDSS")
    assert is_planning_question("Sure! For which date?")
    assert is_planning_question("Great choice. From which city will you observe?")
    assert is_planning_question("I need to know your location.")
    # Galaxy API texts that must not count as planning questions
    assert not is_planning_question(
        "Here is M51. When you are ready, tell me what you want to analyse."
    )
    assert not is_planning_question("Acquisition date 1993. Shall I segment the planetary nebula?")


@pytest.mark.asyncio
async def test_english_follow_ups_stay_in_planning() -> None:
    classifier = _classifier()
    assert (
        await classifier.classify("and tomorrow?", previous_message="Saturn tonight from Madrid")
        == "observation_planning"
    )
    assert (
        await classifier.classify("Today", previous_assistant_message="For which date?")
        == "observation_planning"
    )


@pytest.mark.asyncio
async def test_english_viewer_follow_up_leaves_planning() -> None:
    classifier = _classifier("galaxy_analysis")
    intent = await classifier.classify(
        "show me M31", previous_message="What can I see tonight from Madrid?"
    )
    assert intent == "galaxy_analysis"
