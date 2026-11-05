"""A module-level helper (a Formula Node's function, a failure class of an
operation's own) never takes a name the module already holds: a project class
named ``InvalidPathError`` that the VI imports, and Delete's own
``InvalidPathError``, are two names in the generated module, and both run."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from tests.class_graphs import INT, Project, class_type, pane, wire

PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
CLASH = class_type("InvalidPathError")
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


def _cast_and_delete(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """``specific = To More Specific Class(target: InvalidPathError, obj)`` and
    ``Delete(path)``, each error out wired to an indicator."""
    cast = PrimitiveNode(
        id=f"{vi}:cast",
        vi_path=vi,
        name="To More Specific Class",
        prim_id=8016,
        terminals=[
            Terminal(id=f"{vi}:cast.out", index=0, direction="output", lv_type=CLASH),
            Terminal(id=f"{vi}:cast.err", index=1, direction="output", lv_type=ERROR),
            Terminal(id=f"{vi}:cast.tgt", index=2, direction="input", lv_type=CLASH),
            Terminal(id=f"{vi}:cast.ref", index=3, direction="input", lv_type=CLASH),
        ],
    )
    delete = PrimitiveNode(
        id=f"{vi}:del",
        vi_path=vi,
        name="Delete",
        prim_id=8056,
        terminals=[
            Terminal(id=f"{vi}:del.err", index=0, direction="output", lv_type=ERROR),
            Terminal(id=f"{vi}:del.path", index=11, direction="input", lv_type=PATH),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "obj", CLASH, "obj", 0),
                pane(vi, "input", "tgt", CLASH, "target", 1),
                pane(vi, "input", "path", PATH, "path", 2),
            ],
            outputs=[
                pane(vi, "output", "specific", CLASH, "specific", 3),
                pane(vi, "output", "cast_err", ERROR, "cast error", 4),
                pane(vi, "output", "del_err", ERROR, "delete error", 5),
            ],
            data_flow=[
                wire(vi, "obj", "cast.ref"),
                wire(vi, "tgt", "cast.tgt"),
                wire(vi, "cast.out", "specific"),
                wire(vi, "cast.err", "cast_err"),
                wire(vi, "path", "del.path"),
                wire(vi, "del.err", "del_err"),
            ],
        ),
        [cast, delete],
    )


def test_a_helper_class_and_an_imported_class_of_one_name(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_class("InvalidPathError", [ClusterField(name="Count", type=INT)])
    project.add_vi("Both", _cast_and_delete)
    package = project.generate()
    assert package.gaps == []
    source = package.sources["both"]
    # The import keeps its name; Delete's own class is numbered, and its raise
    # and its except name the numbered class.
    assert "from .invalidpatherror import InvalidPathError\n" in source
    assert "class InvalidPathError_2(ValueError):" in source
    assert "raise InvalidPathError_2(" in source
    assert "except InvalidPathError_2 as " in source
    with package.use() as module:
        both = module("both").both
        obj = module("invalidpatherror").InvalidPathError()
        result = both(obj, obj, tmp_path / "a\0b")
        assert result.specific is obj
        assert result.cast_error is None
        assert (result.delete_error.code, result.delete_error.source) == (1, "Delete")
