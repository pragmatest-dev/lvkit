"""mcp SDK compatibility shim.

Built on the decorator-style server the mcp SDK ships: ``FastMCP`` in mcp 1.x
(``mcp.server.fastmcp``), renamed ``MCPServer`` in mcp 2.0
(``mcp.server.mcpserver``) with the SAME ``@tool``/``run``/``list_tools`` API.
We import whichever exists (see below) so lvkit runs on both majors. This
supersedes the module-global ``mcp.server.Server`` + ``@app.list_tools()`` build
this module used to have (that decorator was removed in mcp 2.0, silently
disabling the server — see ``docs/_internal/design/lvkit-mcp-improvements.md``).
"""

from __future__ import annotations

# Whichever the installed SDK ships: mcp 2.0's MCPServer or 1.x's FastMCP. Only
# one module exists at a time, so the OTHER branch is unresolvable to the type
# checker — suppress the missing-import there (both are annotated so it holds
# under whichever major is installed).
try:  # mcp >= 2.0 renamed FastMCP -> MCPServer (identical decorator API)
    from mcp.server.mcpserver import Context  # type: ignore
    from mcp.server.mcpserver import MCPServer as _MCPServer  # type: ignore
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context  # type: ignore
    from mcp.server.fastmcp import FastMCP as _MCPServer  # type: ignore

__all__ = ["Context", "_MCPServer"]
