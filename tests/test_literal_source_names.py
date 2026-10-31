"""A literal wired into a For loop's input tunnel is assigned to a local before
the loop. A Path constant's local is named from the path's own components, a
String constant's from its text."""

from __future__ import annotations

import re
from pathlib import Path

from lvkit.codegen.builder import build_module
from tests.structure_diagrams import (
    I32,
    PATH,
    PATH_TYPES,
    STR,
    TYPES,
    Views,
    constant,
    dco,
    diag,
    face,
    fp_terminal,
    front_panel_xml,
    increment,
    load,
    loop,
    path_bytes,
    path_constant,
    root_diagram,
    srn,
    term,
    wire,
)


def _loop_over(tmp_path: Path, source: str, saved: bytes | str) -> str:
    """The generated source of a VI whose For loop takes ``saved`` (a Path
    constant for ``source`` ``"path"``, else a String constant) as a tunnel input."""
    (tmp_path / "T_FPHb.xml").write_text(
        front_panel_xml({}, {30: "result"}, pane={0: 30})
    )
    literal = (
        path_constant(702, saved)  # type: ignore[arg-type]
        if source == "path"
        else constant(702, STR, saved)
    )
    type_id = PATH if source == "path" else STR
    border = [
        term(410, dco("lMax", 411, type_id=I32, faces=[412, 410])),
        term(430, dco("lpTun", 431, type_id=type_id, faces=[432, 430])),
    ]
    inner = diag(450, nodes=[srn(460, [face(412, 411), face(432, 431)])])
    inner = inner.replace('<nodeList elements="1">', '<nodeList elements="2">', 1)
    inner = inner.replace("</nodeList>", increment(20, type_id) + "</nodeList>", 1)
    xml = root_diagram(
        [loop("forLoop", 400, border, inner)],
        [srn(800, [constant(701, I32, 3), literal, fp_terminal(5030, 30)])],
        [
            wire(901, 701, 410),
            wire(902, 702, 430),
            wire(903, 432, 23),
            wire(904, 21, 5030),
        ],
    )
    views: Views = load(tmp_path, xml, TYPES + PATH_TYPES)
    context = views.graph.get_vi_context(views.vi)
    return build_module(context, "T.vi", graph=views.graph)


def test_a_path_constant_local_is_named_from_the_path(tmp_path: Path) -> None:
    saved = path_bytes(0, [b"C", b"Program Files", b"NI"])
    source = _loop_over(tmp_path, "path", saved)
    compile(source, "T.py", "exec")
    assignment = re.search(r"^\s*(\w+) = Path\(.*'Program Files'", source, re.M)
    assert assignment is not None, source
    assert assignment.group(1) == "pathcprogram_filesni"


def test_a_string_constant_local_keeps_its_name(tmp_path: Path) -> None:
    source = _loop_over(tmp_path, "string", "a b")
    compile(source, "T.py", "exec")
    assert re.search(r"^\s*a_b = 'a b'", source, re.M), source
