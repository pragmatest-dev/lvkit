"""Call Library Function Node drawing: one row per terminal pair, placed from the
terminals' own geometry, with no text inside the node."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.load_mode import LoadMode
from lvkit.render import render_vi

# Node bounds (top, left, bottom, right) = (10, 20, 53, 60): 40 wide. Each row's
# input terminal records its termBounds (top, left, bottom, right); the output
# terminal's rect is the same one mirrored across the node's width.
_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="3">
      <SL__arrayElement class="prim" uid="70">
        <primResID>1711</primResID>
        <bounds>(30, -60, 40, -50)</bounds>
        <termList elements="1">
          <SL__arrayElement class="term" uid="71">
            <objFlags>1</objFlags>
            <dco class="parm" uid="72">
              <objFlags>1</objFlags>
              <termBounds>(0, 0, 10, 10)</termBounds>
            </dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="prim" uid="80">
        <primResID>1710</primResID>
        <bounds>(30, 120, 40, 130)</bounds>
        <termList elements="1">
          <SL__arrayElement class="term" uid="81">
            <dco class="parm" uid="82">
              <parmIndex>2</parmIndex>
              <termBounds>(0, 0, 10, 10)</termBounds>
            </dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="extFunc" uid="20">
        <bounds>(10, 20, 53, 60)</bounds>
        <termList elements="11">
          <SL__arrayElement class="term" uid="21">
            <dco class="hGrowCItem" uid="22">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(22)</typeDesc>
              <termBounds>(0, 0, 9, 13)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="23">
            <dco class="hGrowCItem" uid="24">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(23)</typeDesc>
              <termBounds>(0, 27, 9, 40)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="25">
            <dco class="hGrowCItem" uid="26">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(24)</typeDesc>
              <termBounds>(9, 0, 18, 13)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="27">
            <dco class="hGrowCItem" uid="28">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(25)</typeDesc>
              <termBounds>(9, 27, 18, 40)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="30">
            <dco class="extFuncArg" uid="31">
              <objFlags>65536</objFlags>
              <termList elements="2">
                <SL__arrayElement uid="30" />
                <SL__arrayElement uid="32" />
              </termList>
              <typeDesc>TypeID(27)</typeDesc>
              <termBounds>(18, 0, 26, 20)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="32">
            <dco uid="31" />
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="33">
            <dco class="extFuncArg" uid="34">
              <termList elements="2">
                <SL__arrayElement uid="33" />
                <SL__arrayElement uid="35" />
              </termList>
              <typeDesc>TypeID(21)</typeDesc>
              <termBounds>(26, 0, 34, 20)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="35">
            <dco uid="34" />
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="36">
            <dco class="extFuncArg" uid="37">
              <termList elements="2">
                <SL__arrayElement uid="36" />
                <SL__arrayElement uid="38" />
              </termList>
              <typeDesc>TypeID(26)</typeDesc>
              <termBounds>(34, 0, 42, 20)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="38">
            <dco uid="37" />
          </SL__arrayElement>
        </termList>
        <nodeName>"DoWork"</nodeName>
        <libPath>505448300000000C00010001076C69622E646C6C</libPath>
        <funcTD>1D</funcTD>
      </SL__arrayElement>
    </nodeList>
    <signalList elements="2">
      <SL__arrayElement class="signal" uid="90">
        <termList elements="2">
          <SL__arrayElement uid="71" />
          <SL__arrayElement uid="33" />
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="signal" uid="91">
        <termList elements="2">
          <SL__arrayElement uid="35" />
          <SL__arrayElement uid="81" />
        </termList>
      </SL__arrayElement>
    </signalList>
  </root>
</SL__rootObject>
"""


# A node like NI's configured example: a void return, a String parameter `name`
# (a C string) and an I32 parameter `count`.
_MAIN_XML = """<?xml version='1.0' encoding='utf-8'?>
<LVSR>
  <TM80>
    <!-- Heap TypeID 22 = Consolidated TypeID 2: Path -->
    <!-- Heap TypeID 23 = Consolidated TypeID 3: Path -->
    <!-- Heap TypeID 24 = Consolidated TypeID 4: NumInt32 -->
    <!-- Heap TypeID 25 = Consolidated TypeID 5: NumInt32 -->
    <!-- TypeID 20: String -->
    <!-- TypeID 21: String -->
    <!-- TypeID 26: NumInt32 -->
    <!-- TypeID 27: Void -->
    <!-- Heap TypeID 29 = Consolidated TypeID 1: Cluster -->
  </TM80>
  <VCTP>
    <Section Index="0" Format="inline">
      <TypeDesc Type="Void" Format="inline" />
      <TypeDesc Type="CString" Format="inline" Label="name" />
      <TypeDesc Type="NumInt32" Format="inline" Label="count" />
      <TypeDesc Type="Cluster" Format="inline">
        <TypeDesc TypeID="0" />
        <TypeDesc TypeID="1" />
        <TypeDesc TypeID="2" />
      </TypeDesc>
      <TypeDesc Type="Path" Format="inline" Label="path in" />
      <TypeDesc Type="Path" Format="inline" Label="path out" />
      <TypeDesc Type="NumInt32" Format="inline" Label="error in (no error)" />
      <TypeDesc Type="NumInt32" Format="inline" Label="error out" />
      <TopLevel>
        <TypeDesc Index="1" FlatTypeID="3" />
        <TypeDesc Index="2" FlatTypeID="4" />
        <TypeDesc Index="3" FlatTypeID="5" />
        <TypeDesc Index="4" FlatTypeID="6" />
        <TypeDesc Index="5" FlatTypeID="7" />
      </TopLevel>
    </Section>
  </VCTP>
</LVSR>
"""


def _svg(tmp_path: Path, thread_info: str = "", return_type: str = "27") -> str:
    (tmp_path / "Clfn_BDHb.xml").write_text(
        _BD_XML.replace("<funcTD>", f"{thread_info}<funcTD>").replace(
            "TypeID(27)", f"TypeID({return_type})"
        )
    )
    (tmp_path / "Clfn.xml").write_text(
        _MAIN_XML.replace("<!-- TypeID 27: Void -->", "<!-- TypeID 28: NumInt32 -->")
        if return_type == "28"
        else _MAIN_XML
    )
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Clfn_BDHb.xml", layout=True)
    svg = render_vi(graph, graph.list_vis()[0], interactive=False)
    assert svg is not None
    return svg


def _node_markup(svg: str, node_uid: str = "20") -> str:
    start = svg.index(f'<g class="lv-node" data-node="{node_uid}">')
    return svg[start : svg.index("</g>", start)]


def _shapes(markup: str) -> list[str]:
    return [
        line
        for line in markup.splitlines()
        if line.startswith(("<rect", "<line", "<text"))
    ]


def _rect(x: float, y: float, w: float, h: float, fill: str) -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"'
        f' fill="{fill}" stroke="none"/>'
    )


def _mnemonic(x: float, y: float, colour: str, text: str) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="6.5" text-anchor="middle"'
        f' fill="{colour}" font-weight="bold">{text}</text>'
    )


def test_node_is_drawn_in_labviews_pixels(tmp_path: Path) -> None:
    """The node's top-left is (20, 10), 40 wide. Over a black ground (the border,
    row lines and the 2-unit divider): the orange band 1..18 with the mark, then
    rows of 8 units whose 7-unit cells are white with the type's mnemonic, or
    grey where the row has no terminal. The return value is void: both cells
    grey. The bottom line is the unit below the last row."""
    shapes = _shapes(_node_markup(_svg(tmp_path)))
    black, white, grey = "#000000", "#ffffff", "#cecece"
    assert shapes == [
        _rect(20, 10, 40, 43, black),
        _rect(21, 11, 38, 17, "#ffad00"),
        # the mark: three bars on a base line, inside x 4..36 and y 3..16
        _rect(28, 19, 6, 6, black),
        _rect(37, 14, 6, 11, black),
        _rect(46, 16, 6, 9, black),
        _rect(24, 25, 32, 1, black),
        # the void return value row: no terminals
        _rect(21, 29, 18, 7, grey),
        _rect(41, 29, 18, 7, grey),
        # `name`: a C string, shown as abc on both sides
        _rect(21, 37, 18, 7, white),
        _mnemonic(30, 42.8, "#e05fa0", "abc"),
        _rect(41, 37, 18, 7, white),
        _mnemonic(50, 42.8, "#e05fa0", "abc"),
        # `count`: an I32 on both sides
        _rect(21, 45, 18, 7, white),
        _mnemonic(30, 50.8, "#1f3fbf", "I32"),
        _rect(41, 45, 18, 7, white),
        _mnemonic(50, 50.8, "#1f3fbf", "I32"),
    ]


def test_the_only_text_is_each_cells_type_mnemonic(tmp_path: Path) -> None:
    node = _node_markup(_svg(tmp_path))
    assert re.findall(r">([^<]+)</text>", node) == ["abc", "abc", "I32", "I32"]


def test_the_thread_setting_colours_the_band(tmp_path: Path) -> None:
    ui_thread = _node_markup(_svg(tmp_path))
    (tmp_path / "any").mkdir()
    any_thread = _node_markup(_svg(tmp_path / "any", "<threadInfo>01</threadInfo>"))
    assert _rect(21, 11, 38, 17, "#ffad00") in ui_thread
    assert _rect(21, 11, 38, 17, "#ffffcc") in any_thread


def _wire_ends(svg: str) -> list[tuple[str, tuple[float, float]]]:
    """Each wire's ("start"|"end", point), drawn in a type colour (not the white
    halo), as it leaves or reaches a terminal."""
    ends: list[tuple[str, tuple[float, float]]] = []
    for d in re.findall(r'<path d="([^"]*)"[^>]*stroke="(?!#ffffff)', svg):
        numbers = [float(v) for v in re.findall(r"-?\d+\.?\d*", d)]
        points = list(zip(numbers[0::2], numbers[1::2]))
        ends += [("start", points[0]), ("end", points[-1])]
    return ends


def test_wires_meet_the_node_edges_at_their_row(tmp_path: Path) -> None:
    """`name`'s row is 36..44 below the node's top (y=10): its centre is y=40. An
    output wire starts at the node's right edge (x=60) and an input wire ends at
    its left edge (x=20)."""
    ends = _wire_ends(_svg(tmp_path))
    assert ("start", (60.0, 40.0)) in ends
    assert ("end", (20.0, 40.0)) in ends


_SAMPLES = Path(".lvkit/cache/samples")
_CORPUS_NODES = [
    pytest.param(
        _SAMPLES
        / "OpenG/extracted/File Group 0/user.lib/_OpenG.lib/largefile/largefile.llb"
        / "LF Write File (SGL)__ogtk.vi",
        _SAMPLES / "OpenG/extracted",
        "241",
        "uint32_t __stdcall WriteFile(uint32_t handle, float *buffer,"
        " uint32_t bytesToRead, uint32_t *bytesRead, uint32_t overlapped);",
        id="LF_Write_File_SGL",
    ),
    pytest.param(
        _SAMPLES
        / "measurement-plugin-labview/Source/Tests/Tests.Runtime"
        / "Measurement Server/Helpers/Write Environment Variable.vi",
        _SAMPLES / "measurement-plugin-labview",
        "538",
        "int32_t __stdcall SetEnvironmentVariableA(char *varName, char *varValue);",
        id="Write_Environment_Variable",
    ),
]


@pytest.mark.needs_samples
@pytest.mark.parametrize("vi_path,root,node_uid,prototype", _CORPUS_NODES)
def test_corpus_wires_meet_the_node_edges_at_their_row(
    vi_path: Path, root: Path, node_uid: str, prototype: str
) -> None:
    if not vi_path.exists():
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = load_vi_by_path(
        vi_path, LoadMode.MINIMAL, search_paths=[root], layout=True
    )
    svg = render_vi(graph, vi_name, interactive=False)
    assert svg is not None
    markup = _node_markup(svg, node_uid)
    ground = re.search(
        r'<rect x="([\d.]+)" y="([\d.]+)" width="40.0" height="([\d.]+)"'
        r' fill="#000000"',
        markup,
    )
    assert ground is not None
    left, top, height = (float(v) for v in ground.groups())
    right, bottom = left + 40, top + height - 1  # the last line is one unit below
    # Row centres: the path and error rows, then a row every 8 units from 18.
    centres = [top + 4.5, top + 13.5] + [
        top + 22 + 8 * row for row in range(int((bottom - top - 18) // 8))
    ]
    on_node = [
        (kind, point)
        for kind, point in _wire_ends(svg)
        if top <= point[1] <= bottom and point[0] in (left, right)
    ]
    assert on_node
    # The hover panel's prototype (wrapped at its commas) follows the title.
    centred = [
        text for text, anchor in _panel_texts(svg, node_uid) if anchor == "middle"
    ]
    assert " ".join(centred[1:]).startswith(prototype)
    for kind, (x, y) in on_node:
        # An output wire starts at the right edge, an input wire ends at the left.
        assert (kind, x) in (("start", right), ("end", left)), (kind, x, y)
        # The wire's own row centre, within the router's half-unit snapping.
        assert min(abs(y - c) for c in centres) <= 0.5, (kind, x, y, centres)


def _panel_texts(svg: str, node_uid: str = "20") -> list[tuple[str, str]]:
    """The hover panel's (text, anchor) pairs."""
    start = svg.index(f'<g class="lv-help" data-node="{node_uid}">')
    panel = svg[start : svg.index("</g>", start)]
    pairs = []
    for attributes, text in re.findall(r"<text ([^>]*)>([^<]+)</text>", panel):
        anchor = re.search(r'text-anchor="(\w+)"', attributes)
        pairs.append((text, anchor.group(1) if anchor else ""))
    return pairs


def test_hover_panel_shows_the_c_prototype_under_the_title(tmp_path: Path) -> None:
    texts = _panel_texts(_svg(tmp_path))
    assert texts[:2] == [
        ("lib.dll:DoWork", "middle"),
        ("void __stdcall DoWork(char *name, int32_t count);", "middle"),
    ]


def test_hover_panel_lists_inputs_left_and_outputs_right(tmp_path: Path) -> None:
    labels = [
        (text, anchor)
        for text, anchor in _panel_texts(_svg(tmp_path, return_type="28"))
        if anchor in ("end", "start") and text in _TERMINAL_NAMES
    ]
    assert labels == [
        ("path in", "end"),
        ("error in (no error)", "end"),
        ("name", "end"),
        ("count", "end"),
        ("path out", "start"),
        ("error out", "start"),
        ("return value", "start"),
        ("name output", "start"),
        ("count output", "start"),
    ]


_TERMINAL_NAMES = {
    "path in",
    "error in (no error)",
    "name",
    "count",
    "path out",
    "error out",
    "return value",
    "name output",
    "count output",
}
