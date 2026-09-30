from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import NOT_GIVEN

from app.config import Settings
from app.router import IntentClassifier


def test_default_router_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert Settings.from_env().openai_model == "gpt-6-luna"


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-4.1-mini"])
def test_router_request_preserves_json_and_legacy_compatibility(model: str) -> None:
    classifier = IntentClassifier.__new__(IntentClassifier)
    classifier._model = model
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"intent":"galaxy_analysis"}'))]
    )
    client = Mock()
    client.chat.completions.create.return_value = response
    classifier._client = client

    assert classifier._call_openai("Quiero ver M101") == '{"intent":"galaxy_analysis"}'
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == model
    assert kwargs["response_format"] == {"type": "json_object"}
    if model == "gpt-6-luna":
        assert kwargs["reasoning_effort"] == "none"
    else:
        assert kwargs["reasoning_effort"] is NOT_GIVEN
