"""Every property of ``control_properties.json`` works on a control reference
(``codegen/nodes/panel_reference.py``) the way it does on a bound control: a read
gives the last write, else what the file saves, else the property's default, else
raises NotImplementedError naming the property; a write is held by the panel. No
property is opted in one by one. Each case is a handmade diagram whose generated
package is imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import LVType, LVTypeKind
from tests.class_graphs import INT, Project, pane, wire
from tests.test_combo_box_strings import STRINGS
from tests.test_control_reference_properties import reader, writer
from tests.test_front_panel_reference import STRING

DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")

X_MAX = (104078347, 104001542)
CELL_FONT_COLOR = (416911404,)
TREE_ACTIVE_TAG = (1969896461,)
SELECTION_END = (104093700, 103995396)
MAXIMUM = (104050694,)
MINIMUM = (104050693,)
RING_STRINGS = (104056839,)
LABEL_TEXT = (104013829, 103995392)
FILL_COLOR = (104072194,)
DOC_MAX = (1727315969,)


def generate(
    tmp_path: Path,
    class_name: str,
    codes: tuple[int, ...],
    kind: LVType,
    control_type: str,
    fields: dict[str, Any] | None = None,
    gaps: list[Gap] | None = None,
) -> Any:
    """A package with the control ``c`` of front-panel class ``control_type`` (with
    the ``FPTerminal`` ``fields``), a Reader and a Writer of the property ``codes``
    of a ``class_name`` reference."""

    def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
        control = pane(vi, "input", "c", kind, "c", 0, tab_index=0)
        control = control.model_copy(
            update={"control_type": control_type, **(fields or {})}
        )
        return (
            VIContext(
                name=vi,
                inputs=[control],
                outputs=[pane(vi, "output", "out", kind, "out", 1, tab_index=1)],
                data_flow=[wire(vi, "c", "out")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Reader", reader(class_name, codes, kind))
    project.add_vi("Writer", writer(class_name, codes, kind))
    return project.generate(gaps)


def reference(module: Any) -> Any:
    return {c.label: c for c in module("target").target.panel.controls}["c"]


def read(module: Any) -> Any:
    return module("reader").reader(ctl=reference(module)).value


def write(module: Any, value: Any) -> None:
    assert module("writer").writer(ctl=reference(module), new=value).error_out is None


@pytest.mark.parametrize(
    ("class_name", "codes", "kind", "control_type", "value"),
    [
        ("Tree", TREE_ACTIVE_TAG, STRING, "treeControl", "tag"),
        ("Slide (strict)", MAXIMUM, DBL, "stdSlide", 10.0),
        ("Slide (strict)", MINIMUM, DBL, "stdSlide", -1.0),
        ("MCListbox (strict)", CELL_FONT_COLOR, INT, "listbox", 0xFF0000),
        ("Slide", FILL_COLOR, INT, "stdSlide", 0x00FF00),
    ],
)
def test_a_property_of_a_control_works_on_its_reference_with_no_opt_in(
    tmp_path: Path,
    class_name: str,
    codes: tuple[int, ...],
    kind: LVType,
    control_type: str,
    value: Any,
) -> None:
    gaps: list[Gap] = []
    package = generate(tmp_path, class_name, codes, kind, control_type, None, gaps)
    assert gaps == []
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="'c'"):
            read(module)
        write(module, value)
        assert read(module) == value


def test_the_maximum_of_a_graph_scale_is_written_like_its_minimum(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    package = generate(tmp_path, "XYGraph (strict)", X_MAX, DBL, "stdGraph", None, gaps)
    assert gaps == []
    with package.use() as module:
        write(module, 7.0)
        assert read(module) == 7.0


def test_the_text_selection_end_of_a_string_starts_at_zero(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = generate(tmp_path, "Str", SELECTION_END, INT, "stdString", None, gaps)
    assert gaps == []
    with package.use() as module:
        assert read(module) == 0
        write(module, 4)
        assert read(module) == 4


def test_a_rings_strings_are_the_items_it_is_saved_with(tmp_path: Path) -> None:
    ring = LVType(kind=LVTypeKind.RING, underlying_type="NumUInt16")
    package = generate(
        tmp_path,
        "Ring (strict)",
        RING_STRINGS,
        STRINGS,
        "stdRing",
        {"lv_type": ring, "enum_values": ["low", "high"]},
    )
    with package.use() as module:
        assert read(module) == ["low", "high"]
        write(module, ["only"])
        assert read(module) == ["only"]


def test_the_label_text_of_a_reference_is_not_written_and_says_why(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    generate(tmp_path, "Ctl", LABEL_TEXT, STRING, "stdString", None, gaps)
    reasons = [g.reason for g in gaps]
    assert len(reasons) == 1
    assert "cannot be set while the VI runs" in reasons[0]
    assert "decoration/text/text.html" in reasons[0]


def test_a_property_of_another_class_stays_a_gap(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    generate(tmp_path, "Bool", DOC_MAX, DBL, "stdBool", None, gaps)
    assert gaps
