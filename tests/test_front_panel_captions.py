"""Front-panel captions (issue #148): carried on the terminal and shown in
describe, JSON, lvnet and diff. The label stays the identity everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.describe import _shown_caption, describe_vi
from lvkit.graph.diff import diff_to_dict
from lvkit.graph.lvnet_parse import ParsedLvnet, netlist_signature, parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.graph.netlist_models import (
    BoundaryOutput,
    ConnectorPane,
    ConnectorPaneTerminal,
    NetlistBoundaryInput,
    NetlistModule,
    WiringRequirement,
)
from lvkit.load_mode import LoadMode
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.test_lvnet_identity import _assert_graph_identity

_SAMPLES = Path(".lvkit/cache/samples")
_JKI_SOURCE_ROOT = _SAMPLES / "JKI-VI-Tester/source"
_MAIN_UI = (
    _JKI_SOURCE_ROOT
    / "User Interfaces"
    / "Graphical Test Runner"
    / "Graphical Test Runner - Main UI - .vi"
)

# Captions that need the string-literal escaping to stay on one physical line,
# including the multi-line ones in the sample corpus.
_AWKWARD_CAPTIONS = [
    "Last Run:",
    'say "hi"',
    "back\\slash",
    "line one\nline two",
    "crlf\r\nsplit",
    'ends with a quote"',
    'caption "inside" a caption',
    'x default "y" @3',
    "wild * ? < > | chars",
    "",
]


def _module(caption: str | None, *, visible: bool) -> NetlistModule:
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    return NetlistModule(
        vi_name="Synthetic.vi",
        inputs=[
            NetlistBoundaryInput(
                name="txt",
                type_descriptor="String",
                lv_type=string,
                caption=caption,
                caption_visible=visible,
            )
        ],
        outputs=[
            BoundaryOutput(
                name="out",
                type_descriptor="String",
                source=None,
                lv_type=string,
                caption=caption,
                caption_visible=visible,
            )
        ],
        connector_pane=ConnectorPane(
            pattern_id=4815,
            terminals=[
                ConnectorPaneTerminal(
                    name=name,
                    type="String",
                    direction=direction,
                    index=index,
                    wiring_requirement=WiringRequirement.UNKNOWN,
                    default="fallback" if name == "txt" else None,
                    lv_type=string,
                    caption=caption,
                    caption_visible=visible,
                )
                for name, direction, index in (
                    ("txt", "input", 3),
                    ("out", "output", 4),
                )
            ],
        ),
    )


@pytest.mark.parametrize("visible", [True, False])
@pytest.mark.parametrize("caption", _AWKWARD_CAPTIONS)
def test_caption_round_trips_by_bytes_and_identity(caption: str, visible: bool) -> None:
    module = _module(caption, visible=visible)
    text = render_lvnet(module, display_name="Synthetic.vi", verbose=True)
    row = next(line for line in text.splitlines() if " txt " in line and "@3" in line)
    assert "\n" not in row and "\r" not in row
    parsed = parse_lvnet(text)
    assert isinstance(parsed, ParsedLvnet)
    rebuilt = reconstruct_module(parsed)
    assert render_lvnet(rebuilt, display_name="Synthetic.vi", verbose=True) == text
    _assert_graph_identity(module, rebuilt, "Synthetic.vi")
    assert netlist_signature(module) == netlist_signature(parsed)
    boundary = parsed.boundary[0]
    assert boundary.caption == caption
    assert boundary.caption_visible is visible
    assert boundary.default == '"fallback"'


def test_lvnet_clause_order_and_hidden_marker() -> None:
    text = render_lvnet(
        _module("Last Run:", visible=True), display_name="S.vi", verbose=True
    )
    assert '"fallback" caption "Last Run:" @3' in text
    hidden = render_lvnet(
        _module("Last Run:", visible=False), display_name="S.vi", verbose=True
    )
    assert '"fallback" caption hidden "Last Run:" @3' in hidden


def test_unset_caption_emits_nothing_and_terse_hides_captions() -> None:
    assert "caption" not in render_lvnet(
        _module(None, visible=False), display_name="S.vi", verbose=True
    )
    assert "caption" not in render_lvnet(
        _module("Last Run:", visible=True), display_name="S.vi"
    )


def test_json_carries_caption_and_visibility_on_every_entry() -> None:
    payload = netlist_to_dict(_module("Last Run:", visible=True))
    for entry in (
        *payload["inputs"],
        *payload["outputs"],
        *payload["connector_pane"]["terminals"],
    ):
        assert entry["caption"] == "Last Run:"
        assert entry["caption_visible"] is True
    unset = netlist_to_dict(_module(None, visible=False))
    for entry in (*unset["inputs"], *unset["connector_pane"]["terminals"]):
        assert entry["caption"] is None
        assert entry["caption_visible"] is False


def _load_main_ui():
    if not _MAIN_UI.exists():
        pytest.skip(f"sample corpus VI not present: {_MAIN_UI}")
    return load_vi_by_path(
        _MAIN_UI, LoadMode.MINIMAL, layout=False
    )


@pytest.mark.needs_samples
def test_main_ui_shows_the_caption_in_describe_json_and_lvnet() -> None:
    graph, key = _load_main_ui()
    text = describe_vi(graph, key)
    assert 'txtRuns:: String (caption "Last Run:")' in text

    module = build_netlist_from_graph(graph, key)
    payload = json.loads(json.dumps(netlist_to_dict(module)))
    runs = next(
        t for t in payload["connector_pane"]["terminals"] if t["name"] == "txtRuns:"
    )
    assert runs["caption"] == "Last Run:"
    assert runs["caption_visible"] is True

    verbose = render_lvnet(module, display_name=_MAIN_UI.name, verbose=True)
    assert 'txtRuns:' in verbose
    assert 'caption "Last Run:"' in verbose
    assert "caption" not in render_lvnet(module, display_name=_MAIN_UI.name)

    rebuilt = reconstruct_module(parse_lvnet(verbose))
    assert render_lvnet(rebuilt, display_name=_MAIN_UI.name, verbose=True) == verbose
    _assert_graph_identity(module, rebuilt, _MAIN_UI.name)


def test_describe_shows_only_a_visible_caption_that_differs_from_the_label() -> None:
    def terminal(caption: str | None, visible: bool) -> FPTerminal:
        return FPTerminal(
            id="t",
            index=None,
            direction="output",
            name="txtRuns:",
            caption=caption,
            caption_visible=visible,
            is_public=False,
        )

    assert _shown_caption(terminal("Last Run:", True)) == "Last Run:"
    assert _shown_caption(terminal("Last Run:", False)) is None
    assert _shown_caption(terminal("txtRuns:", True)) is None
    assert _shown_caption(terminal(None, False)) is None


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    "vi",
    [
        pytest.param(
            _SAMPLES / "measurement-plugin-labview/Source/Generator"
            / "Create Measurement Client.vi",
            id="Create_Measurement_Client",
        ),
        pytest.param(
            _SAMPLES
            / "LabVIEW-DAQ/Intrinsic Optical Signal/PCO Edge 5.5/PCO Examples"
            / "deprecated/pco.labview_Example.vi",
            id="pco_labview_Example",
        ),
    ],
)
def test_multi_line_corpus_captions_round_trip(vi: Path) -> None:
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, key = load_vi_by_path(
        vi, LoadMode.MINIMAL, layout=False
    )
    module = build_netlist_from_graph(graph, key)
    captions = [
        t.caption
        for t in module.connector_pane.terminals
        if t.caption is not None and "\n" in t.caption
    ]
    assert captions, "the VI has a multi-line caption"
    text = render_lvnet(module, display_name=vi.name, verbose=True)
    rebuilt = reconstruct_module(parse_lvnet(text))
    assert render_lvnet(rebuilt, display_name=vi.name, verbose=True) == text
    _assert_graph_identity(module, rebuilt, vi.name)


@pytest.mark.needs_samples
def test_caption_change_is_a_modified_terminal() -> None:
    graph_a, key_a = _load_main_ui()
    graph_b, key_b = _load_main_ui()
    runs = next(
        t
        for t in graph_b.get_outputs(key_b, public_only=False)
        if isinstance(t, FPTerminal) and t.name == "txtRuns:"
    )
    assert runs.caption == "Last Run:"
    runs.caption = "Runs:"
    changes = diff_to_dict(graph_a, graph_b, key_a, key_b)["changes"]
    terminal_changes = [c for c in changes if c["kind"] == "terminal"]
    assert len(terminal_changes) == 1
    change = terminal_changes[0]
    assert change["change"] == "modified"
    assert change["detail"] == 'caption "Last Run:" → "Runs:"'
