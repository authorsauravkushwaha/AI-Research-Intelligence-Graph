#!/usr/bin/env python3
"""NEXUS as an MCP server (Model Context Protocol, stdio transport).

    python mcp/server.py                 # speak MCP on stdin/stdout
    python mcp/server.py --list          # print the tool catalogue and exit

The tools are the *same* registry objects the HTTP API, the agent and the gap
engine use (`backend.agents.tools.build_registry`), so there is exactly one
implementation of every research operation: no divergent behaviour between the UI,
the REST API and external MCP clients.

Protocol notes
--------------
* Transport: newline-delimited JSON-RPC 2.0 over stdio (the MCP stdio transport).
* Methods: `initialize`, `tools/list`, `tools/call`, plus `ping` and `shutdown`.
* Also speaks the JSON-RPC "Content-Length" framing if a client uses it, because
  some MCP clients still send LSP-style headers.
* Every result carries the engine that produced it, and engineering failures are
  returned as `isError: true` tool results instead of crashing the server.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "nexus-research-intelligence"
SERVER_VERSION = "1.0.0"


class NexusMCP:
    def __init__(self) -> None:
        from backend.agents.tools import build_registry
        from backend.engine.gaps import GapEngine
        from backend.graph.factory import get_state
        from backend.rag.graphrag import GraphRAG

        self.state = get_state()
        self.store = self.state.store
        self.gaps = GapEngine(self.store)
        self.rag = GraphRAG(self.store)
        self.registry = build_registry(self.store, self.gaps, self.rag)

    # ------------------------------------------------------------- protocol
    def catalogue(self) -> list[dict[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.parameters}
            for tool in self.registry.tools.values()
        ]

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        outcome = self.registry.execute(name, arguments or {})
        if not outcome.get("ok"):
            return {
                "content": [{"type": "text", "text": f"tool error: {outcome.get('error')}"}],
                "isError": True,
            }
        payload = outcome["result"]
        text = json.dumps(
            {
                "engine": self.store.engine_name,
                "graph": {"nodes": len(self.store.nodes), "edges": len(self.store.edges)},
                "result": payload,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        )
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return self.ok(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
                "instructions": (
                    "NEXUS research-intelligence tools over an AI-research knowledge graph. "
                    "find_research_gaps returns prototype-heuristic candidate opportunities: always "
                    "present them as hypotheses with their evidence, never as established findings. "
                    "Predicted links are hypotheses too."
                ),
            })
        if method in {"notifications/initialized", "initialized"}:
            return None
        if method == "ping":
            return self.ok(msg_id, {})
        if method == "shutdown":
            return self.ok(msg_id, None)
        if method == "tools/list":
            return self.ok(msg_id, {"tools": self.catalogue()})
        if method == "tools/call":
            name = params.get("name", "")
            if name not in self.registry.tools:
                return self.error(msg_id, -32602, f"unknown tool '{name}'",
                                  {"available": list(self.registry.tools)})
            return self.ok(msg_id, self.call(name, params.get("arguments")))
        if method == "resources/list":
            return self.ok(msg_id, {"resources": []})
        if method == "prompts/list":
            return self.ok(msg_id, {"prompts": []})
        return self.error(msg_id, -32601, f"method not found: {method}")

    @staticmethod
    def ok(msg_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
        if data is not None:
            payload["error"]["data"] = data
        return payload


def _read_message(stream) -> dict[str, Any] | None:
    """Read one JSON-RPC message in either framing style."""
    line = stream.readline()
    if not line:
        return None
    stripped = line.strip()
    if not stripped:
        return _read_message(stream)
    if stripped.lower().startswith("content-length:"):
        length = int(stripped.split(":", 1)[1])
        while True:  # skip headers
            header = stream.readline()
            if not header or header.strip() == "":
                break
        return json.loads(stream.read(length))
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        sys.stderr.write(f"nexus-mcp: ignoring unparseable line: {stripped[:120]}\n")
        return _read_message(stream)


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS MCP server")
    parser.add_argument("--list", action="store_true", help="print the tool catalogue and exit")
    args = parser.parse_args()

    server = NexusMCP()
    if args.list:
        for tool in server.catalogue():
            print(f"{tool['name']}: {tool['description']}")
        print(f"\nengine: {server.store.engine_name} · "
              f"{len(server.store.nodes)} nodes / {len(server.store.edges)} edges")
        return 0

    sys.stderr.write(f"nexus-mcp {SERVER_VERSION} ready ({server.store.engine_name}) — "
                     f"{len(server.catalogue())} tools\n")
    while True:
        message = _read_message(sys.stdin)
        if message is None:
            break
        response = server.handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
