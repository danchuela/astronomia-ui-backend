"""LLM-based intent classifier for routing requests to the appropriate gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from typing import Literal

from openai import OpenAI

logger = logging.getLogger(__name__)

Intent = Literal["galaxy_analysis", "observation_planning"]

_DEFAULT_INTENT: Intent = "galaxy_analysis"
_SOLAR_SYSTEM_PATTERNS = [
    r"\bsistema\s+solar\b",
    r"\bsol\b",
    r"\bsolar(es)?\b",
    r"\bluna\b",
    r"\blunar(es)?\b",
    r"\bmercurio\b",
    r"\bmercury\b",
    r"\bvenus\b",
    r"\bmarte\b",
    r"\bmars\b",
    r"\bjupiter\b",
    r"\bsaturno\b",
    r"\bsaturn\b",
    r"\burano\b",
    r"\buranus\b",
    r"\bneptuno\b",
    r"\bneptune\b",
    r"\bpluton\b",
    r"\bpluto\b",
    r"\bcometa\b",
    r"\bcometas\b",
    r"\bcomet\b",
    r"\basteroide\b",
    r"\basteroides\b",
    r"\basteroid\b",
    r"\beclipse\b",
    r"\beclipses\b",
    r"\bmeteoro\b",
    r"\bmeteoros\b",
    r"\bmeteorito\b",
    r"\bmeteoritos\b",
    r"\bmeteor\b",
    r"\bmeteor\s+shower\b",
]
_GENERAL_INFO_PATTERNS = [
    r"\bque\s+(es|son)\b",
    r"\bque\s+sabes\s+(de|sobre|acerca\s+de)\b",
    r"\bque\s+tipo\s+de\s+objeto\b",
    r"\bcual(es)?\s+(es|son)\b.*\b(distancia|magnitud|redshift|corrimiento|tipo|caracteristicas|propiedades)\b",
    r"\b(con|de)\s+que\s+caracteristicas\b",
    r"\bcaracteristicas?\s+especial(es)?\b",
    r"\b(cuentame|hablame|describe|explica|explicame)\b",
    r"\b(info|informacion|datos|curiosidades|propiedades)\b",
    r"\b(distancia|magnitud|redshift|corrimiento|constelacion|descubrimiento|historia|peculiar|especial)\b",
]
_IMAGE_ANALYSIS_PATTERNS = [
    r"\banaliz(a|ar|ame|alo|ala|alo|ala|emos|is)\b",
    r"\banalisis\b",
    r"\bmorfologia\b",
    r"\bmorfologic[oa]s?\b",
    r"\bsegment(a|ar|acion)\b",
    r"\bisofotas?\b",
    r"\bfotometria\b",
    r"\bperfil\s+de\s+brillo\b",
    r"\bsersic\b",
    r"\bcas\b",
    r"\bcontornos?\b",
    r"\bmed(ir|icion|iciones|idas?)\b",
    r"\bcalcul(a|ar)\b",
]

_SYSTEM_PROMPT = """\
Eres un clasificador de intenciones para una plataforma de astronomia.
Clasifica el mensaje del usuario en UNA de estas categorias:

1. "galaxy_analysis" — quiere ver, analizar, segmentar o medir un objeto celeste.
   NO menciona ubicacion terrestre ni momento temporal para observar.
   Ejemplos: "analiza M31", "muestrame NGC 1300 en infrarrojo", "morfologia de M87"
   Incluye objetos de cielo profundo como cumulos, nebulosas o galaxias:
   "quiero ver las Pleyades", "muestrame Orion", "busca M45".

2. "observation_planning" — quiere planificar una observacion desde un lugar concreto.
   Menciona ubicacion (ciudad, pais, coordenadas terrestres) y/o tiempo
   ("esta noche", "manana", "cuando puedo ver").
   Ejemplos: "quiero observar M31 desde Barcelona", "cuando puedo ver Saturno desde Madrid"
   SIEMPRE usa esta categoria para objetos del Sistema Solar, aunque no mencione
   ubicacion ni fecha: Sol, Luna, Mercurio, Venus, Marte, Jupiter, Saturno,
   Urano, Neptuno, Pluton, cometas o asteroides.
   Tambien usa esta categoria para preguntas informativas generales sobre un
   cuerpo u objeto celeste cuando NO piden abrir visor ni analizar imagen.
   Ejemplos: "que es UGC10214", "con que caracteristicas especiales cuenta UGC10214",
   "cuentame sobre M81", "cual es la distancia de M87".

Responde SOLO con JSON: {"intent": "galaxy_analysis"} o {"intent": "observation_planning"}
Si el mensaje es ambiguo o no encaja, responde {"intent": "galaxy_analysis"}.\
"""


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def is_solar_system_request(message: str) -> bool:
    """Return True when the message references Solar System targets."""
    normalized = _normalize_text(message)
    return any(re.search(pattern, normalized) for pattern in _SOLAR_SYSTEM_PATTERNS)


def is_general_information_request(message: str) -> bool:
    """Return True for general info questions handled by the n8n knowledge flow."""
    normalized = _normalize_text(message)
    if not normalized.strip():
        return False
    if any(re.search(pattern, normalized) for pattern in _IMAGE_ANALYSIS_PATTERNS):
        return False
    return any(re.search(pattern, normalized) for pattern in _GENERAL_INFO_PATTERNS)


class IntentClassifier:
    """Routes user messages to the correct backend using lightweight LLM classification."""

    def __init__(self, model: str = "gpt-4.1-mini") -> None:
        self._client = OpenAI()
        self._model = model

    async def classify(self, message: str) -> Intent:
        """Classify the intent of the user message."""
        if not message or not message.strip():
            return _DEFAULT_INTENT
        if is_solar_system_request(message):
            logger.info(
                "intent_classified",
                extra={"intent": "observation_planning", "reason": "solar_system_target"},
            )
            return "observation_planning"
        if is_general_information_request(message):
            logger.info(
                "intent_classified",
                extra={"intent": "observation_planning", "reason": "general_information"},
            )
            return "observation_planning"
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
