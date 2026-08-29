"""Workspace-root defaulting — the stateless helpers.

MCP clients advertise the folder(s) the user opened (VS Code / Claude Code
workspace) as `roots`. We read them so a caller who has already opened their
VI repo never has to repeat its path: `project` defaults to the first root,
and a relative VI path resolves under it. Passing an explicit path still wins
(multi-repo sessions, headless agents), and a client that doesn't support
roots simply falls back to that explicit argument.

A Windows VS Code / Claude client speaks Windows paths (`C:\\repo`, and roots
arrive as `file:///C:/repo`). When the server itself runs INSIDE WSL — the
common "run the WSL checkout, drive it from a Windows editor" setup — those
paths must be re-expressed as the WSL mount (`/mnt/c/repo`) or nothing
resolves. Applied at every point a path enters the server (roots + explicit
args), so `project` defaulting and relative targets work across the boundary.

The stateful counterparts (``_DEFAULT_ROOTS``, ``_default_roots``,
``_resolve_project``, ``_resolve_target``) live in the package's ``__init__``
facade instead of here — they read/write the ``_DEFAULT_ROOTS`` module global
that ``main()`` rebinds via ``global``, and tests monkeypatch that global on
the ``lvkit.mcp.server`` module object directly, so it must stay co-located
with its reader/writer in that exact module (see the facade's module
docstring for the full explanation).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from ._compat import Context

_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_WSL_UNC = re.compile(r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+\\(.*)$")


def _win_to_wsl_path(p: str) -> str:
    """Map a Windows path to its WSL-visible form; a no-op on native Windows or
    for an already-POSIX path.

    - ``C:\\repo`` / ``C:/repo`` -> ``/mnt/c/repo`` (WSL's default automount;
      override the mount root in ``/etc/wsl.conf`` if you've changed it).
    - ``\\\\wsl.localhost\\Ubuntu\\home\\x`` (a WSL folder opened FROM Windows)
      -> ``/home/x``.
    """
    if os.name == "nt":
        return p  # native Windows process — its own paths are already correct
    m = _WIN_DRIVE.match(p)
    if m:
        return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}"
    m = _WSL_UNC.match(p)
    if m:
        return "/" + m.group(1).replace("\\", "/")
    return p


def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI (as sent in MCP roots) to a local path.

    Handles POSIX (``file:///home/x``), Windows (``file:///C:/Users/x``, which
    arrives as ``/C:/Users/x``), and — when the server runs under WSL — maps a
    Windows drive path onto its ``/mnt`` mount (see :func:`_win_to_wsl_path`).
    """
    path = unquote(urlparse(uri).path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # Windows drive path: '/C:/...' -> 'C:/...'
    return Path(_win_to_wsl_path(path))


async def _client_roots(ctx: Context | None) -> list[Path]:
    """Workspace folders the client advertised (empty if unsupported/declined).

    Never raises: a client without the roots capability just yields ``[]``, and
    the explicit path argument is the documented fallback.
    """
    if ctx is None:
        return []
    try:
        result = await ctx.session.list_roots()
    except Exception:
        return []
    return [_uri_to_path(str(r.uri)) for r in result.roots]


__all__ = [
    "_WIN_DRIVE",
    "_WSL_UNC",
    "_win_to_wsl_path",
    "_uri_to_path",
    "_client_roots",
]
