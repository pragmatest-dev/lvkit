"""A VI that LabVIEW shows as broken is a gap, not an lvkit error: the run goes
on, the module raises ``NotImplementedError`` where the node runs, and the run is
Partial. Each case is a handmade diagram generated into a package and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit import cli
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from tests.class_graphs import INT, Project, Recipe, call, class_type, pane, wire

BASE = class_type("Base")


def _method(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A method of Base: object in at pane slot 0, out at slot 1."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "self", BASE, "Self", 0)],
            outputs=[pane(vi, "output", "out", BASE, "Self out", 1)],
            data_flow=[wire(vi, "self", "out")],
        ),
        [],
    )


def _chain(method: str, source_slot: int) -> Recipe:
    """A VI that calls ``method`` on its object, then calls it again on the
    object wired from the first call's output at pane slot ``source_slot``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", BASE, "obj", 0)],
                outputs=[pane(vi, "output", "res", BASE, "res", 1)],
                data_flow=[
                    wire(vi, "obj", "a.self"),
                    wire(vi, "a.self out", "b.self"),
                    wire(vi, "b.self out", "res"),
                ],
            ),
            [
                call(
                    vi,
                    "a",
                    method,
                    "dynIUse",
                    [("self", 0, BASE)],
                    [("self out", source_slot, BASE)],
                ),
                call(
                    vi,
                    "b",
                    method,
                    "dynIUse",
                    [("self", 0, BASE)],
                    [("self out", 1, BASE)],
                ),
            ],
        )

    return build


def _unwired(method: str) -> Recipe:
    """A VI that calls ``method`` by dynamic dispatch with no object wired."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "x", INT, "x", 0)],
                outputs=[pane(vi, "output", "y", INT, "y", 1)],
                data_flow=[wire(vi, "x", "y")],
            ),
            [call(vi, "c", method, "dynIUse", [("self", 0, BASE)], [])],
        )

    return build


def test_a_wire_from_a_slot_the_called_vi_does_not_have_is_a_gap(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [])
    method = project.add_method("Base", "Destroy", _method)
    project.add_vi("Chain", _chain(method, 15))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert [g.reason for g in gaps] == [
        "The wired input 'self' of 'Destroy.vi' comes from connector-pane "
        "position 15 of 'Destroy.vi', which the called VI does not have, so the "
        "VI is broken in LabVIEW"
    ]
    with package.use() as module:
        base = module("base").Base()
        with pytest.raises(NotImplementedError, match="position 15"):
            module("chain").chain(obj=base)


def test_a_wire_from_a_slot_the_called_vi_has_is_not_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [])
    method = project.add_method("Base", "Destroy", _method)
    project.add_vi("Chain", _chain(method, 1))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        base = module("base").Base()
        assert module("chain").chain(obj=base).res is not None


def test_an_unwired_dynamic_dispatch_object_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [])
    method = project.add_method("Base", "Destroy", _method)
    project.add_vi("Use", _unwired(method))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert [g.reason for g in gaps] == [
        "The call of the dynamic-dispatch method 'destroy' has no object wired to "
        "its class input, but a dynamic dispatch input must be wired, so the VI "
        "is broken in LabVIEW"
    ]
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="must be wired"):
            module("use").use(x=1)


SAMPLES = Path(".lvkit/cache/samples/LabVIEW-OOP-Classes")


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    ("vi", "reason"),
    [
        (
            "Utility/UUT/Example - Simple Test.vi",
            "has no object wired to its class input, but a dynamic dispatch input "
            "must be wired, so the VI is broken in LabVIEW",
        ),
        (
            "Hardware/TDK-Lambda Power Supply/Example - Single On-Off.vi",
            "which the called VI does not have, so the VI is broken in LabVIEW",
        ),
    ],
)
def test_a_sample_vi_broken_in_labview_is_a_partial_run(
    vi: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "lvkit",
            "generate",
            str(SAMPLES / vi),
            "-o",
            str(tmp_path),
            "--no-auto-vilib",
        ],
    )
    assert cli.main() == 3
    err = capsys.readouterr().err
    assert reason in err
    assert "Partial:" in err
