# NEXUS MCP server

Exposes the NEXUS research tools to any MCP client (Claude Desktop, Cursor, an
agent framework) over the stdio transport:

```bash
python mcp/server.py --list          # tool catalogue + engine status
python mcp/server.py                 # speak MCP on stdin/stdout
```

Claude Desktop configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "command": "python",
      "args": ["/absolute/path/to/AI-Research-Intelligence-Graph/mcp/server.py"],
      "cwd": "/absolute/path/to/AI-Research-Intelligence-Graph"
    }
  }
}
```

Security and honesty notes:

* No credentials live in this server: it reads the same `.env` as the API, and the
  tools are read-only graph queries.
* The server instructions tell the client that `find_research_gaps` returns
  prototype-heuristic **hypotheses** and that predicted links are predictions —
  so an LLM consuming these tools is told to phrase them as such.
