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
    _require_vis,
    _resolve_project,
    _resolve_target,
    _uri_to_path,
    _win_to_wsl_path,
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
    # A Windows client sends file:///C:/Users/x -> urlparse path is /C:/Users/x;
    # on a non-Windows (WSL) host that maps onto the /mnt mount.
    assert _uri_to_path("file:///C:/Users/ryanf/repo") == Path(
        "/mnt/c/Users/ryanf/repo"
    )


# ---- _win_to_wsl_path (Windows client -> WSL-hosted server) -----------------


def test_win_to_wsl_drive_forward_slash():
    assert _win_to_wsl_path("C:/Users/ryanf/repo") == "/mnt/c/Users/ryanf/repo"


def test_win_to_wsl_drive_backslash():
    assert _win_to_wsl_path("D:\\work\\my repo") == "/mnt/d/work/my repo"


def test_win_to_wsl_unc_wsl_localhost():
    # A WSL folder opened FROM Windows arrives as a \\wsl.localhost\ UNC path.
    assert (
        _win_to_wsl_path("\\\\wsl.localhost\\Ubuntu\\home\\ryanf\\repo")
        == "/home/ryanf/repo"
    )


def test_win_to_wsl_posix_is_noop():
    assert _win_to_wsl_path("/home/ryanf/repo") == "/home/ryanf/repo"


def test_win_to_wsl_native_windows_is_noop(monkeypatch: pytest.MonkeyPatch):
    # On a native Windows process the path is already correct — never remap.
    monkeypatch.setattr(mcp_server.os, "name", "nt")
    assert _win_to_wsl_path("C:/Users/ryanf/repo") == "C:/Users/ryanf/repo"


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


def test_resolve_project_autodetects_source_root_from_cwd_subdir(tmp_path, monkeypatch):
    """No CLI roots, no client ctx: the pure-cwd fallback walks UP to the enclosing
    source root (here a ``*.lvproj`` marker) rather than scoping to the subdir the
    client happened to launch in."""
    root = tmp_path / "proj"
    (root / "Classes" / "Foo.lvclass").mkdir(parents=True)
    (root / "proj.lvproj").write_text("<Project/>")
    monkeypatch.setattr(mcp_server, "_DEFAULT_ROOTS", [])
    monkeypatch.delenv("LVKIT_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(root / "Classes" / "Foo.lvclass")
    assert asyncio.run(_resolve_project(None, None)) == str(root)


def test_resolve_project_single_configured_root(tmp_path, monkeypatch):
    """One configured default root (no client ctx) is used automatically."""
    root = tmp_path / "only"
    monkeypatch.setattr(mcp_server, "_DEFAULT_ROOTS", [str(root)])
    assert asyncio.run(_resolve_project(None, None)) == str(root)


def test_resolve_project_multiple_configured_roots_disambiguate(tmp_path, monkeypatch):
    """Several configured default roots + no explicit project -> raise (approach a),
    never a silent pick of one."""
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setattr(mcp_server, "_DEFAULT_ROOTS", [str(a), str(b)])
    with pytest.raises(ValueError, match="pass project="):
        asyncio.run(_resolve_project(None, None))


def test_list_projects_reports_configured_roots(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setattr(mcp_server, "_DEFAULT_ROOTS", [str(a), str(b)])
    out = asyncio.run(mcp_server.list_projects(None))
    assert out["configured_roots"] == [str(a), str(b)]
    assert out["client_roots"] == []


# ---- _resolve_target --------------------------------------------------------


def test_resolve_target_absolute_passthrough(tmp_path):
    ctx = _ctx_for(tmp_path)
    abs_p = str(tmp_path / "x.vi")
    assert asyncio.run(_resolve_target(abs_p, ctx)) == abs_p


def test_resolve_target_windows_abs_maps_to_wsl(tmp_path):
    # A Windows client can pass an explicit C:\ path; on a WSL host it must map
    # to /mnt/... and be treated as absolute (C:\ is NOT is_absolute() on Linux).
    ctx = _ctx_for(tmp_path)
    assert (
        asyncio.run(_resolve_target("C:\\repo\\Sub\\run.vi", ctx))
        == "/mnt/c/repo/Sub/run.vi"
    )


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
    """`query` with no `project` indexes the folder the client opened."""
    if not _TESTCASE_DIR.exists():
        pytest.skip("sample corpus absent")
    ctx = _ctx_for(_TESTCASE_DIR)
    res = asyncio.run(mcp_server.query("SELECT path FROM vi", project=None, ctx=ctx))
    assert res["rows"], "expected VIs to be indexed from the client-root default"
    root = str(_TESTCASE_DIR.resolve())
    assert all(row[0].startswith(root) for row in res["rows"])


# ---- tool surface: exactly the understanding tools, describe dropped ---------


def test_mcp_registers_the_expected_tool_set():
    """The SDK must expose EXACTLY the understanding-surface tools — and NOT
    `describe` (dropped; CLI-only). Guards the silent-disable regression, where a
    decorator change in mcp 2.0 once left the server exposing no tools at all."""
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {
        "list_projects",
        "index",
        "query",
        "query_schema",
        "read_vi",
        "render",
        "diff",
        "unresolved",
    }
    assert "describe" not in names


def test_require_vis_points_at_project_when_root_has_no_vis(tmp_path):
    """The 'resolved root holds no .vi' case (notably Claude Desktop, whose cwd
    isn't your files) must raise a caller-actionable message naming `project=` —
    the difference between a stuck user and a fixed one."""
    with pytest.raises(ValueError, match="project="):
        _require_vis(tmp_path, [])
    # A non-empty list passes silently (no raise).
    _require_vis(tmp_path, [tmp_path / "x.vi"])
