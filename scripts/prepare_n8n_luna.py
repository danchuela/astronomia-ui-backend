"""Prepare an unpublished development copy of the astronomIA n8n workflow."""

from __future__ import annotations

import argparse
import copy
import json
import re
import uuid
from pathlib import Path
from typing import Any

REQUEST_NODES = {"PREPARE_UNDERSTANDING", "RESPOND_INFO", "PREPARE_PROMPT", "SUGGEST_PROMPT"}
SIDE_EFFECT_TYPES = {"n8n-nodes-base.postgres", "n8n-nodes-base.telegram"}


def migrate_models(workflow: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(workflow)
    found: set[str] = set()
    for node in migrated["nodes"]:
        if node["name"] not in REQUEST_NODES:
            continue
        code = node["parameters"]["jsCode"]
        code, models = re.subn(
            r"(?m)^(\s*)model:\s*(['\"])gpt-4o-mini\2,",
            r"\1model: 'gpt-6-luna',\n\1reasoning_effort: 'none',",
            code,
        )
        code, limits = re.subn(r"\bmax_tokens\s*:", "max_completion_tokens:", code)
        if models != 1 or limits != 1:
            raise ValueError(f"Unexpected model configuration in {node['name']}")
        node["parameters"]["jsCode"] = code
        found.add(node["name"])
    if found != REQUEST_NODES:
        raise ValueError(f"Missing request nodes: {sorted(REQUEST_NODES - found)}")
    return migrated


def prepare_dev(workflow: dict[str, Any]) -> dict[str, Any]:
    migrated = migrate_models(workflow)
    result = {
        "id": uuid.uuid4().hex[:16],
        "versionId": str(uuid.uuid4()),
        "name": "astronomIA - Luna (dev)",
        "active": False,
        "nodes": migrated["nodes"],
        "connections": migrated["connections"],
        "settings": dict(migrated.get("settings") or {}),
        "pinData": {},
        "staticData": None,
    }
    result["settings"].pop("errorWorkflow", None)
    for node in result["nodes"]:
        if node["type"] == "n8n-nodes-base.webhook":
            node["parameters"]["path"] += "-luna-dev"
            node["webhookId"] = str(uuid.uuid4())
        if node["type"] in SIDE_EFFECT_TYPES:
            node["disabled"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("Use a separate output file; the source must be preserved.")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    if isinstance(source, list):
        if len(source) != 1:
            parser.error("Export exactly one astronomIA workflow.")
        source = source[0]
    result = prepare_dev(source)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump([result], output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(f"Prepared {result['name']} ({result['id']}), unpublished.")


if __name__ == "__main__":
    main()
