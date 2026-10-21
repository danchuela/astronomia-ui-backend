"""Answer language: Spanish or English.

Rule decided for astronomIA on 2026-10-20 (the same as the n8n workflow, see
astronomia-infra n8n/src/suggest/lang_detect.js): the answer follows the
language of the MESSAGE. If the message does not show it (a name, a number,
"ok"), the previous user messages decide; then the language chosen in the
frontend (``language`` in the request); then Spanish.

Detection is deterministic word counting: free, instant and testable. Keep the
word lists in sync with lang_detect.js.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.schemas import AnalyzeRequest

Lang = Literal["es", "en"]

_EN = frozenset(
    "the what whats can could should i im ill we see from tonight tomorrow today want wanna "
    "would like is are was how when where which why and show please of to observe observing "
    "sky visible next this that with my you your there it its do does tell about will be at "
    "in on hi hello hey thanks thank yes look night give chart analyze analyse morphology any "
    "something good best".split()
)
_ES = frozenset(
    "el la los las que puedo puede ver desde esta este noche manana quiero quisiera de del y "
    "es como cuando donde cual por para hoy observar cielo hola gracias un una en con mi se "
    "lo al hay dame muestrame analiza sugiereme sugerime proximo semana buenas podria tengo "
    "algo si mira mirar carta tambien pero ahora".split()
)
_SPANISH_CHARS = re.compile(r"[¿¡ñáéíóú]", re.IGNORECASE)


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z\s']", " ", folded).replace("'", "").split()


def detect_language(text: str | None) -> Lang | None:
    """'es', 'en', or None when the text does not show it."""
    raw = text or ""
    en = 0
    es = 2 if _SPANISH_CHARS.search(raw) else 0
    for word in _words(raw):
        en += word in _EN
        es += word in _ES
    if en > es:
        return "en"
    if es > en:
        return "es"
    return None


def answer_language(
    message: str | None, history: list[str] | None = None, requested: str | None = None
) -> Lang:
    """history: previous user messages, oldest first (the current one may be the last)."""
    found = detect_language(message)
    if found:
        return found
    for previous in reversed(history or []):
        if previous == message:
            continue
        found = detect_language(previous)
        if found:
            return found
    if requested in ("es", "en"):
        return requested  # type: ignore[return-value]
    return "es"


def request_language(request: AnalyzeRequest) -> Lang:
    """Answer language of a BFF request (its message, its history, then the frontend choice)."""
    users = [m.content for m in (request.messages or []) if m.role == "user"]
    message = request.message if request.message is not None else (users[-1] if users else "")
    return answer_language(message, users, request.language)
