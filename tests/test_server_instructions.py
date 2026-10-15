# User intent: the tm MCP server must ship short server instructions at initialize, because they load every
# session even when the tool list is deferred; they carry the write-receipt and sync rules agents need.
"""The FastMCP initialize result carries the server instructions."""
import asyncio

from fastmcp import Client

from taskmaster import backlog_server as bs


def test_initialize_result_carries_instructions():
    async def initialize():
        async with Client(bs.mcp._inner) as client:
            return client.initialize_result

    result = asyncio.run(initialize())
    assert result.instructions
    assert "[seq N]" in result.instructions
    assert "backlog_sync" in result.instructions
    assert "backlog_store_status" in result.instructions
    assert "export_pending" in result.instructions
