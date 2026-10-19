"""Quick-reply analysis buttons after "Muéstrame <objeto>".

When the Galaxy API has only placed an object in the viewer (task "resolve"),
its summary ends with a fixed, code-generated sentence listing the analyses
the user can ask for. The BFF turns that list into data (`suggested_actions`
on the `end` event) so the frontend can show one button per analysis, and
shortens the sentence accordingly. Writing a free request keeps working.
"""

from __future__ import annotations

import re
from typing import Any

# Fixed text of the Galaxy API (packages/galaxy_agent/agent_tools.py and orchestrator.py).
VIEWER_READY_MARKER = "Cuando estés listo, dime qué quieres analizar"
SHORT_PROMPT = (
    "Cuando estés listo, elige un análisis con los botones o escríbeme qué quieres analizar."
)

_LABEL_RE = re.compile(r"Aquí tienes (.+?)\. Ajusta")

# (button label, message sent when clicked); {name} is the object.
_ACTIONS = (
    ("Morfología completa", "Analiza la morfología completa de {name}"),
    ("Segmentación e isofotas", "Haz la segmentación e isofotas de {name}"),
    ("Fotometría básica", "Haz la fotometría básica de {name}"),
    ("Perfil de brillo", "Calcula el perfil de brillo de {name}"),
)


def is_viewer_ready(summary: Any) -> bool:
    return isinstance(summary, str) and VIEWER_READY_MARKER in summary


def shorten_summary(summary: str) -> str:
    """Replace the trailing list of analyses with a pointer to the buttons."""
    return summary[: summary.index(VIEWER_READY_MARKER)] + SHORT_PROMPT


def suggested_actions(summary: str, object_name: Any = None) -> list[dict[str, str]]:
    name = object_name if isinstance(object_name, str) and object_name.strip() else None
    if name is None:
        match = _LABEL_RE.search(summary)
        name = match.group(1) if match else "este objeto"
    return [{"label": label, "message": message.format(name=name)} for label, message in _ACTIONS]
