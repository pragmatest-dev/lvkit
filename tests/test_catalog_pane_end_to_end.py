"""A loaded catalog VI completes the catalog entry used for the generated call, and
a pane that disagrees with the entry is a generation warning and a raise."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.pipeline import create_import_resolver
from tests.helpers import build_graph

KEY = "/vilib/Utility/sysdir.llb/Get System Directory.vi"
RECORDED = ["<vilib>", "Utility", "sysdir.llb", "Get System Directory.vi"]
_NUMBER = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")


def _pane(create_type: LVType) -> list[Terminal]:
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
            default_value=False,
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pane: list[Terminal]
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
        ],
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
            Wire.from_terminals(from_terminal_id="fp:z", to_terminal_id="k:create"),
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
