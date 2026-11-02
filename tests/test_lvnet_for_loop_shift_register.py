"""A For Loop's right shift register carries a value out of the loop, whether the
loop's own terminal for it holds the register's class or only refers to the entry
that defines it in the loop's ``srDCOList``. Both read the same in every view: the
lvnet text (and its round trip), describe, and the generated Python, which runs.

The diagram is tiny heap XML (``structure_diagrams.py``): a For Loop of 3
iterations whose left shift register starts at 7; the body adds 1 and feeds the
right shift register, whose value after the loop drives the indicator ``result``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests.structure_diagrams import (
    I32,
    Views,
    constant,
    diag,
    face,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    loop,
    root_diagram,
    srn,
    term,
    tunnel,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity


def _loop_xml(*, listed: bool) -> str:
    """``listed``: the right register is defined in ``srDCOList`` and the loop's
    terminal 442 only refers to it; otherwise 442 carries the ``rSR`` class."""
    extra = ""
    if listed:
        extra = (
            '<srDCOList elements="1"><SL__arrayElement class="rSR" uid="440">'
            f"<typeDesc>TypeID({I32})</typeDesc>"
            '<termList elements="2"><SL__arrayElement uid="441" />'
            '<SL__arrayElement uid="442" /></termList></SL__arrayElement>'
            "</srDCOList>"
        )
        right = term(442, '<dco uid="440"/>')
    else:
        right = tunnel("rSR", 442, 440, [441], output=True)
    border = [
        tunnel("lMax", 410, 411, [412]),
        tunnel("lSR", 420, 421, [422]),
        right,
    ]
    inner = diag(
        450, nodes=[srn(460, [face(412, 411), face(422, 421), face(441, 440)])]
    )
    inner = inner.replace('<nodeList elements="1">', '<nodeList elements="2">', 1)
    inner = inner.replace("</nodeList>", increment(20) + "</nodeList>", 1)
    return root_diagram(
        [loop("forLoop", 400, border, inner, extra)],
        [
            srn(
                800,
                [constant(701, I32, 3), constant(702, I32, 7), fp_terminal(5030, 30)],
            )
        ],
        [
            wire(901, 701, 410),
            wire(902, 702, 420),
            wire(903, 422, 23),
            wire(904, 21, 441),
            wire(905, 442, 5030),
        ],
    )


def _views(tmp_path: Path, *, listed: bool) -> Views:
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    return load(tmp_path, _loop_xml(listed=listed))


@pytest.mark.parametrize("listed", [True, False])
def test_the_register_is_read_after_the_loop_in_lvnet(
    tmp_path: Path, listed: bool
) -> None:
    views = _views(tmp_path, listed=listed)
    assert "shift-register loop_400::shift0 :" in views.lvnet
    assert "\n    result = loop_400::shift0" in views.lvnet
    assert "400::442" not in views.lvnet
    assert views.describe.endswith(
        "## Operations\nFor Loop\n  Increment [prim 1057]\n  Tunnels:\n"
        "    in  count terminal\n    in  shift register\n"
        "    out shift register (result)"
    )


@pytest.mark.parametrize("listed", [True, False])
def test_the_register_text_reads_back_to_the_same_graph(
    tmp_path: Path, listed: bool
) -> None:
    views = _views(tmp_path, listed=listed)
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def test_a_listed_register_reads_like_one_with_its_class_inline(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    listed = _views(tmp_path / "a", listed=True)
    inline = _views(tmp_path / "b", listed=False)
    assert listed.lvnet == inline.lvnet
    assert _body(listed.describe) == _body(inline.describe)
    assert listed.json["body"] == inline.json["body"]


def _body(describe: str) -> str:
    """Describe without its first line, which names the XML file."""
    return describe.split("\n", 1)[1]


@pytest.mark.parametrize("listed", [True, False])
def test_the_generated_function_returns_the_register_value(
    tmp_path: Path, listed: bool
) -> None:
    views = _views(tmp_path, listed=listed)
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, views.vi, graph=views.graph)
    namespace: dict[str, object] = {}
    exec(compile(source, "T.py", "exec"), namespace)  # noqa: S102
    assert namespace["t"]().result == 10  # type: ignore[operator]
    assert "state = 7" in source and "for i in range(3):" in source
