"""The agent-side client holds one token and cannot read Bridge state or widen tools."""

# ruff: noqa: F811
import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from kaydbooks_bridge import remote_client
from kaydbooks_bridge.config import BridgeError
from test_bridge import TOKENS, setup  # noqa: F401
from test_remote_mcp import ORIGIN, remote  # noqa: F401


@pytest.fixture
def client_config(tmp_path):
    token_file = tmp_path / "hermes-token.json"
    token_file.write_text(json.dumps({"token": TOKENS["operator-a"]}))
    path = tmp_path / "remote-client.json"
    path.write_text(
        json.dumps(
            {"url": ORIGIN + "/mcp", "token_file": str(token_file), "tools": ["company_catalog_v1"]}
        )
    )
    return path, token_file


@pytest.mark.parametrize(
    "url",
    [
        "http://bridge.test/mcp",
        "https://bridge.test/other",
        "https://user@bridge.test/mcp",
        "https://bridge.test:8443/mcp",
        "https://bridge.test/mcp?secret=x",
        "https://bridge.test/mcp#x",
    ],
)
def test_bad_destination_rejected(client_config, url):
    path, _ = client_config
    data = json.loads(path.read_text())
    data["url"] = url
    path.write_text(json.dumps(data))
    with pytest.raises(BridgeError):
        remote_client.settings(path)


@pytest.mark.parametrize(
    "tools", [["shell"], ["company_catalog_v1", "company_catalog_v1"], "company_catalog_v1", [None]]
)
def test_no_arbitrary_tool_allowlist(client_config, tools):
    path, _ = client_config
    data = json.loads(path.read_text())
    data["tools"] = tools
    path.write_text(json.dumps(data))
    with pytest.raises(BridgeError):
        remote_client.settings(path)


@pytest.mark.parametrize(
    "value",
    [
        {"token": "short"},
        {"token": "x" * 40, "operator": "y" * 40},
        {"token": "x" * 40 + "\n"},
        {"token": 32},
    ],
)
def test_only_one_valid_token_accepted(client_config, value):
    _, token_file = client_config
    token_file.write_text(json.dumps(value))
    with pytest.raises(BridgeError):
        remote_client.credential(token_file)


def test_http_client_disables_redirects_and_environment_proxy(monkeypatch):
    captured = {}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: captured.update(kwargs))
    remote_client.http_client(headers={"Authorization": "Bearer synthetic"})
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured.get("verify", True) is True


def test_proxy_preserves_errors_and_never_retries_or_expands_tools(client_config):
    path, _ = client_config
    calls = []
    expected = CallToolResult(
        isError=True,
        content=[TextContent(type="text", text="denied")],
        structuredContent={"error": "denied"},
    )

    class Session:
        async def list_tools(self, cursor=None):
            return ListToolsResult(
                tools=[
                    Tool(name=name, inputSchema={"type": "object"})
                    for name in ("company_catalog_v1", "entry_review_v1")
                ]
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if arguments.get("fail"):
                raise RuntimeError(TOKENS["operator-a"])
            return expected

    @asynccontextmanager
    async def connect(value):
        yield Session()

    async def check():
        client = remote_client.RemoteClient(path, connect=connect)
        assert [t.name for t in await client.list_tools()] == ["company_catalog_v1"]
        assert (await client.call_tool("entry_review_v1", {})).isError
        assert calls == []
        result = await client.call_tool("company_catalog_v1", {})
        assert result == expected
        failure = await client.call_tool("company_catalog_v1", {"fail": True})
        assert failure.isError and TOKENS["operator-a"] not in failure.model_dump_json()
        assert len(calls) == 2

    asyncio.run(check())


def test_real_mcp_proxy_observes_rotation_revocation_and_company_scope(
    remote, setup, client_config, monkeypatch
):
    app, _ = remote
    path, token_file = client_config
    _, config_path, config, _ = setup

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            transport=httpx.ASGITransport(app=app),
            follow_redirects=False,
            trust_env=False,
        )

    monkeypatch.setattr(remote_client, "http_client", factory)

    async def check():
        proxy = remote_client.create_server(remote_client.RemoteClient(path))
        async with (
            app.app.router.lifespan_context(app.app),
            create_connected_server_and_client_session(proxy) as session,
        ):
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["company_catalog_v1"]
            result = await session.call_tool("company_catalog_v1", {"company": "company-a"})
            assert not result.isError
            assert json.loads(result.content[0].text)["principal"] == "operator-a"
            denied = await session.call_tool("company_catalog_v1", {"company": "company-b"})
            assert denied.isError
            new = "rotated-" + "q" * 40
            monkeypatch.setenv(config["principals"]["operator-a"]["token_env"], new)
            # Old token in the client file stops working immediately.
            assert (await session.call_tool("company_catalog_v1", {"company": "company-a"})).isError
            token_file.write_text(json.dumps({"token": new}))
            assert not (
                await session.call_tool("company_catalog_v1", {"company": "company-a"})
            ).isError
            config["principals"]["operator-a"]["disabled"] = True
            config_path.write_text(json.dumps(config))
            assert (await session.call_tool("company_catalog_v1", {"company": "company-a"})).isError

    asyncio.run(check())
