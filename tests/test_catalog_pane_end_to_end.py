"""A loaded catalog VI completes the catalog entry used for the generated call, and
a pane that disagrees with the entry is a generation warning and a raise."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.pipeline import create_import_resolver
from lvkit.vilib_resolver import get_resolver
from tests.helpers import build_graph

KEY = "/vilib/Utility/sysdir.llb/Get System Directory.vi"
RECORDED = ["<vilib>", "Utility", "sysdir.llb", "Get System Directory.vi"]
_NUMBER = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")


def _pane(create_type: LVType, create_default: bool = False) -> list[Terminal]:
    return [
        Terminal(
            id="p:type",
            index=11,
            direction="input",
            name="system directory type",
            lv_type=_NUMBER,
        ),
        Terminal(
            id="p:create",
            index=5,
            direction="input",
            name="create directory if not found? (F)",
            lv_type=create_type,
            default_value=create_default,
        ),
        Terminal(
            id="p:out",
            index=2,
            direction="output",
            name="system directory",
            lv_type=_PATH,
        ),
    ]


def _generate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pane: list[Terminal],
    *,
    wire_create: bool = True,
) -> tuple[str, list[Gap]]:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(KEY, node_type="vi")  # noqa: SLF001
    graph._recorded.record(KEY, None, RECORDED)  # noqa: SLF001
    out = tmp_path / "pkg"
    out.mkdir()
    resolver = create_import_resolver(
        "pkg",
        out,
        {KEY: out / "sysdir.py", "caller.vi": out / "caller.py"},
        out / "caller.py",
        caller_key="caller.vi",
        graph=graph,
    )
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name="Get System Directory.vi",
        node_type="iUse",
        callee_key=KEY,
        terminals=[
            FPTerminal(id="k:type", index=11, direction="input", lv_type=_NUMBER),
            FPTerminal(id="k:create", index=5, direction="input", lv_type=_BOOL),
            FPTerminal(id="k:out", index=2, direction="output", lv_type=_PATH),
        ],
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id="fp:x",
                index=0,
                direction="input",
                name="x",
                is_public=True,
                is_indicator=False,
                lv_type=_NUMBER,
            ),
            FPTerminal(
                id="fp:z",
                index=1,
                direction="input",
                name="z",
                is_public=True,
                is_indicator=False,
                lv_type=_BOOL,
            ),
        ][: 2 if wire_create else 1],
        outputs=[
            FPTerminal(
                id="fp:y",
                index=0,
                direction="output",
                name="y",
                is_public=True,
                is_indicator=True,
                lv_type=_PATH,
            )
        ],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:type"),
            *(
                [
                    Wire.from_terminals(
                        from_terminal_id="fp:z", to_terminal_id="k:create"
                    )
                ]
                if wire_create
                else []
            ),
            Wire.from_terminals(from_terminal_id="k:out", to_terminal_id="fp:y"),
        ],
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: KEY)
    monkeypatch.setattr(built, "vi_interface", lambda key: (pane[:2], [], pane[2:], []))
    gaps: list[Gap] = []
    source = build_module(
        context, "caller.vi", import_resolver=resolver, graph=built, gaps=gaps
    )
    return source, gaps


def test_a_loaded_catalog_vi_fills_the_missing_index_into_the_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, gaps = _generate(tmp_path, monkeypatch, _pane(_BOOL))
    assert gaps == []
    assert "directory_type=x" in source
    # The catalog gives "create directory if not found? (F)" no index: the pane's
    # control with that label gives it index 5, so the wire on 5 is its argument.
    assert "create_if_not_found=z" in source
    assert "NotImplementedError" not in source


def test_a_pane_that_disagrees_with_the_entry_is_a_gap_and_a_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = _pane(_BOOL)
    pane[0] = Terminal(
        id="p:type",
        index=3,
        direction="input",
        name="system directory type",
        lv_type=_NUMBER,
    )
    source, gaps = _generate(tmp_path, monkeypatch, pane)
    assert [g.kind for g in gaps] == ["terminal_mapping"]
    assert "raise NotImplementedError(" in source


def test_an_unwired_input_gets_the_loaded_controls_saved_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The catalog gives "create directory if not found? (F)" no default (its
    # Python defaults to False); the loaded VI's control saves True. The caller
    # leaves it unwired, so the call runs with the control's saved default and the
    # missing directory is created.
    source, gaps = _generate(
        tmp_path, monkeypatch, _pane(_BOOL, create_default=True), wire_create=False
    )
    assert gaps == []
    package = tmp_path / "pkg"
    (package / "__init__.py").write_text("", encoding="utf-8")
    implementation = get_resolver().get_implementation("/".join(RECORDED))
    assert implementation is not None
    (package / "sysdir.py").write_text(implementation, encoding="utf-8")
    (package / "caller.py").write_text(source, encoding="utf-8")
    missing = tmp_path / "cache"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(missing))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        caller = importlib.import_module("pkg.caller")
        # System directory type 13 is the public cache folder: XDG_CACHE_HOME here.
        result = caller.caller(x=13)
    finally:
        for name in ("pkg.caller", "pkg.sysdir", "pkg"):
            sys.modules.pop(name, None)
    assert result.y == missing
    assert missing.is_dir()
    assert "create_if_not_found=True" in source
