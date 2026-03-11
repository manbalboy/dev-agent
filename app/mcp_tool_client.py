"""Optional MCP client used for shadow tool execution."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import importlib
import importlib.util
import os
import shlex
from typing import Any, Dict, List


@dataclass(frozen=True)
class MCPToolCallResult:
    """Result of one optional MCP shadow tool call."""

    enabled: bool
    available: bool
    ok: bool
    tool: str
    server_command: str
    detail: str
    result_preview: str = ""
    listed_tools: List[str] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MCPToolClient:
    """Minimal MCP stdio client for shadow tool execution."""

    def __init__(self, *, server_command_env: str = "AGENTHUB_MCP_SHADOW_SERVER_COMMAND") -> None:
        self.server_command_env = server_command_env

    def call_tool_shadow(self, *, tool_name: str, arguments: Dict[str, Any]) -> MCPToolCallResult:
        """Try one shadow tool call via MCP stdio without affecting primary execution."""

        server_command = str(os.getenv(self.server_command_env, "")).strip()
        if not server_command:
            return MCPToolCallResult(
                enabled=False,
                available=False,
                ok=False,
                tool=tool_name,
                server_command="",
                detail="server_not_configured",
            )
        if importlib.util.find_spec("mcp") is None:
            return MCPToolCallResult(
                enabled=True,
                available=False,
                ok=False,
                tool=tool_name,
                server_command=server_command,
                detail="mcp_sdk_not_installed",
            )

        try:
            return asyncio.run(
                self._call_tool_stdio(
                    server_command=server_command,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )
        except Exception as error:  # noqa: BLE001
            return MCPToolCallResult(
                enabled=True,
                available=True,
                ok=False,
                tool=tool_name,
                server_command=server_command,
                detail=f"shadow_call_failed: {error}",
            )

    async def _call_tool_stdio(
        self,
        *,
        server_command: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> MCPToolCallResult:
        """Execute one stdio MCP tools/list + tools/call sequence."""

        parts = shlex.split(server_command)
        if not parts:
            return MCPToolCallResult(
                enabled=True,
                available=False,
                ok=False,
                tool=tool_name,
                server_command=server_command,
                detail="server_not_configured",
            )

        mcp_module = importlib.import_module("mcp")
        stdio_module = importlib.import_module("mcp.client.stdio")
        ClientSession = getattr(mcp_module, "ClientSession")
        StdioServerParameters = getattr(mcp_module, "StdioServerParameters")
        stdio_client = getattr(stdio_module, "stdio_client")

        params = StdioServerParameters(command=parts[0], args=parts[1:])
        async with stdio_client(params) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tool_listing = await session.list_tools()
                listed_tools = [str(getattr(item, "name", "")).strip() for item in getattr(tool_listing, "tools", [])]
                if tool_name not in listed_tools:
                    return MCPToolCallResult(
                        enabled=True,
                        available=True,
                        ok=False,
                        tool=tool_name,
                        server_command=server_command,
                        detail="tool_not_listed",
                        listed_tools=[name for name in listed_tools if name],
                    )
                call_result = await session.call_tool(tool_name, arguments=arguments)
                preview = str(call_result)[:1200]
                return MCPToolCallResult(
                    enabled=True,
                    available=True,
                    ok=True,
                    tool=tool_name,
                    server_command=server_command,
                    detail="ok",
                    result_preview=preview,
                    listed_tools=[name for name in listed_tools if name],
                )
