"""Specodex MCP server — the public catalog API as MCP tools.

Thin, read-only wrappers over the same HTTPS endpoints the web UI calls
(https://www.specodex.com/api/...). No database credentials, no
LLM keys: anything that can reach the site can run this.

    uv run specodex-mcp            # stdio transport (Claude Code, Claude Desktop)
    SPECODEX_API_URL=https://d1acboh655kvrt.cloudfront.net uv run specodex-mcp

See specodex/mcp/server.py for the tool list.
"""

from specodex.mcp.api import SpecodexApi, SpecodexApiError
from specodex.mcp.server import build_server, main

__all__ = ["SpecodexApi", "SpecodexApiError", "build_server", "main"]
