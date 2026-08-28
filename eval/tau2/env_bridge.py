"""HTTP and MCP bridges over the Orchestrator's toolkit.

Do not start `tau2 domain`; that is a different Environment instance.
Successful `toolkit.use_tool` calls from either transport append the same
`BridgeCall` list used for trajectory projection.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel.server import Server as McpServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool
from pydantic import BaseModel
from starlette.routing import Mount
from tau2.environment.environment import Environment
from tau2.environment.tool import Tool
from tau2.environment.toolkit import ToolKitBase


@dataclass(frozen=True)
class BridgeCall:
    """One successful toolkit.use_tool that went through HTTP or MCP.

    `content` is Environment.to_json_str of the live return value, matching
    what set_state compares during official DB replay.
    """

    name: str
    arguments: dict[str, Any]
    content: str


def toolkit_from_tools(tools: list[Tool]) -> ToolKitBase:
    """Recover the bound toolkit; it is the same object as Environment.tools."""
    if not tools:
        raise ValueError("no domain tools")
    func = getattr(tools[0], "_func", None)
    toolkit = getattr(func, "__self__", None)
    if toolkit is None:
        raise ValueError("tools are not bound toolkit methods; cannot share the Environment DB")
    return toolkit


def jsonable(value: Any) -> Any:
    """JSON-encode a toolkit return value for the HTTP response."""
    if isinstance(value, BaseModel):
        return json.loads(value.model_dump_json())
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.loads(json.dumps(value, default=str))


def write_tools_markdown(workspace, base_url: str, tools: list[Tool], mcp_url: str | None = None) -> None:
    """Write TOOLS.md: MCP names first, HTTP curl as backup."""
    mcp = mcp_url or f"{base_url}/mcp"
    lines = [
        "# Domain API",
        "",
        "Prefer the MCP tools already registered on this agent, named `mcp__tau2__<tool>`.",
        "Those tools write the scored domain database. Local files do not complete the request.",
        "Do not use bash curl unless an MCP tool is missing.",
        "",
        f"MCP URL: {mcp}",
        f"HTTP backup: {base_url}",
        "127.0.0.1 is localhost, not the public internet.",
        "",
        "```bash",
        f"curl -sS -X POST {base_url}/tools/TOOL_NAME \\",
        "  -H 'Content-Type: application/json' \\",
        "  -d '{\"arg\":\"value\"}'",
        "```",
        "",
        "## Tools",
        "",
    ]
    for tool in tools:
        schema = tool.openai_schema["function"]
        lines.append(f"### {tool.name} (`mcp__tau2__{tool.name}`)")
        lines.append("")
        lines.append(schema.get("description") or "")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(schema.get("parameters") or {}, indent=2))
        lines.append("```")
        lines.append("")
    workspace.joinpath("TOOLS.md").write_text("\n".join(lines), encoding="utf-8")
    workspace.joinpath("ENV_API.txt").write_text(f"{base_url}\n{mcp}\n", encoding="utf-8")


class EnvBridge:
    """Serve assistant tools on 127.0.0.1 as HTTP and Streamable HTTP MCP."""

    def __init__(self, toolkit: ToolKitBase, tools: list[Tool]):
        self.toolkit = toolkit
        self.tools = {tool.name: tool for tool in tools}
        self.calls: list[BridgeCall] = []
        self.base_url: str | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def mcp_url(self) -> str | None:
        """Streamable HTTP MCP endpoint, or None before start()."""
        if self.base_url is None:
            return None
        return f"{self.base_url}/mcp"

    def start(self) -> str:
        """Bind an ephemeral localhost port and serve until stop()."""
        if self._thread is not None:
            raise RuntimeError("bridge already started")
        app = self._app()
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="tau2-env-bridge", daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("env bridge thread exited before listen")
            if time.time() > deadline:
                raise RuntimeError("env bridge failed to start within 10s")
            time.sleep(0.02)
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    def stop(self) -> None:
        """Ask uvicorn to exit and join the daemon thread."""
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        self.base_url = None
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)

    def _record_success(self, name: str, args: dict[str, Any], result: Any) -> None:
        self.calls.append(
            BridgeCall(
                name=name,
                arguments=deepcopy(args),
                content=Environment.to_json_str(result),
            )
        )

    def _app(self) -> FastAPI:
        toolkit = self.toolkit
        known = self.tools
        bridge = self
        mcp_server = McpServer("tau2")
        session_manager = StreamableHTTPSessionManager(
            app=mcp_server,
            stateless=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
            ),
        )

        @mcp_server.list_tools()
        async def list_mcp_tools() -> list[McpTool]:
            listed: list[McpTool] = []
            for tool in known.values():
                function = tool.openai_schema["function"]
                parameters = function.get("parameters") or {"type": "object", "properties": {}}
                listed.append(
                    McpTool(
                        name=tool.name,
                        description=function.get("description") or "",
                        inputSchema=parameters,
                    )
                )
            return listed

        @mcp_server.call_tool()
        async def call_mcp_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult | list[TextContent]:
            if name not in known:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"unknown tool {name}")],
                    isError=True,
                )
            args = arguments or {}
            if not isinstance(args, dict):
                return CallToolResult(
                    content=[TextContent(type="text", text="JSON object required")],
                    isError=True,
                )
            try:
                result = toolkit.use_tool(tool_name=name, **args)
            except Exception as exc:
                return CallToolResult(
                    content=[TextContent(type="text", text=str(exc))],
                    isError=True,
                )
            bridge._record_success(name, args, result)
            return [TextContent(type="text", text=Environment.to_json_str(result))]

        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            async with session_manager.run():
                yield

        app = FastAPI(title="tau2 shared environment", lifespan=lifespan)

        @app.get("/health")
        def health() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/tools")
        def list_http_tools() -> list[dict[str, Any]]:
            listed = []
            for tool in known.values():
                function = tool.openai_schema["function"]
                listed.append(
                    {
                        "name": tool.name,
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters") or {},
                    }
                )
            return listed

        @app.post("/tools/{name}")
        async def call_http_tool(name: str, request: Request) -> JSONResponse:
            if name not in known:
                raise HTTPException(status_code=404, detail=f"unknown tool {name}")
            raw = await request.body()
            if raw:
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
            else:
                args = {}
            if not isinstance(args, dict):
                raise HTTPException(status_code=400, detail="JSON object required")
            try:
                result = toolkit.use_tool(tool_name=name, **args)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            bridge._record_success(name, args, result)
            return JSONResponse(content=jsonable(result))

        app.router.routes.append(Mount("/mcp", app=session_manager.handle_request))
        return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def mcp_call_create_task(mcp_url: str) -> str:
    """Call create_task over Streamable HTTP MCP; used by the keyless contrast."""

    async def _run() -> str:
        async with streamablehttp_client(mcp_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                if "create_task" not in names:
                    raise RuntimeError(f"MCP list_tools missing create_task, got {names}")
                result = await session.call_tool(
                    "create_task",
                    {"user_id": "user_1", "title": "Important Meeting"},
                )
                if result.isError:
                    raise RuntimeError(f"MCP create_task returned isError: {result}")
                texts = [block.text for block in result.content if isinstance(block, TextContent)]
                return texts[0] if texts else ""

    return asyncio.run(_run())


def self_test() -> None:
    """HTTP and MCP writes must each change the mock DB hash and record one call."""
    from tau2.domains.mock.environment import get_environment

    _http_write_test(get_environment())
    _mcp_write_test(get_environment())


def _http_write_test(environment: Environment) -> None:
    toolkit = environment.tools
    assert toolkit is not None
    tools = list(toolkit.get_tools().values())
    before = toolkit.get_db_hash()
    bridge = EnvBridge(toolkit, tools)
    base_url = bridge.start()
    try:
        import urllib.request

        body = json.dumps({"user_id": "user_1", "title": "Important Meeting"}).encode()
        request = urllib.request.Request(
            f"{base_url}/tools/create_task",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode())
        after = toolkit.get_db_hash()
        recorded = list(bridge.calls)
    finally:
        bridge.stop()
    if before == after:
        raise SystemExit(f"DB hash did not change after HTTP create_task: {before}")
    if len(recorded) != 1 or recorded[0].name != "create_task":
        raise SystemExit(f"expected one HTTP create_task audit record, got {recorded}")
    print(f"ok: HTTP hash {before} -> {after}")
    print(f"HTTP create_task returned: {payload}")


def _mcp_write_test(environment: Environment) -> None:
    """MCP call_tool only — no HTTP POST — must mutate the same toolkit."""
    toolkit = environment.tools
    assert toolkit is not None
    tools = list(toolkit.get_tools().values())
    before = toolkit.get_db_hash()
    bridge = EnvBridge(toolkit, tools)
    bridge.start()
    mcp_url = bridge.mcp_url
    assert mcp_url is not None
    try:
        payload = mcp_call_create_task(mcp_url)
        after = toolkit.get_db_hash()
        recorded = list(bridge.calls)
    finally:
        bridge.stop()
    if before == after:
        raise SystemExit(f"DB hash did not change after MCP create_task: {before}")
    if len(recorded) != 1 or recorded[0].name != "create_task":
        raise SystemExit(f"expected one MCP create_task audit record, got {recorded}")
    print(f"ok: MCP-only hash {before} -> {after}")
    print(f"MCP create_task returned: {payload}")


if __name__ == "__main__":
    self_test()
