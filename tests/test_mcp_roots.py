"""Workspace-root defaulting for the MCP server.

A client that has opened a VI repo advertises it as an MCP *root*; the server
reads that so `project` (and a relative VI path) never has to be repeated on
every call. These tests pin the resolution rules without a full MCP handshake,
driving the helpers with a fake context whose `session.list_roots()` returns a
canned `ListRootsResult` (or raises, standing in for a client with no roots
capability).
"""
import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.types import ListRootsResult, Root

from lvkit.mcp import server as mcp_server
from lvkit.mcp.server import (
    _resolve_project,
    _resolve_target,
    _uri_to_path,
)

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_TESTCASE_DIR = _SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"


class _FakeSession:
    def __init__(self, roots: list[Root] | None, *, unsupported: bool = False):
        self._roots = roots or []
        self._unsupported = unsupported

    async def list_roots(self) -> ListRootsResult:
        if self._unsupported:
            raise RuntimeError("client does not support roots")
        return ListRootsResult(roots=self._roots)


class _FakeCtx:
    def __init__(self, roots: list[Root] | None, *, unsupported: bool = False):
        self.session = _FakeSession(roots, unsupported=unsupported)


def _ctx_for(*paths: Path, unsupported: bool = False) -> Any:
    """A stand-in for the MCP ``Context`` (duck-typed; returned as ``Any`` so it
    satisfies the helpers' ``Context | None`` parameter)."""
    roots = [Root.model_validate({"uri": p.as_uri(), "name": p.name}) for p in paths]
    return cast(Any, _FakeCtx(roots, unsupported=unsupported))


# ---- _uri_to_path -----------------------------------------------------------

def test_uri_to_path_posix():
    assert _uri_to_path("file:///home/ryanf/repo") == Path("/home/ryanf/repo")


def test_uri_to_path_windows_drive():
    # A Windows client sends file:///C:/Users/x -> urlparse path is /C:/Users/x.
    assert _uri_to_path("file:///C:/Users/ryanf/repo") == Path("C:/Users/ryanf/repo")


def test_uri_to_path_percent_encoded():
    assert _uri_to_path("file:///home/a%20b/repo") == Path("/home/a b/repo")


# ---- _resolve_project -------------------------------------------------------

def test_resolve_project_explicit_wins(tmp_path):
    other = tmp_path / "other"
    ctx = _ctx_for(tmp_path / "root")
    assert asyncio.run(_resolve_project(str(other), ctx)) == str(other)


def test_resolve_project_defaults_to_first_root(tmp_path):
    root = tmp_path / "root"
    ctx = _ctx_for(root, tmp_path / "second")
    assert asyncio.run(_resolve_project(None, ctx)) == str(root)


def test_resolve_project_falls_back_to_cwd_without_ctx():
    assert asyncio.run(_resolve_project(None, None)) == str(Path.cwd())


def test_resolve_project_falls_back_to_cwd_when_roots_unsupported(tmp_path):
    ctx = _ctx_for(tmp_path, unsupported=True)
    assert asyncio.run(_resolve_project(None, ctx)) == str(Path.cwd())


# ---- _resolve_target --------------------------------------------------------

def test_resolve_target_absolute_passthrough(tmp_path):
    ctx = _ctx_for(tmp_path)
    abs_p = str(tmp_path / "x.vi")
    assert asyncio.run(_resolve_target(abs_p, ctx)) == abs_p


def test_resolve_target_relative_resolves_under_root(tmp_path):
    vi = tmp_path / "Classes" / "run.vi"
    vi.parent.mkdir(parents=True)
    vi.write_bytes(b"stub")
    ctx = _ctx_for(tmp_path)
    assert asyncio.run(_resolve_target("Classes/run.vi", ctx)) == str(vi)


def test_resolve_target_unmatched_relative_unchanged(tmp_path):
    # Nothing under root matches -> return as-given so the tool raises its own
    # FileNotFoundError rather than a silently wrong path.
    ctx = _ctx_for(tmp_path)
    assert asyncio.run(_resolve_target("nope/missing.vi", ctx)) == "nope/missing.vi"


# ---- end-to-end: a real tool honors the client root when project is omitted --

@pytest.mark.needs_samples
def test_project_tool_defaults_to_client_root():
    """`find_symbols` with no `project` indexes the folder the client opened."""
    if not _TESTCASE_DIR.exists():
        pytest.skip("sample corpus absent")
    ctx = _ctx_for(_TESTCASE_DIR)
    syms = asyncio.run(mcp_server.find_symbols(project=None, ctx=ctx))
    assert syms, "expected VIs to be indexed from the client-root default"
    root = str(_TESTCASE_DIR.resolve())
    assert all(s["path"].startswith(root) for s in syms)
