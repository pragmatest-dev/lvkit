"""A class generates as a plain Python class; a child descends from its parent
class only. Preserve Run-Time Class and To More Specific Class pass the
object through."""

from __future__ import annotations

import sys
from pathlib import Path

from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.pipeline import create_import_resolver
from lvkit.primitive_resolver import get_resolver


def _graph(base: str, child: str) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    graph._dep_graph.add_node(  # noqa: SLF001
        base,
        node_type=NodeType.CLASS,
        fields=[ClusterField(name="count", type=number)],
        parent_class="LabVIEW Object",
    )
    graph._dep_graph.add_node(  # noqa: SLF001
        child,
        node_type=NodeType.CLASS,
        fields=[],
        parent_class="Base",
        parent_key=base,
    )
    return graph


def test_root_class_is_plain_and_child_descends_from_parent_only(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "__init__.py").write_text("")
    base, child = "/src/Base.lvclass", "/src/Child.lvclass"
    paths = {base: out / "base.py", child: out / "child.py"}
    graph = _graph(base, child)
    sources = {}
    for key, path in paths.items():
        resolver = create_import_resolver(
            "pkg", out, paths, path, caller_key=key, graph=graph
        )
        sources[key] = build_type_module(graph, key, resolver)
        path.write_text(sources[key])
    assert "lvkit" not in sources[base] + sources[child]
    assert "class Base:\n" in sources[base]
    assert "class Child(Base):\n" in sources[child]
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        import pkg.base
        import pkg.child

        assert pkg.base.Base.__bases__ == (object,)
        assert pkg.child.Child.__bases__ == (pkg.base.Base,)
    finally:
        for name in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
            del sys.modules[name]


def test_class_casts_pass_the_object_through() -> None:
    resolver = get_resolver()
    preserve = resolver.resolve(prim_id=8018)
    specific = resolver.resolve(prim_id=8016)
    assert preserve is not None and specific is not None
    assert specific.python_code == {
        "specific class reference": "in_3",
        "error out": "in_4",
    }
    assert "lvkit" not in str(preserve.python_code)
