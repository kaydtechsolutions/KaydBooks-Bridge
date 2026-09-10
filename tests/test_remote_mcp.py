"""Authenticated Streamable HTTP boundary and narrow remote tool contract."""

# ruff: noqa: F811
import json

import pytest
from fastapi.testclient import TestClient

from kaydbooks_bridge.config import BridgeError
from kaydbooks_bridge.remote_mcp import TOOL_NAMES, create_app, create_server, load_policy
from test_bridge import TOKENS, setup  # noqa: F401

ORIGIN = "https://bridge.test"
MCP_HEADERS = {
    "authorization": f"Bearer {TOKENS['operator-a']}",
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@pytest.fixture
def remote(setup, tmp_path):
    _, config_path, _, _ = setup
    policy_path = tmp_path / "remote-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "principals": {
                    "operator-a": {
                        "source": "chatgpt",
                        "companies": ["company-a"],
                        "tools": ["company_catalog_v1", "entry_status_v1"],
                    }
                },
            }
        )
    )
    return create_app(config_path, policy_path, ORIGIN), policy_path


def rpc(method, params=None, request_id=1):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def test_remote_transport_requires_auth_host_and_origin(remote):
    app, _ = remote
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/mcp", json=rpc("initialize")).status_code == 401
        assert (
            client.post(
                "/mcp", json=rpc("initialize"), headers={**MCP_HEADERS, "host": "evil.test"}
            ).status_code
            == 403
        )
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ready",
            "service": "kaydbooks-remote-mcp",
            "version": "v1",
        }
        assert (
            client.post(
                "/mcp",
                json=rpc("initialize"),
                headers={**MCP_HEADERS, "origin": "https://evil.test"},
            ).status_code
            == 403
        )


def test_streamable_http_lists_only_versioned_allowlist(remote):
    app, _ = remote
    with TestClient(app, base_url=ORIGIN) as client:
        initialized = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "synthetic-test", "version": "1"},
                },
            ),
        )
        assert initialized.status_code == 200
        result = client.post("/mcp", headers=MCP_HEADERS, json=rpc("tools/list", {}, 2)).json()
        names = {tool["name"] for tool in result["result"]["tools"]}
        assert names == TOOL_NAMES
        assert all(name.endswith("_v1") for name in names)
        assert not any(word in " ".join(names) for word in ("sql", "shell", "qbxml"))


def test_source_policy_enforces_company_and_tool(remote):
    app, _ = remote
    with TestClient(app, base_url=ORIGIN) as client:
        accepted = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc(
                "tools/call",
                {"name": "company_catalog_v1", "arguments": {"company": "company-a"}},
            ),
        ).json()
        assert accepted["result"]["isError"] is False
        denied_company = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc(
                "tools/call",
                {"name": "company_catalog_v1", "arguments": {"company": "company-b"}},
                2,
            ),
        ).json()
        denied_tool = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc(
                "tools/call",
                {
                    "name": "entry_preview_v1",
                    "arguments": {"company": "company-a", "job_id": "missing"},
                },
                3,
            ),
        ).json()
        assert denied_company["result"]["isError"] is True
        assert denied_tool["result"]["isError"] is True


def test_rate_limit_is_per_authenticated_principal(remote, setup):
    _, policy_path = remote
    app = create_app(setup[1], policy_path, ORIGIN, rate=1)
    with TestClient(app, base_url=ORIGIN) as client:
        first = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json=rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "synthetic-test", "version": "1"},
                },
            ),
        )
        second = client.post("/mcp", headers=MCP_HEADERS, json=rpc("tools/list", {}, 2))
        assert first.status_code == 200
        assert second.status_code == 429


def test_invalid_remote_policy_fails_closed(setup, tmp_path):
    path = tmp_path / "bad-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "principals": {
                    "operator-a": {
                        "source": "whatsapp",
                        "companies": ["company-a"],
                        "tools": ["shell_v1"],
                    }
                },
            }
        )
    )
    with pytest.raises(BridgeError):
        load_policy(path)
    with pytest.raises(BridgeError):
        create_server(setup[1], path, ORIGIN)
