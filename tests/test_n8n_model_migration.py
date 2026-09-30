import copy

import pytest

from scripts.prepare_n8n_luna import REQUEST_NODES, migrate_models, prepare_dev


def _workflow() -> dict:
    return {
        "id": "production",
        "active": True,
        "nodes": [
            {
                "name": name,
                "type": "n8n-nodes-base.code",
                "parameters": {
                    "jsCode": "const request = {\n model: 'gpt-4o-mini',\n"
                    " temperature: 0.7,\n max_tokens: 500\n};\nreturn request;"
                },
            }
            for name in sorted(REQUEST_NODES)
        ]
        + [
            {
                "name": "WEBHOOK_START",
                "type": "n8n-nodes-base.webhook",
                "parameters": {"path": "astronomia"},
                "webhookId": "prod-hook",
            },
            {"name": "LOG", "type": "n8n-nodes-base.postgres", "parameters": {}},
            {"name": "ALERT", "type": "n8n-nodes-base.telegram", "parameters": {}},
        ],
        "connections": {"WEBHOOK_START": {"main": [[{"node": "PREPARE_UNDERSTANDING"}]]}},
        "settings": {"errorWorkflow": "prod-alerts", "timezone": "Europe/Madrid"},
        "staticData": {"production": "history"},
    }


def test_dev_workflow_is_isolated_and_keeps_request_limits() -> None:
    original = _workflow()
    before = copy.deepcopy(original)
    result = prepare_dev(original)
    assert original == before
    assert result["id"] != original["id"]
    assert not result["active"]
    assert result["staticData"] is None
    assert result["settings"] == {"timezone": "Europe/Madrid"}
    assert result["connections"] == original["connections"]
    nodes = {node["name"]: node for node in result["nodes"]}
    assert nodes["WEBHOOK_START"]["parameters"]["path"] == "astronomia-luna-dev"
    assert nodes["WEBHOOK_START"]["webhookId"] != "prod-hook"
    assert nodes["LOG"]["disabled"] and nodes["ALERT"]["disabled"]
    for name in REQUEST_NODES:
        code = nodes[name]["parameters"]["jsCode"]
        assert "gpt-6-luna" in code
        assert "reasoning_effort: 'none'" in code
        assert "max_completion_tokens: 500" in code
        assert "temperature: 0.7" in code
        assert "max_tokens:" not in code


def test_migration_rejects_missing_or_changed_request_nodes() -> None:
    workflow = _workflow()
    workflow["nodes"].pop(0)
    with pytest.raises(ValueError, match="Missing request nodes"):
        migrate_models(workflow)
    workflow = _workflow()
    workflow["nodes"][0]["parameters"]["jsCode"] = "return {};"
    with pytest.raises(ValueError, match="Unexpected model configuration"):
        migrate_models(workflow)
