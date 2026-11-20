from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.core.config import Settings


class McpToolError(RuntimeError):
    pass


class NexusOpsMcpToolClient:
    """Optional stdio MCP client for read-only NexusOps operational tools."""

    READ_ONLY_TOOLS = {
        "nexusops_list_incidents",
        "nexusops_get_incident",
        "nexusops_list_alerts",
        "nexusops_search_knowledge",
        "nexusops_list_approvals",
        "nexusops_list_tool_audits",
    }

    def __init__(self, settings: Settings):
        self.settings = settings

    async def call_readonly_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if name not in self.READ_ONLY_TOOLS:
            raise McpToolError(f"MCP tool is not in the read-only allowlist: {name}")
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments=arguments or {})
                message = self._result_message(result)
                if getattr(result, "isError", False):
                    raise McpToolError(f"{name} failed: {message}")
                return message
        except McpToolError:
            raise
        except Exception as exc:
            raise McpToolError(f"NexusOps MCP call failed: {type(exc).__name__}: {exc}") from exc

    def _result_message(self, result: Any) -> str:
        parts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            parts.append(text if text is not None else str(item))
        if parts:
            return "\n".join(parts)
        structured = getattr(result, "structuredContent", None)
        return str(structured if structured is not None else result)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise McpToolError("Install the mcp dependency to call NexusOps MCP tools") from exc
        project_root = self.settings.project_root
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(project_root) if not python_path else f"{project_root}{os.pathsep}{python_path}"
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_tools.server"],
            env=env,
            cwd=str(project_root),
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session
