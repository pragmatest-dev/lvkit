"""The MCP tools for ``.ctl`` controls: ``read_ctl`` and ``render``."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lvkit.mcp import server as srv

from .conftest import SAMPLES_ROOT

_CLUSTER_CTL = (
    SAMPLES_ROOT
    / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/API_Test Settings.ctl"
)
_ENUM_CTL = SAMPLES_ROOT / "DCAF-DAQModule/source/editor node/Permissions Enum.ctl"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def test_read_ctl_is_a_registered_tool() -> None:
    names = {t.name for t in _run(srv.mcp.list_tools())}
    assert {"read_vi", "read_ctl", "render"} <= names


def test_a_missing_control_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _run(srv.read_ctl(str(tmp_path / "Nope.ctl")))


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_read_ctl_returns_the_typedef_structure():
    result = _run(srv.read_ctl(str(_CLUSTER_CTL)))
    assert result["typedef"] == "API_Test Settings.ctl"
    assert result["kind"] == "cluster"
    assert {f["name"] for f in result["fields"]} >= {"Font", "Size", "Text color"}
    assert "root_type" not in result  # only with verbose
    assert "used_by" not in result  # a lone control has no users loaded
    verbose = _run(srv.read_ctl(str(_CLUSTER_CTL), verbose=True))
    assert verbose["root_type"]["kind"] == "cluster"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ENUM_CTL.exists(), reason="DCAF sample absent")
def test_read_ctl_resolves_a_workspace_relative_path(monkeypatch):
    monkeypatch.setattr(srv, "_DEFAULT_ROOTS", [str(_ENUM_CTL.parent)])
    result = _run(srv.read_ctl(_ENUM_CTL.name))
    assert result["kind"] == "enum"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_render_draws_a_control(tmp_path, monkeypatch):
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    result = _run(srv.render(str(_CLUSTER_CTL)))
    page = Path(result["render_path"]).read_text(encoding="utf-8")
    assert result["bytes"] == len(page)
    assert "<b>API_Test Settings.ctl</b>" in page and ">Text color<" in page
