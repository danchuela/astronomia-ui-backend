"""Quick-reply analysis buttons after "Muéstrame <objeto>" / "Show me <object>".

When the Galaxy API has only placed an object in the viewer (task "resolve"),
its summary ends with a fixed, code-generated sentence listing the analyses
the user can ask for. The BFF turns that list into data (`suggested_actions`
on the `end` event) so the frontend can show one button per analysis, and
shortens the sentence accordingly. Writing a free request keeps working.

The sentence exists in Spanish and in English; its language decides the
language of the buttons.
"""

from __future__ import annotations

import re
from typing import Any

# Fixed texts of the Galaxy API (packages/galaxy_agent/agent_tools.py and orchestrator.py).
VIEWER_READY_MARKER = "Cuando estés listo, dime qué quieres analizar"
VIEWER_READY_MARKER_EN = "When you are ready, tell me what you want to analyse"
SHORT_PROMPT = (
    "Cuando estés listo, elige un análisis con los botones o escríbeme qué quieres analizar."
)
SHORT_PROMPT_EN = (
    "When you are ready, choose an analysis with the buttons or tell me what you want to analyse."
)

_MARKERS = {"es": VIEWER_READY_MARKER, "en": VIEWER_READY_MARKER_EN}
_SHORT = {"es": SHORT_PROMPT, "en": SHORT_PROMPT_EN}
_LABEL_RE = {
    "es": re.compile(r"Aquí tienes (.+?)\. Ajusta"),
    "en": re.compile(r"Here is (.+?)\. Adjust"),
}
_FALLBACK_NAME = {"es": "este objeto", "en": "this object"}

# (button label, message sent when clicked); {name} is the object.
_ACTIONS = {
    "es": (
        ("Morfología completa", "Analiza la morfología completa de {name}"),
        ("Segmentación e isofotas", "Haz la segmentación e isofotas de {name}"),
        ("Fotometría básica", "Haz la fotometría básica de {name}"),
        ("Perfil de brillo", "Calcula el perfil de brillo de {name}"),
    ),
    "en": (
        ("Full morphology", "Analyse the full morphology of {name}"),
        ("Segmentation and isophotes", "Do the segmentation and isophotes of {name}"),
        ("Basic photometry", "Do the basic photometry of {name}"),
        ("Brightness profile", "Compute the brightness profile of {name}"),
    ),
}


def _language(summary: Any) -> str | None:
    if not isinstance(summary, str):
        return None
    for lang, marker in _MARKERS.items():
        if marker in summary:
            return lang
    return None


def is_viewer_ready(summary: Any) -> bool:
    return _language(summary) is not None


def shorten_summary(summary: str) -> str:
    """Replace the trailing list of analyses with a pointer to the buttons."""
    lang = _language(summary) or "es"
    return summary[: summary.index(_MARKERS[lang])] + _SHORT[lang]


def suggested_actions(summary: str, object_name: Any = None) -> list[dict[str, str]]:
    lang = _language(summary) or "es"
    name = object_name if isinstance(object_name, str) and object_name.strip() else None
    if name is None:
        match = _LABEL_RE[lang].search(summary)
        name = match.group(1) if match else _FALLBACK_NAME[lang]
    return [
        {"label": label, "message": message.format(name=name)} for label, message in _ACTIONS[lang]
    ]
