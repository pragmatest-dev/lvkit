"""To More Specific Class (8016) on a class wire: an object of the target class
(or a child of it) passes; any other object is error 1448 (NI, LabVIEW
Object-Oriented Programming Error Codes: "Bad type cast. ...") and the specific
class reference is Not A Refnum (NI, "To More Specific Class Function"). A
VI Server reference passes through.

A handmade project of two classes (``tests/class_graphs.py``) is generated,
imported and run."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, class_type, pane, wire

BASE, CHILD = class_type("Base"), class_type("Child")
COUNT = ClusterField(name="Count", type=INT)
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(
            name="status",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        ),
        ClusterField(name="code", type=INT),
        ClusterField(
            name="source",
            type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        ),
    ],
)


def _cast(target: LVType):  # noqa: ANN202
    """``specific = To More Specific Class(target class: a control of
    ``target``'s type, reference: obj)``, error out wired."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = PrimitiveNode(
            id=f"{vi}:cast",
            vi_path=vi,
            name="To More Specific Class",
            prim_id=8016,
            terminals=[
                Terminal(
                    id=f"{vi}:cast.out", index=0, direction="output", lv_type=target
                ),
                Terminal(
                    id=f"{vi}:cast.err", index=1, direction="output", lv_type=ERROR
                ),
                Terminal(
                    id=f"{vi}:cast.tgt", index=2, direction="input", lv_type=target
                ),
                Terminal(id=f"{vi}:cast.ref", index=3, direction="input", lv_type=BASE),
            ],
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", BASE, "obj", 0),
                    pane(
                        vi,
                        "input",
                        "tgt",
                        target,
                        "target",
                        1,
                        ParsedWiringRule.RECOMMENDED,
                    ),
                ],
                outputs=[
                    pane(vi, "output", "specific", target, "specific", 2),
                    pane(vi, "output", "err_out", ERROR, "error out", 3),
                ],
                data_flow=[
                    wire(vi, "obj", "cast.ref"),
                    wire(vi, "tgt", "cast.tgt"),
                    wire(vi, "cast.out", "specific"),
                    wire(vi, "cast.err", "err_out"),
                ],
            ),
            [node],
        )

    return build


def test_a_class_cast_checks_the_target_class(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    project.add_vi("Cast", _cast(CHILD))
    package = project.generate()
    assert package.gaps == []
    assert "    if not isinstance(obj, Child):\n" in package.sources["cast"]
    with package.use() as module:
        cast = module("cast").cast
        child = module("child").Child()
        result = cast(obj=child)
        assert (result.specific, result.error_out) == (child, None)
        result = cast(obj=module("base").Base())
        # The failed cast gives the default object of the target class.
        assert type(result.specific) is module("child").Child
        assert result.specific is not child
        error = result.error_out
        assert (error.status, error.code, error.source) == (
            True,
            1448,
            "To More Specific Class",
        )
        assert error.message.startswith("Bad type cast.")


def test_a_vi_server_reference_cast_passes_through(tmp_path: Path) -> None:
    control = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Control"
    )
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_vi("Cast", _cast(control))
    package = project.generate()
    assert "isinstance" not in package.sources["cast"]
    with package.use() as module:
        reference = object()
        result = module("cast").cast(obj=reference)
        assert (result.specific, result.error_out) == (reference, None)


def test_the_parent_class_casts_to_its_child(tmp_path: Path) -> None:
    """A Base method casts to Child, whose module imports Base as its parent: the
    two modules import each other, so the cast imports Child when it runs."""
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_class("Child", [COUNT], parent="Base")
    project.add_method("Base", "Cast", _cast(CHILD), static=True)
    package = project.generate()
    assert package.gaps == []
    base = package.sources["base"]
    assert "\nfrom .child import Child\n" not in base
    assert (
        "    def cast(self, *, target: Child | None=None) -> CastResult:\n"
        "        from .child import Child\n" in base
    )
    with package.use() as module:
        child = module("child").Child()
        assert child.cast().specific is child
        assert module("base").Base().cast().error_out.code == 1448
