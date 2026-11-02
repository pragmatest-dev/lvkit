"""Handmade LabVIEW classes for tests: a project of classes whose VIs are built from
graph nodes, generated into a package and imported.

``Project.add_class`` and ``Project.add_method`` make the class node and the
ownership edge the loader makes (scope, accessor, dynamic dispatch); a VI is a
``Recipe`` that returns its connector pane and nodes. ``Project.generate`` writes
the package the pipeline would (one module per class, one per free VI) and
``Package.use`` imports it."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from lvkit.class_property import PropertyRole, property_role
from lvkit.codegen import build_module
from lvkit.codegen.gaps import Gap
from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext, VINode, Wire
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from lvkit.pipeline import create_import_resolver
from tests.helpers import build_graph

INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")

Recipe = Callable[[str], tuple[VIContext, list[GraphNode]]]


def class_type(name: str) -> LVType:
    """The type of a wire that carries an object of the class ``name``."""
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname=f"{name}.lvclass",
    )


def pane(
    vi: str,
    direction: str,
    tag: str,
    lv_type: LVType,
    name: str,
    index: int,
    wiring_rule: ParsedWiringRule | None = None,
) -> FPTerminal:
    """A control (``input``) or indicator (``output``) on the connector pane; a
    control is Required unless ``wiring_rule`` says otherwise."""
    return FPTerminal(
        id=f"{vi}:{tag}",
        index=index,
        direction=direction,
        name=name,
        lv_type=lv_type,
        is_public=True,
        is_indicator=direction == "output",
        wiring_rule=(
            wiring_rule
            if wiring_rule is not None
            else ParsedWiringRule.REQUIRED
            if direction == "input"
            else ParsedWiringRule.INVALID
        ),
    )


def wire(vi: str, source: str, target: str) -> Wire:
    return Wire.from_terminals(f"{vi}:{source}", f"{vi}:{target}")


def mux(
    vi: str, tag: str, bundle: bool, owner: LVType, field_type: LVType, index: int
) -> PrimitiveNode:
    """The Bundle (``bundle``) or Unbundle By Name of the field ``index`` of the
    class ``owner``; its terminals are ``<tag>.agg``, ``<tag>.field`` and, for a
    bundle, ``<tag>.agg_out``."""
    terminals = [
        Terminal(
            id=f"{vi}:{tag}.agg",
            index=0,
            direction="input",
            lv_type=owner,
            nmux_role="agg",
        ),
        Terminal(
            id=f"{vi}:{tag}.field",
            index=2 if bundle else 1,
            direction="input" if bundle else "output",
            lv_type=field_type,
            nmux_role="list",
            nmux_field_index=index,
        ),
    ]
    if bundle:
        terminals.append(
            Terminal(
                id=f"{vi}:{tag}.agg_out",
                index=1,
                direction="output",
                lv_type=owner,
                nmux_role="agg",
            )
        )
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Bundle By Name" if bundle else "Unbundle By Name",
        node_type="nMux",
        terminals=terminals,
    )


def add(vi: str, tag: str) -> PrimitiveNode:
    """Add: ``<tag>.x`` + ``<tag>.y`` = ``<tag>.sum``."""
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Add",
        prim_id=1050,
        terminals=[
            Terminal(id=f"{vi}:{tag}.y", index=1, direction="input", lv_type=INT),
            Terminal(id=f"{vi}:{tag}.x", index=2, direction="input", lv_type=INT),
            Terminal(id=f"{vi}:{tag}.sum", index=0, direction="output", lv_type=INT),
        ],
    )


def call(
    vi: str,
    tag: str,
    callee: str,
    node_type: str,
    inputs: list[tuple[str, int, LVType]],
    outputs: list[tuple[str, int, LVType]],
) -> VINode:
    """A call of the VI ``callee`` whose terminals are ``<tag>.<name>``."""
    terminals = [
        Terminal(
            id=f"{vi}:{tag}.{name}",
            index=index,
            direction="input",
            lv_type=lv_type,
            name=name,
        )
        for name, index, lv_type in inputs
    ] + [
        Terminal(
            id=f"{vi}:{tag}.{name}",
            index=index,
            direction="output",
            lv_type=lv_type,
            name=name,
        )
        for name, index, lv_type in outputs
    ]
    return VINode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name=Path(callee).name,
        node_type=node_type,
        callee_key=callee,
        terminals=terminals,
    )


@dataclass
class Package:
    """The generated modules of a project, on disk."""

    root: Path
    sources: dict[str, str]
    gaps: list[Gap]

    @contextmanager
    def use(self) -> Iterator[Callable[[str], ModuleType]]:
        """Import the package; ``module("base")`` is ``pkg.base``."""
        sys.path.insert(0, str(self.root.parent))
        try:
            yield lambda name: importlib.import_module(f"pkg.{name}")
        finally:
            sys.path.remove(str(self.root.parent))
            for name in [m for m in sys.modules if m == "pkg" or m.startswith("pkg.")]:
                del sys.modules[name]


@dataclass
class Project:
    """Classes and free VIs built from nodes."""

    tmp_path: Path
    graph: InMemoryVIGraph = field(default_factory=InMemoryVIGraph)
    classes: dict[str, str] = field(default_factory=dict)
    vis: dict[str, str] = field(default_factory=dict)
    members: dict[str, str] = field(default_factory=dict)
    free: list[str] = field(default_factory=list)
    parents: dict[str, str] = field(default_factory=dict)

    def add_class(
        self,
        name: str,
        fields: list[ClusterField],
        parent: str | None = None,
        parent_found: bool = True,
    ) -> str:
        key = str((self.tmp_path / "src" / f"{name}.lvclass").resolve())
        self.graph._dep_graph.add_node(  # noqa: SLF001
            key,
            node_type=NodeType.CLASS,
            fields=fields,
            parent_class=parent or "LabVIEW Object",
            parent_key=self.classes[parent] if parent and parent_found else None,
            name=f"{name}.lvclass",
            qname=f"{name}.lvclass",
            method_paths={},
        )
        self.graph._vi_display_names[key] = f"{name}.lvclass"  # noqa: SLF001
        self.classes[name] = key
        return key

    def _register(self, key: str, recipe: Recipe, display: str) -> VIContext:
        context, nodes = recipe(key)
        build_graph(context, key, nodes, graph=self.graph)
        self.graph._graph.add_node(  # noqa: SLF001
            key,
            node=VINode(
                id=key,
                vi_path=key,
                name=Path(key).name,
                terminals=[
                    *context.inputs,
                    *context.off_pane_inputs,
                    *context.outputs,
                    *context.off_pane_outputs,
                ],
            ),
        )
        self.graph._dep_graph.add_node(key)  # noqa: SLF001
        self.graph._vi_display_names[key] = display  # noqa: SLF001
        return context

    def key_of(self, cls: str, vi: str) -> str:
        """The key a method VI will have (known before it is added, so a call of
        it can be built first)."""
        return str((self.tmp_path / "src" / cls / f"{vi}.vi").resolve())

    def add_method(
        self,
        cls: str,
        vi: str,
        recipe: Recipe,
        scope: str = "public",
        static: bool = False,
        property_name: str | None = None,
    ) -> str:
        """The method VI ``vi`` of the class ``cls``. ``static`` is a static
        (not dynamic-dispatch) VI; ``property_name`` lists it in the Property
        Definition folder of that property."""
        key = self.key_of(cls, vi)
        class_key = self.classes[cls]
        context = self._register(key, recipe, f"{cls}.lvclass:{vi}.vi")
        self.graph._dep_graph.nodes[class_key]["method_paths"][  # noqa: SLF001
            f"{vi}.vi"
        ] = key
        role: PropertyRole | None = None
        if property_name is not None:
            role = property_role(
                context.inputs,
                context.outputs,
                lambda t, cls=cls: bool(
                    t.lv_type and t.lv_type.classname == f"{cls}.lvclass"
                ),
            )
        self.graph._add_ownership_edge(  # noqa: SLF001
            class_key,
            key,
            scope=scope,
            is_accessor=property_name is not None,
            accessor_type=role.value if role else None,
            accessor_field=property_name,
            is_static=static,
            must_override=False,
            must_call_parent=False,
        )
        self.members[key] = class_key
        self.vis[f"{cls}.{vi}"] = key
        return key

    def add_vi(self, vi: str, recipe: Recipe) -> str:
        """A VI of no class."""
        key = str((self.tmp_path / "src" / f"{vi}.vi").resolve())
        self._register(key, recipe, f"{vi}.vi")
        self.free.append(key)
        self.vis[vi] = key
        return key

    def generate(self, gaps: list[Gap] | None = None) -> Package:
        """Write the package the pipeline would write for the project."""
        graph = self.graph
        gaps = gaps if gaps is not None else []
        out = self.tmp_path / "pkg"
        out.mkdir(exist_ok=True)
        (out / "__init__.py").write_text("")
        for key in self.vis.values():
            for class_key in self.classes.values():
                graph._dep_graph.add_edge(key, class_key)  # noqa: SLF001
        paths: dict[str, Path] = {
            class_key: out / f"{name.lower()}.py"
            for name, class_key in self.classes.items()
        }
        for member, class_key in self.members.items():
            paths[member] = paths[class_key]
        for key in self.free:
            paths[key] = out / f"{Path(key).stem.lower()}.py"
        sources: dict[str, str] = {}
        for name, class_key in self.classes.items():
            resolver = create_import_resolver(
                "pkg",
                out,
                paths,
                paths[class_key],
                caller_key=class_key,
                graph=graph,
                defined_names=[name],
            )
            sources[name.lower()] = build_type_module(
                graph, class_key, resolver, gaps=gaps
            )
        for key in self.free:
            resolver = create_import_resolver(
                "pkg", out, paths, paths[key], caller_key=key, graph=graph
            )
            sources[Path(key).stem.lower()] = build_module(
                graph.get_vi_context(key),
                key,
                import_resolver=resolver,
                graph=graph,
                gaps=gaps,
            )
        for module, source in sources.items():
            (out / f"{module}.py").write_text(source, encoding="utf-8")
        return Package(out, sources, gaps)
