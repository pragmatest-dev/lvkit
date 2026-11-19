"""``Strings[]`` of an Enum, a property the file saves and NI says a running VI cannot
write (``control_properties.json`` ``settable_while_running``): a read of an Enum
reference gives the items the control is saved with, a read of an Enum control of the
VI's own is the same list, and a write is a gap. Each case is a handmade diagram whose
generated package is imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.class_graphs import INT, Project, pane, wire
from tests.test_combo_box_strings import STRINGS
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import ERROR

STRINGS_CODE = (104056839,)
ENUM = LVType(kind=LVTypeKind.ENUM, underlying_type="NumUInt16")
ITEMS = ["red", "green", "blue"]


def enum_control(vi: str) -> FPTerminal:
    """The Enum control ``e`` of ``vi``, saved with ``ITEMS``."""
    terminal = pane(vi, "input", "e", ENUM, "e", 0, tab_index=0, control_type="stdRing")
    return terminal.model_copy(update={"enum_values": ITEMS, "fp_dco_uid": "e-dco"})


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with the Enum control ``e`` and the number ``n``."""
    return (
        VIContext(
            name=vi,
            inputs=[
                enum_control(vi),
                pane(vi, "input", "n", INT, "n", 1, tab_index=1, control_type="stdNum"),
            ],
            outputs=[pane(vi, "output", "out", ENUM, "out", 2, tab_index=2)],
            data_flow=[wire(vi, "e", "out")],
        ),
        [],
    )


def reference_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the Strings[] of the Enum reference it is given."""
    node = property_node(
        vi, "ctl", "Enum", [("strings", STRINGS_CODE, "output", STRINGS)]
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "strings", STRINGS, "strings", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "ctl.strings", "strings"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def reference_writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that writes the Strings[] of the Enum reference it is given."""
    node = property_node(
        vi, "ctl", "Enum", [("strings", STRINGS_CODE, "input", STRINGS)]
    )
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "items", STRINGS, "items", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "items", "ctl.strings"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def bound_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """The VI's own Enum control ``e``, its Strings[] read by a Property Node bound
    to it."""
    node = property_node(
        vi, "p", "Enum", [("strings", STRINGS_CODE, "output", STRINGS)]
    ).model_copy(
        update={"bound_control_uid": "e-uid", "bound_control_dco_uid": "e-dco"}
    )
    return (
        VIContext(
            name=vi,
            inputs=[enum_control(vi)],
            outputs=[pane(vi, "output", "strings", STRINGS, "strings", 1)],
            data_flow=[wire(vi, "p.strings", "strings")],
        ),
        [node],
    )


def project_with(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Reader", reference_reader)
    project.add_vi("Writer", reference_writer)
    return project


def by_label(module: Any) -> dict[str, Any]:
    return {c.label: c for c in module("target").target.panel.controls}


def test_a_read_gives_the_items_the_enum_is_saved_with(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path).generate(gaps)
    assert [g.reason for g in gaps if "Reader" in str(g)] == []
    assert "saved={'e': {'strings': ['red', 'green', 'blue']}}" in (
        package.sources["target"]
    )
    assert ".get_property('strings')" in package.sources["reader"]
    with package.use() as module:
        result = module("reader").reader(ctl=by_label(module)["e"])
        assert result.error_out is None
        assert result.strings == ITEMS
        # the caller's list is a copy of the panel's
        result.strings.append("x")
        assert module("reader").reader(ctl=by_label(module)["e"]).strings == ITEMS


def test_a_control_that_is_not_an_enum_has_no_strings(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate()
    with package.use() as module:
        result = module("reader").reader(ctl=by_label(module)["n"])
        assert result.error_out.code == 1026


def test_a_write_is_a_gap_that_quotes_nis_rule(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path).generate(gaps)
    reasons = [g.reason for g in gaps]
    assert len(reasons) == 1
    assert "Property 'p104056839' on a Enum reference is written" in reasons[0]
    assert "cannot be set while the VI runs" in reasons[0]
    with package.use() as module:
        try:
            module("writer").writer(ctl=by_label(module)["e"], items=["a"])
        except NotImplementedError as error:
            assert "cannot be set while the VI runs" in str(error)
        else:
            raise AssertionError("the write ran")


def test_a_read_of_the_vis_own_enum_control_is_the_saved_list(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Bound", bound_reader)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        assert module("bound").bound(e=0).strings == ITEMS
