"""LLM-based intent classifier for routing requests to the appropriate gateway."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from openai import OpenAI

logger = logging.getLogger(__name__)

Intent = Literal["galaxy_analysis", "observation_planning"]

_DEFAULT_INTENT: Intent = "galaxy_analysis"

_SYSTEM_PROMPT = """\
Eres un clasificador de intenciones para una plataforma de astronomia.
Clasifica el mensaje del usuario en UNA de estas categorias:

1. "galaxy_analysis" — quiere ver, analizar, segmentar o medir un objeto celeste.
   NO menciona ubicacion terrestre ni momento temporal para observar.
   Ejemplos: "analiza M31", "muestrame NGC 1300 en infrarrojo", "morfologia de M87"

2. "observation_planning" — quiere planificar una observacion desde un lugar concreto.
   Menciona ubicacion (ciudad, pais, coordenadas terrestres) y/o tiempo
   ("esta noche", "manana", "cuando puedo ver").
   Ejemplos: "quiero observar M31 desde Barcelona", "cuando puedo ver Saturno desde Madrid"

Responde SOLO con JSON: {"intent": "galaxy_analysis"} o {"intent": "observation_planning"}
Si el mensaje es ambiguo o no encaja, responde {"intent": "galaxy_analysis"}.\
"""


class IntentClassifier:
    """Routes user messages to the correct backend using lightweight LLM classification."""

    def __init__(self, model: str = "gpt-4.1-mini") -> None:
        self._client = OpenAI()
        self._model = model

    async def classify(self, message: str) -> Intent:
        """Classify the intent of the user message."""
        if not message or not message.strip():
            return _DEFAULT_INTENT
        try:
            response = await asyncio.to_thread(self._call_openai, message)
            data = json.loads(response)
            intent_raw = data.get("intent", _DEFAULT_INTENT)
            if intent_raw not in ("galaxy_analysis", "observation_planning"):
                return _DEFAULT_INTENT
            intent: Intent = intent_raw
            logger.info("intent_classified", extra={"intent": intent, "message": message[:80]})
            return intent
        except Exception:
            logger.warning("intent_classification_failed", exc_info=True)
            return _DEFAULT_INTENT

    def _call_openai(self, message: str) -> str:
        """Synchronous OpenAI call executed in a thread via asyncio.to_thread."""
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            timeout=15,
        )
        return response.choices[0].message.content or "{}"
