"""An unwired input of a catalog call takes the default the catalog documents
(``default``); one with none is left to the called function, or is a gap where an
inline template reads it."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.pipeline import create_import_resolver
from lvkit.vilib_resolver import VIEntry, VITerminal, get_resolver
from tests.helpers import build_graph

KEY = "/vilib/Analysis/Inverse FFT.vi"
RECORDED = ["<vilib>", "Analysis", "x.llb", "Inverse FFT.vi"]
_DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_ENUM = LVType(kind=LVTypeKind.ENUM)


def _caller_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: VIEntry,
    gaps: list[Gap],
) -> str:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(KEY, node_type="vi")  # noqa: SLF001
    graph._recorded.record(KEY, None, RECORDED)  # noqa: SLF001
    out = tmp_path / "pkg"
    out.mkdir()
    resolver = create_import_resolver(
        "pkg",
        out,
        {KEY: out / "ifft.py", "caller.vi": out / "caller.py"},
        out / "caller.py",
        caller_key="caller.vi",
        graph=graph,
    )
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name="Inverse FFT.vi",
        node_type="iUse",
        callee_key=KEY,
        terminals=[
            FPTerminal(id="k:x", index=1, direction="input", lv_type=_DBL),
            FPTerminal(id="k:s", index=2, direction="input", lv_type=_BOOL),
            FPTerminal(id="k:o", index=3, direction="output", lv_type=_DBL),
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
                lv_type=_DBL,
            )
        ],
        outputs=[
            FPTerminal(
                id="fp:y",
                index=0,
                direction="output",
                name="y",
                is_public=True,
                is_indicator=True,
                lv_type=_DBL,
            )
        ],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:x", to_terminal_id="k:x"),
            Wire.from_terminals(from_terminal_id="k:o", to_terminal_id="fp:y"),
        ],
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: None)
    monkeypatch.setattr(get_resolver(), "resolve_recorded", lambda path: entry)
    monkeypatch.setattr(
        get_resolver(), "catalog_file_name", lambda path: "Inverse FFT.vi"
    )
    return build_module(
        context, "caller.vi", import_resolver=resolver, graph=built, gaps=gaps
    )


def _entry(default: bool | None, *, inline: bool, template: str) -> VIEntry:
    return VIEntry(
        name="Inverse FFT",
        python_code=template,
        inline=inline,
        terminals=[
            VITerminal(name="FFT {X}", index=1, direction="in", python_param="fft_x"),
            VITerminal(
                name="shift?",
                index=2,
                direction="in",
                python_param="shift",
                default=default,
            ),
            VITerminal(name="x", index=3, direction="out", python_param="out"),
        ],
    )


def test_module_call_omits_an_unknown_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    source = _caller_source(
        tmp_path,
        monkeypatch,
        _entry(None, inline=False, template="ifft({fft_x}, {shift})"),
        gaps,
    )
    assert "shift=" not in source


def test_module_call_passes_the_documented_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _caller_source(
        tmp_path,
        monkeypatch,
        _entry(True, inline=False, template="ifft({fft_x}, {shift})"),
        [],
    )
    assert "shift=True" in source


def test_inline_unknown_default_is_a_gap_where_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    source = _caller_source(
        tmp_path,
        monkeypatch,
        _entry(None, inline=True, template="{out} = f({fft_x}, {shift})"),
        gaps,
    )
    assert len(gaps) == 1
    assert "NotImplementedError" in source


def test_inline_unknown_default_unread_is_no_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    _caller_source(
        tmp_path,
        monkeypatch,
        _entry(None, inline=True, template="{out} = f({fft_x})"),
        gaps,
    )
    assert gaps == []


def test_inline_documented_default_substitutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _caller_source(
        tmp_path,
        monkeypatch,
        _entry(True, inline=True, template="{out} = f({fft_x}, {shift})"),
        [],
    )
    assert "True" in source
