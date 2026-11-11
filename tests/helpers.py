"""Shared test helpers for lvkit tests.

Provides graph and context construction for tests that need CodeGenContext
with a proper graph (required since bind/resolve store var_names on the graph).
"""

from __future__ import annotations

import itertools
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE
from lvkit.codegen.panels_module import PANELS_MODULE_NAME, PANELS_MODULE_SOURCE
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.load_mode import LoadMode
from lvkit.models import FPTerminal, Terminal


def transitive_closure(
    entry: Path, root: Path, *, include_entry: bool = False
) -> set[Path]:
    """The MINIMAL dependency closure the web extension's
    ``stageDependencyClosure`` stages into ``/proj`` — mirrored here the SAME
    way the extension does it: MINIMAL-load ``entry`` and ask the loader which
    files that load resolved (``get_dependency_paths``). Over a complete on-disk
    corpus every dependency is present, so a single load names the whole
    closure; the extension reaches the same set incrementally as it fetches
    absent files into ``/proj``. ``include_entry`` seeds the result with
    ``entry`` itself (the extension stages the opened VI too)."""
    entry = entry.resolve()
    graph = InMemoryVIGraph()
    name = graph.load_vi(entry, LoadMode.MINIMAL)
    closure = {p.resolve() for p in graph.get_dependency_paths(name or str(entry))}
    if include_entry:
        closure.add(entry)
    return closure


def progressive_closure(entry: Path, root: Path, proj: Path) -> set[Path]:
    """The dependency closure reached by REPLAYING the web extension's
    progressive staging loop against an initially-empty ``/proj`` — the ACTUAL
    absent-then-fetch path, not a single load over the full on-disk tree.

    Desktop/native tests load with every file already present, so they never
    exercise the loader's ABSENT branches (a dep must be NAMED by its recorded
    path AND EDGED to its caller while its file is missing, and its stub key must
    match the key a later present load produces). Those branches are what the web
    depends on and what silently regresses. This mirrors ``stageDependencyClosure``:
    stage only ``entry`` into ``proj``, MINIMAL-load it by its recorded paths,
    fetch every newly-named dependency from ``root`` into ``proj`` preserving
    layout, and repeat until the set stops growing.

    Returns the staged deps as their ORIGINAL ``root`` resolved paths (same shape
    as ``transitive_closure`` for direct comparison) — a converged progressive
    run MUST equal the single-pass closure, or the absent-path logic is broken.
    """
    entry, root = entry.resolve(), root.resolve()
    rel_entry = entry.relative_to(root)
    staged: set[Path] = set()

    def fetch(rel: Path) -> bool:
        src = root / rel
        dst = proj / rel
        if rel in staged or not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        staged.add(rel)
        return True

    fetch(rel_entry)
    entry_in_proj = proj / rel_entry
    for _ in range(20):  # bounded; real closures converge in a handful of passes
        graph = InMemoryVIGraph()
        name = graph.load_vi(entry_in_proj, LoadMode.MINIMAL)
        added = 0
        for dep in graph.get_dependency_paths(name or str(entry_in_proj)):
            try:
                rel = dep.resolve().relative_to(proj.resolve())
            except ValueError:
                continue  # a dep that didn't resolve under /proj — skip
            if fetch(rel):
                added += 1
        if added == 0:
            break

    return {(root / rel).resolve() for rel in staged if rel != rel_entry}


_RENDER_ID_RE = re.compile(r"lv-[a-z0-9-]*-vi", re.IGNORECASE)


def normalize_render_ids(svg: str) -> str:
    """Normalize the root ``id`` and every clip id/ref of a render for a
    web/desktop byte-parity comparison. They are ``lv-<display name slug>-<digest
    of the drawn elements>`` plus ``-cN`` for clips: none of it holds a file
    path, so the same drawing has the same ids on every machine, and this only
    rewrites the display-name part (``lv-<slug>-vi``) so the comparison does not
    depend on how a fixture is named. It mirrors
    ``editors/vscode/build/check-web-parity.sh``'s ``norm()``
    (``sed -E 's/lv-[A-Za-z0-9-]*-vi/lv-ID/g'``, global), case-insensitive in both
    so the Python parity tests and the shell CI gate can't drift."""
    return _RENDER_ID_RE.sub("lv-ID", svg)


def make_node(node_id: str, terminal_ids: list[str]) -> PrimitiveNode:
    """Create a graph node with terminals."""
    return PrimitiveNode(
        id=node_id,
        vi_path="test.vi",
        name=node_id,
        terminals=[
            Terminal(id=tid, index=i, direction="output")
            for i, tid in enumerate(terminal_ids)
        ],
    )


def make_graph_with_terminals(*terminal_ids: str) -> InMemoryVIGraph:
    """Create a graph with nodes that have the given terminals."""
    graph = InMemoryVIGraph()
    for i, tid in enumerate(terminal_ids):
        nid = f"n{i}"
        node = make_node(nid, [tid])
        graph._graph.add_node(nid, node=node)
        graph._term_to_node[tid] = nid
    return graph


def make_graph_with_edge(
    src_tid: str,
    dst_tid: str,
    src_node: str = "p1",
    dst_node: str = "p2",
) -> InMemoryVIGraph:
    """Create a graph with one edge between two nodes."""
    graph = InMemoryVIGraph()
    src = make_node(src_node, [src_tid])
    dst = make_node(dst_node, [dst_tid])
    graph._graph.add_node(src_node, node=src)
    graph._graph.add_node(dst_node, node=dst)
    graph._graph.add_edge(
        src_node,
        dst_node,
        source=WireEnd(terminal_id=src_tid, node_id=src_node),
        dest=WireEnd(terminal_id=dst_tid, node_id=dst_node),
    )
    graph._term_to_node[src_tid] = src_node
    graph._term_to_node[dst_tid] = dst_node
    return graph


def make_ctx(*terminal_ids: str) -> CodeGenContext:
    """Create a CodeGenContext with a graph that has the given terminals."""
    graph = make_graph_with_terminals(*terminal_ids)
    return CodeGenContext(graph=graph)


def build_graph(vi_context, vi_name: str, nodes: list, graph=None):
    """Build an InMemoryVIGraph for a codegen unit test: stub FP terminals for
    ``vi_context``'s inputs/outputs/constants, register the given (already
    graph-native) ``GraphNode``s as ``vi_name``'s node set, and wire up
    ``vi_context.data_flow`` — so a ``build_module`` unit test can pass a real
    graph, since ``build_module`` walks ``graph.top_level_nodes`` (there is no
    more projected ``vi_context.operations`` to build one from).

    Callers build the typed graph nodes directly (``LoopNode``/
    ``CaseStructureNode``/``VINode``/``PrimitiveNode``/... — set ``.parent``/
    ``.frame`` on children before passing them in here; ``register_nodes``
    derives each parent's forward ``children`` list from those back-links).
    """
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import ConstantNode, PrimitiveNode, WireEnd

    if graph is None:
        graph = InMemoryVIGraph()
    graph._vi_nodes.setdefault(vi_name, set())

    def _stub(tid: str, direction: str, control: Terminal | None = None) -> None:
        nid = f"_fp_{tid}"
        if nid in graph._graph:
            return
        stub_terminal = (
            control.model_copy(update={"direction": direction})
            if isinstance(control, FPTerminal)
            else Terminal(id=tid, index=0, direction=direction)
        )
        graph._graph.add_node(
            nid,
            node=PrimitiveNode(
                id=nid,
                vi_path=vi_name,
                name=nid,
                terminals=[stub_terminal],
            ),
        )
        graph._term_to_node[tid] = nid

    for inp in [*vi_context.inputs, *vi_context.off_pane_inputs]:
        if inp.id:
            _stub(inp.id, "output", inp)
    for out in [*vi_context.outputs, *vi_context.off_pane_outputs]:
        if out.id:
            _stub(out.id, "input", out)
    # A constant's node and its one output terminal share its id, as the loader
    # builds them (``graph/construction.py``).
    for const in vi_context.constants:
        if const.id and const.id not in graph._graph:
            graph._graph.add_node(
                const.id,
                node=ConstantNode(
                    id=const.id,
                    vi_path=vi_name,
                    name=const.id,
                    value=const.value,
                    lv_type=const.lv_type,
                    terminals=[Terminal(id=const.id, index=0, direction="output")],
                ),
            )
            graph._term_to_node[const.id] = const.id
            graph._vi_nodes[vi_name].add(const.id)

    register_nodes(graph, nodes, vi_name=vi_name)

    for wire in vi_context.data_flow:
        src_tid = wire.from_terminal_id
        dst_tid = wire.to_terminal_id
        src_nid = graph._term_to_node.get(src_tid)
        dst_nid = graph._term_to_node.get(dst_tid)
        if src_nid is None or dst_nid is None:
            continue
        graph._graph.add_edge(
            src_nid,
            dst_nid,
            source=WireEnd(terminal_id=src_tid, node_id=src_nid),
            dest=WireEnd(terminal_id=dst_tid, node_id=dst_nid),
        )

    return graph


def tunnel_terminals(tunnels):
    """Express a list of ``Tunnel`` objects as the paired outer/inner
    ``TunnelTerminal``s a ``StructureNode`` carries, so ``ctx.tunnels`` (which
    reconstructs tunnels from a node's terminals) yields the same set. Lets a
    codegen unit test build a structure ``GraphNode`` with an equivalent
    ``tunnels=[...]`` list.
    """
    from lvkit.models import TunnelTerminal

    out: list = []
    for i, t in enumerate(tunnels):
        common: dict[str, Any] = dict(
            tunnel_type=t.tunnel_type,
            mode=t.mode,
            conditional=t.conditional,
            condition_terminal=t.condition_terminal_uid,
            sr_initialized=t.sr_initialized,
            sr_stack_depth=t.sr_stack_depth,
        )
        out.append(
            TunnelTerminal(
                id=t.outer_terminal_uid,
                index=2 * i,
                direction="input",
                boundary="outer",
                paired_id=t.inner_terminal_uid,
                **common,
            )
        )
        out.append(
            TunnelTerminal(
                id=t.inner_terminal_uid,
                index=2 * i + 1,
                direction="output",
                boundary="inner",
                paired_id=t.outer_terminal_uid,
                **common,
            )
        )
    return out


def register_nodes(graph, nodes, vi_name: str = "test.vi") -> None:
    """Register typed GraphNodes into ``graph`` the way construction does:
    add each node, index its terminals, add it to the VI's node set, and
    populate each structure's forward ``children`` list from the ``parent``
    back-links (sorted by ``_node_order_key``, like the real builder).

    Lets a codegen unit test hand structure emitters real graph nodes to walk
    via ``ctx.child_nodes`` / ``ctx.frame_children``.
    """
    from collections import defaultdict

    from lvkit.graph.core import _node_order_key

    graph._vi_nodes.setdefault(vi_name, set())
    for n in nodes:
        graph._graph.add_node(n.id, node=n)
        graph._vi_nodes[vi_name].add(n.id)
        for t in n.terminals:
            # setdefault: never steal a terminal a prior (e.g. from_wires) node
            # already owns, so existing wire edges keep resolving.
            graph._term_to_node.setdefault(t.id, n.id)

    kids: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.parent:
            kids[n.parent].append(n.id)
    for n in nodes:
        if n.id in kids:
            n.children = sorted(kids[n.id], key=_node_order_key)


def error_cluster_type() -> Any:
    """LabVIEW's error cluster as the parser represents it: ``status`` Boolean,
    ``code`` I32, ``source`` String."""
    from lvkit.models import ClusterField, LVType, LVTypeKind

    def primitive(underlying: str) -> LVType:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)

    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="status", type=primitive("Boolean")),
            ClusterField(name="code", type=primitive("NumInt32")),
            ClusterField(name="source", type=primitive("String")),
        ],
    )


_generated_modules = itertools.count()

# The package a generated module runs in, holding the modules every generated
# package root has (``from .errors import LabVIEWError``, ``from .live_io import
# LiveIO, NO_LIVE_IO``).
GENERATED_PACKAGE = "generated_package"


_PANEL_LINE = re.compile(r"^([\w.]+\.panel = Panel\(|from \.+panels import )")


def without_panel_attributes(source: str) -> str:
    """``source`` without the lines that give a function its ``panel`` and import
    ``Panel`` (``codegen/panel_description.py``): a test that pins a function's
    exact source is about the function, and ``tests/test_front_panel_reference.py``
    pins the panel."""
    return "\n".join(line for line in source.split("\n") if not _PANEL_LINE.match(line))


def install_generated_package() -> None:
    """Register ``GENERATED_PACKAGE`` and its ``errors`` and ``live_io`` modules
    in ``sys.modules`` (once)."""
    if GENERATED_PACKAGE in sys.modules:
        return
    package = ModuleType(GENERATED_PACKAGE)
    package.__path__ = []
    sys.modules[GENERATED_PACKAGE] = package
    for name, source in (
        (ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE),
        (LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE),
        (PANELS_MODULE_NAME, PANELS_MODULE_SOURCE),
    ):
        module = ModuleType(f"{GENERATED_PACKAGE}.{name}")
        sys.modules[module.__name__] = module
        exec(compile(source, f"{name}.py", "exec"), module.__dict__)  # noqa: S102


def write_support_modules(package_dir: Path) -> None:
    """Write the modules every generated package root has (``errors.py`` and
    ``panels.py``) into ``package_dir``, for a test that writes a package by
    hand. A module already there is kept."""
    for name, source in (
        (ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE),
        (PANELS_MODULE_NAME, PANELS_MODULE_SOURCE),
    ):
        module = package_dir / f"{name}.py"
        if not module.exists():
            module.write_text(source, encoding="utf-8")


def exec_module(
    source: str, filename: str = "<generated>", name: str | None = None
) -> ModuleType:
    """Run generated module ``source`` as the module ``name`` (a fresh one when
    None) of ``GENERATED_PACKAGE``, registered in ``sys.modules`` before its code
    runs: a result class is a ``dataclass``, which looks its module up while it
    is defined."""
    install_generated_package()
    module_name = name or f"_generated_{next(_generated_modules)}"
    module = ModuleType(f"{GENERATED_PACKAGE}.{module_name}")
    module.__package__ = GENERATED_PACKAGE
    sys.modules[module.__name__] = module
    exec(compile(source, filename, "exec"), module.__dict__)  # noqa: S102
    return module


class _NamespaceModule:
    """What ``sys.modules`` holds for a namespace ``exec_generated`` runs in."""


def exec_generated(
    source, namespace: dict | None = None, filename: str = "<generated>"
) -> dict:
    """Run generated ``source`` (text or an ``ast.Module``) in ``namespace`` (a
    fresh one when None) as a module of ``GENERATED_PACKAGE``, so its relative
    imports (``from .panels import Panel``) resolve the way they do in a generated
    package. Returns the namespace."""
    install_generated_package()
    ns = {} if namespace is None else namespace
    ns.setdefault("__name__", f"{GENERATED_PACKAGE}._bare_{next(_generated_modules)}")
    ns.setdefault("__package__", GENERATED_PACKAGE)
    # A result class is a ``dataclass``, which looks its module up in
    # ``sys.modules`` while it is defined: register one whose dict is ``ns``.
    holder = _NamespaceModule()
    holder.__dict__ = ns
    sys.modules[ns["__name__"]] = holder  # type: ignore[assignment]
    exec(compile(source, filename, "exec"), ns)  # noqa: S102
    return ns


def vi_key(graph, text: str) -> str:
    """The key of the one VI of ``graph`` that ``text`` names: a key, a
    qualified name, or a file name. A test's way to say which VI it loaded."""
    if text in graph._vi_nodes:
        return text
    leaf = text.rsplit(":", 1)[-1]
    for matches in (
        lambda d: d.get("qname") == text,
        lambda d: d.get("name") == text,
        lambda d: ":" in text and d.get("name") == leaf,
    ):
        keys = [k for k, d in graph._dep_graph.nodes(data=True) if matches(d)]
        if len(keys) == 1:
            return keys[0]
        if keys:
            raise AssertionError(f"{text!r} matches several VIs: {sorted(keys)}")
    folded = [
        k
        for k, d in graph._dep_graph.nodes(data=True)
        if (d.get("qname") or "").casefold() == text.casefold()
    ]
    return folded[0] if len(folded) == 1 else text


def with_file_keys(lv_type, keys: dict[str, str]):
    """A copy of ``lv_type`` (and its fields, elements and values) whose class or
    typedef types carry ``file_key``, taken from ``keys`` by the name the type
    spells (compared without regard to case): the tie a loaded VI makes through
    its link records, written out for a handmade graph."""
    import dataclasses

    if lv_type is None:
        return None
    folded = {name.casefold(): key for name, key in keys.items()}
    changes: dict = {}
    name = lv_type.classname or lv_type.typedef_name
    if name and name.casefold() in folded:
        changes["file_key"] = folded[name.casefold()]
    for attr in ("element_type", "value_type"):
        inner = getattr(lv_type, attr)
        if inner is not None:
            changes[attr] = with_file_keys(inner, keys)
    if lv_type.pane:
        changes["pane"] = tuple(
            dataclasses.replace(slot, type=with_file_keys(slot.type, keys))
            for slot in lv_type.pane
        )
    if lv_type.fields:
        changes["fields"] = [
            dataclasses.replace(f, type=with_file_keys(f.type, keys))
            for f in lv_type.fields
        ]
    return dataclasses.replace(lv_type, **changes)


def key_graph_types(graph, keys: dict[str, str]) -> None:
    """Give every class and typedef type on the terminals of ``graph``'s nodes
    its ``file_key`` (``with_file_keys``), and a constant's own type too, and the
    class and typedef types of the private data of every class node."""
    import dataclasses

    for _, data in graph._dep_graph.nodes(data=True):
        if data.get("fields"):
            data["fields"] = [
                dataclasses.replace(f, type=with_file_keys(f.type, keys))
                for f in data["fields"]
            ]
    for _, data in graph._graph.nodes(data=True):
        node = data.get("node")
        if getattr(node, "lv_type", None) is not None:
            node.lv_type = with_file_keys(node.lv_type, keys)
        for terminal in getattr(node, "terminals", None) or ():
            terminal.lv_type = with_file_keys(terminal.lv_type, keys)
