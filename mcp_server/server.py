"""MCP server (stdio) exposing mmm_readiness_report to an agent."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

import readiness  # noqa: E402

mcp = MCPServer("mmm-readiness")


def _call(fn, *args):
    # The SDK hides the message of any exception that is not a ToolError, so re-raise ours as one.
    try:
        return fn(*args)
    except readiness.ReadinessError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def readiness_summary() -> dict:
    """Count MMM channels by readiness status (ready / review / blocked) and list what blocks modeling."""
    return _call(readiness.readiness_summary)


@mcp.tool()
def channel_readiness(channel: str) -> dict:
    """Explain every data-quality issue flagged for one MMM channel, in business terms, with the
    recommended action: fix the data, review, or validate with an experiment."""
    return _call(readiness.channel_readiness, channel)


@mcp.tool()
def list_channels() -> list[dict]:
    """List every MMM channel in the readiness report with its status and share of total spend."""
    return _call(readiness.list_channels)


if __name__ == "__main__":
    mcp.run()
