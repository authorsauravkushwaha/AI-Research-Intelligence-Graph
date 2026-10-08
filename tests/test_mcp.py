"""The MCP server exposes the same tools as the API."""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("nexus_mcp", ROOT / "mcp" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_lists_and_calls_tools():
    module = _load()
    server = module.NexusMCP()
    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "nexus-research-intelligence"
    listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listing["result"]["tools"]) >= 10
    call = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "graph_metrics", "arguments": {"node_id": "paper:2210.03629"}},
    })
    assert call["result"]["isError"] is False
    unknown = server.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "not_a_tool", "arguments": {}},
    })
    assert unknown["error"]["code"] == -32602
