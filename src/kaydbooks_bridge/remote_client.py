"""Hermes stdio client: one scoped token, no local Bridge config or database access."""

import argparse
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .config import BridgeError, outside_repository, strict_keys
from .remote_mcp import TOOL_NAMES


def settings(path):
    path = outside_repository(Path(path))
    if path.stat().st_size > 16384:
        raise BridgeError("bounded remote client configuration required")
    value = json.loads(path.read_text(encoding="utf-8"))
    strict_keys(value, {"url", "token_file", "tools"})
    url = value["url"]
    if not isinstance(url, str):
        raise BridgeError("private HTTPS MCP URL required")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise BridgeError("private HTTPS MCP URL required")
    token_path = Path(value["token_file"])
    if not token_path.is_absolute():
        raise BridgeError("absolute private token path required")
    outside_repository(token_path)
    allowed = value["tools"]
    if (
        not isinstance(allowed, list)
        or len(allowed) > len(TOOL_NAMES)
        or any(not isinstance(name, str) or name not in TOOL_NAMES for name in allowed)
        or len(set(allowed)) != len(allowed)
    ):
        raise BridgeError("distinct supported remote tools required")
    return value


def credential(path):
    path = outside_repository(Path(path))
    if path.stat().st_size > 4096:
        raise BridgeError("bounded scoped credential required")
    value = json.loads(path.read_text(encoding="utf-8"))
    strict_keys(value, {"token"})
    token = value["token"]
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 512
        or not token.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise BridgeError("valid scoped credential required")
    return token


def http_client(headers=None, timeout=None, auth=None):
    import httpx

    # Never forward the credential through a redirect or an inherited proxy.
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30, read=300),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )


@asynccontextmanager
async def remote_session(value):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = credential(value["token_file"])
    async with (
        streamablehttp_client(
            value["url"],
            headers={"Authorization": "Bearer " + token},
            httpx_client_factory=http_client,
        ) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


class RemoteClient:
    def __init__(self, config_path, *, connect=remote_session):
        self.config_path = config_path
        self.connect = connect
        settings(config_path)

    async def list_tools(self):
        value = settings(self.config_path)
        allowed = set(value["tools"])
        found, cursor = {}, None
        async with self.connect(value) as session:
            for _ in range(16):
                result = await session.list_tools(cursor=cursor)
                for tool in result.tools:
                    if tool.name in allowed:
                        if tool.name in found:
                            raise BridgeError("duplicate remote tool schema")
                        found[tool.name] = tool
                cursor = result.nextCursor
                if not cursor:
                    return list(found.values())
        raise BridgeError("remote tool inventory exceeded page limit")

    async def call_tool(self, name, arguments):
        from mcp.types import CallToolResult, TextContent

        try:
            value = settings(self.config_path)
            if name not in value["tools"] or not isinstance(arguments, dict):
                raise BridgeError("tool not allowed")
            # A fresh authenticated connection observes token-file rotation. Calls
            # are not automatically retried: preparation can have durable effects.
            async with self.connect(value) as session:
                return await session.call_tool(name, arguments)
        except Exception:
            return CallToolResult(
                isError=True,
                content=[
                    TextContent(
                        type="text",
                        text="Remote request failed; check access and service health. No automatic retry was made.",
                    )
                ],
            )


def create_server(client):
    from mcp.server.lowlevel import Server

    server = Server("KaydBooks scoped remote client")

    @server.list_tools()
    async def list_tools():
        try:
            return await client.list_tools()
        except Exception:
            raise BridgeError(
                "Remote tool inventory unavailable; check access and service health"
            ) from None

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        return await client.call_tool(name, arguments)

    return server


async def serve(path):
    from mcp.server.stdio import stdio_server

    server = create_server(RemoteClient(path))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scoped remote MCP client for Hermes")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--check", action="store_true", help="Authenticate and list configured tools, then exit"
    )
    args = parser.parse_args(argv)
    # Third-party transport exceptions may carry request details. Protocol results
    # provide fixed failures; keep library diagnostics out of the agent transcript.
    for name in ("httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.check:
            tools = asyncio.run(RemoteClient(args.config).list_tools())
            print(json.dumps({"status": "ready", "tools": [tool.name for tool in tools]}))
            return 0
        asyncio.run(serve(args.config))
        return 0
    except Exception:
        print(
            "Scoped MCP client stopped; inspect private configuration and service health.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
