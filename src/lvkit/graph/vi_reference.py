"""Which VI a VI reference holds, read from the diagram.

A VI reference wired into Call By Reference, Start Asynchronous Call or Property
Node comes from a Static VI Reference or from Open VI Reference. Open VI Reference
opens the VI at its path or name input; when that input is a constant, or the
Name or Path property of a Static VI Reference, the VI it opens is known without
running the diagram. ``VIReferenceMixin`` traces the wires to that VI and names it
by the identity the caller's link records give it (see ``VINode.callee_key``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import ServerClass
from .models import AnyGraphNode, ConstantNode, LocalVariableNode, PrimitiveNode
from .node_kinds import NodeType
from .op_walk import correlate_property_terminals
from .wire_origin import PathNotConstant, WireOriginMixin

if TYPE_CHECKING:
    from ..models import Terminal
    from .models import WireEnd

# Open VI Reference terminals (data/primitives/application-control.json).
OPEN_VI_REFERENCE_PRIM_ID = 8010
OPEN_OPTIONS_INDEX = 9
OPEN_VI_PATH_INDEX = 10
OPEN_VI_REFERENCE_INDEX = 3
OPEN_TYPE_SPECIFIER_INDEX = 11

# The terminals of a Call By Reference or Start Asynchronous Call node that are
# the node's own, not the called VI's (its connector pane is numbered from 0):
# the VI reference in and out, and the node's error in and error out.
CALL_REFERENCE_IN = -1
CALL_REFERENCE_OUT = -2
CALL_ERROR_IN = -3
CALL_ERROR_OUT = -4

# Property Node on the VI class: the PropItemCode a file records for the Name and
# the Path property (the same codes pair with those names in every Property Node
# of the VI class in the corpus).
VI_CLASS = "VI"
VI_NAME_PROPERTY_CODE = 500
VI_PATH_PROPERTY_CODE = 501
# The Execution:State property (NI: properties-and-methods/vi-server/vi/execstate.html):
# the code a Property Node of the VI class records beside the name Exec.State.
VI_EXEC_STATE_PROPERTY_CODE = 557
# The Description property (NI: properties-and-methods/vi-server/vi/description.html):
# the code a Property Node of the VI class records beside the name Description
# (TestCase shortDescription.vi and TestSuite GetDescription.vi both record 502).
VI_DESCRIPTION_PROPERTY_CODE = 502

# The methods of the VI class that open and close a front panel window, by the
# method code a file records beside the name FP.Open / FP.Close (every Invoke
# Node of the corpus that names one records these codes). NI:
# properties-and-methods/vi-server/vi/openfrontpanel.html and closefrontpanel.html.
VI_FP_OPEN_METHOD_CODE = 1080
VI_FP_CLOSE_METHOD_CODE = 1061

# The Front Panel Window:Title property (FP.Title; NI:
# properties-and-methods/vi-server/vi/fpwintitle.html), by the code a Property
# Node records beside that name (553 in every such node of the corpus).
VI_FP_TITLE_PROPERTY_CODE = 553

# The Property Node's VI reference input.
_PROPERTY_REFERENCE_INDEX = 0
# An Invoke Node's VI reference output.
_REFERENCE_OUT_INDEX = 1


class VIReferenceUnresolved(Exception):
    """A VI reference whose VI cannot be told from the diagram. The message says
    why, in LabVIEW's words."""


class VIReferenceComputed(VIReferenceUnresolved):
    """An Open VI Reference whose path or name is computed when the diagram runs:
    the VI it opens is one of the project's VIs, found by that path or name."""


@dataclass(frozen=True)
class ReferencedVI:
    """The VI a VI reference holds: its qualified name and its identity (the key
    of the loaded VI). ``options`` is the Open VI Reference options value the
    reference was opened with: 0 when unwired or for a Static VI Reference, None
    when wired from a computed value."""

    name: str
    key: str
    options: int | None = 0


class VIReferenceMixin(WireOriginMixin):
    """The VI-reference queries of ``InMemoryVIGraph``."""

    if TYPE_CHECKING:
        _vi_nodes: dict[str, set[str]]

        def vi_display_name(self, vi_name: str) -> str: ...
        def is_stub(self, key: str) -> bool: ...
        def node_type(self, key: str) -> NodeType: ...
        def _owner_key(self, key: str, owner_type: NodeType) -> str | None: ...
        def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]: ...
        def vi_label(self, path: Path | str) -> str: ...

    def referenced_vi(self, terminal_id: str) -> ReferencedVI:
        """The VI the VI reference arriving at ``terminal_id`` holds.

        A reference fed from several frames of a case structure holds a VI only
        when every frame gives the same one. Raises ``VIReferenceUnresolved``
        when the reference is not wired, or comes from something other than a
        Static VI Reference or an Open VI Reference whose path or name is
        known."""
        origins = self.wire_origins(terminal_id)
        if not origins:
            raise VIReferenceUnresolved("The VI reference input is not wired.")
        return _same_vi([self._reference_of(n, s) for n, s in origins])

    def _reference_of(self, node: AnyGraphNode, source: WireEnd) -> ReferencedVI:
        if isinstance(node, ConstantNode) and _is_vi_reference(node):
            if node.referenced_vi_key is None:
                raise VIReferenceUnresolved(
                    f"The Static VI Reference {node.value!r} names no VI the "
                    "caller records."
                )
            return ReferencedVI(str(node.value), node.referenced_vi_key)
        if _is_open_vi_reference_output(node, source):
            assert isinstance(node, PrimitiveNode)
            return self.opened_vi(node)
        key = self.terminal_vi(source.terminal_id)
        if is_this_vi(node) and key is not None:
            return ReferencedVI(self.vi_display_name(key), key)
        if _is_front_panel_window_output(node, source) and key is not None:
            assert isinstance(node, PrimitiveNode)
            reference = _input_at(node, _PROPERTY_REFERENCE_INDEX)
            if reference is None or not self.wire_origins(reference.id):
                return ReferencedVI(self.vi_display_name(key), key)
            return self.referenced_vi(reference.id)
        raise VIReferenceUnresolved(
            f"The VI reference is wired from {_describe(node)}, so which VI it "
            "holds is only known when the diagram runs."
        )

    def acts_on_own_front_panel_window(self, node: AnyGraphNode, vi_name: str) -> bool:
        """Whether ``node`` is an Invoke Node of FP.Open or FP.Close on the front
        panel window of the VI ``vi_name`` that holds it: the node's reference is
        unwired (the default VI object, the VI containing the node, as NI documents
        for an Invoke Node of the VI class) or wired from "This VI". FP.Open or
        FP.Close on the reference of another VI, or on one only known when the
        diagram runs, is not."""
        return (
            isinstance(node, PrimitiveNode)
            and node.object_name == VI_CLASS
            and node.method_code in (VI_FP_OPEN_METHOD_CODE, VI_FP_CLOSE_METHOD_CODE)
            and self._reference_is_own(node, vi_name)
        )

    def acts_on_own_window_title(self, node: AnyGraphNode, vi_name: str) -> bool:
        """Whether ``node`` is a Property Node whose every row is Front Panel
        Window:Title (code 553), on the VI ``vi_name`` that holds it: its reference
        is unwired or wired from "This VI". The Title of another VI's reference,
        or of one only known when the diagram runs, is not."""
        return (
            isinstance(node, PrimitiveNode)
            and node.object_name == VI_CLASS
            and bool(node.properties)
            and all(
                len(p.segments) == 1
                and p.segments[0].code == VI_FP_TITLE_PROPERTY_CODE
                for p in node.properties
            )
            and self._reference_is_own(node, vi_name)
        )

    def writes_own_window_title(self, node: AnyGraphNode, vi_name: str) -> bool:
        """Whether ``node`` writes the Title of the VI's own window
        (``acts_on_own_window_title`` with a row that takes a value in)."""
        return (
            isinstance(node, PrimitiveNode)
            and self.acts_on_own_window_title(node, vi_name)
            and any(
                t.id in node.property_value_terminal_ids and t.direction == "input"
                for t in node.terminals
            )
        )

    def _reference_is_own(self, node: PrimitiveNode, vi_name: str) -> bool:
        """Whether the node's VI reference is unwired (the default VI object) or
        resolves to ``vi_name``."""
        reference = _input_at(node, _PROPERTY_REFERENCE_INDEX)
        if reference is None or not self.wire_origins(reference.id):
            return True
        try:
            return self.referenced_vi(reference.id).key == vi_name
        except VIReferenceUnresolved:
            return False

    def opened_vi(self, node: PrimitiveNode) -> ReferencedVI:
        """The VI an Open VI Reference node opens, from its VI path input: a
        constant path or name, a path built from constants, or the Name or Path
        property of a VI reference.

        A name must be the full qualified name of a loaded VI; a path is read
        relative to the VI holding the node. Raises ``VIReferenceUnresolved`` when
        the input is not one of those, or names no loaded VI."""
        path_term = _input_at(node, OPEN_VI_PATH_INDEX)
        origins = self.wire_origins(path_term.id) if path_term is not None else []
        if path_term is None or not origins:
            raise VIReferenceUnresolved("The VI path input is not wired.")
        target = _same_vi([self._opened_by(path_term, n, s) for n, s in origins])
        return ReferencedVI(target.name, target.key, self._open_options(node))

    def opened_vi_edges(self) -> list[tuple[str, str]]:
        """``(VI, opened VI)`` for each Open VI Reference of a loaded VI whose VI
        is known without running the diagram (``opened_vi``): the generated code
        imports the opened VI's function like a subVI's. Sorted."""
        edges: set[tuple[str, str]] = set()
        for key in sorted(self._vi_nodes):
            for node in self.iter_nodes(key):
                if not (
                    isinstance(node, PrimitiveNode)
                    and node.prim_id == OPEN_VI_REFERENCE_PRIM_ID
                ):
                    continue
                try:
                    edges.add((key, self.opened_vi(node).key))
                except VIReferenceUnresolved:
                    continue
        return sorted(edges)

    def _opened_by(
        self, path_term: Terminal, node: AnyGraphNode, source: WireEnd
    ) -> ReferencedVI:
        if isinstance(node, PrimitiveNode) and is_on_vi_reference(node):
            code = _read_property_code(node, source.terminal_id)
            reference = _input_at(node, _PROPERTY_REFERENCE_INDEX)
            if code in (VI_NAME_PROPERTY_CODE, VI_PATH_PROPERTY_CODE):
                if reference is not None and self.wire_origins(reference.id):
                    return self.referenced_vi(reference.id)
                # An unwired reference is the VI holding the node (NI, Invoke
                # Node of the VI class: the default VI object).
                own = self.terminal_vi(source.terminal_id)
                if own is not None:
                    return ReferencedVI(self.vi_display_name(own), own)
        try:
            text = self.origin_text(node, source)
        except PathNotConstant:
            raise VIReferenceComputed(
                f"The VI path is wired from {_describe(node)}, so "
                "which VI it opens is only known when the diagram runs."
            ) from None
        if isinstance(node, ConstantNode) and not _is_path(node):
            return self._loaded_by_name(text)
        return self._loaded_by_path(text, path_term.id)

    def owner_files(self) -> dict[str, str]:
        """The file (library or class) that owns the VIs named ``<owner>:<VI>``,
        for each owner among the loaded VIs: the owner's full delimited name
        (``Lib.lvlib:Cls.lvclass``) and the key of its file. A name that a diagram
        computes when it runs is split at its last ``:`` into one of these owners
        and a VI file name."""
        owners: dict[str, str] = {}
        for key in sorted(self._vi_nodes):
            display = self.vi_display_name(key)
            owner_name, colon, _ = display.rpartition(":")
            if not colon or self.is_stub(key) or self.node_type(key) != NodeType.VI:
                continue
            owner_key = self._owner_key(key, NodeType.LIBRARY)
            if owner_key is not None and self._owner_key(key, NodeType.CLASS) is None:
                owners[owner_name] = owner_key
        return owners

    def unowned_vi_files(self) -> dict[str, str]:
        """The file of each loaded VI that no library or class owns, by its name
        (``Name.vi``): a name that a diagram computes when it runs and that has no
        ``<owner>:`` is one of these. A name two loaded files share is left out
        (it names no single VI)."""
        found: dict[str, list[str]] = {}
        for key in sorted(self._vi_nodes):
            display = self.vi_display_name(key)
            if ":" in display or self.is_stub(key):
                continue
            if self.node_type(key) == NodeType.VI:
                found.setdefault(display, []).append(key)
        return {name: keys[0] for name, keys in found.items() if len(keys) == 1}

    def class_members(self) -> list[tuple[str, str, str]]:
        """``(qualified name, member key, class key)`` of each loaded VI a class
        lists: the VIs that are methods of their class's module, found by the
        name or path a diagram computes when it runs."""
        members = []
        for key in sorted(self._vi_nodes):
            class_key = self._owner_key(key, NodeType.CLASS)
            if class_key is not None and not self.is_stub(key):
                members.append((self.vi_display_name(key), key, class_key))
        return members

    def _open_options(self, node: PrimitiveNode) -> int | None:
        options = _input_at(node, OPEN_OPTIONS_INDEX)
        if options is None or not self.incoming_edges(options.id):
            return 0
        return self.constant_int(options.id)

    def _loaded_by_name(self, name: str) -> ReferencedVI:
        """The loaded VI whose full qualified name is ``name``."""
        keys = sorted(
            key for key in self._vi_nodes if self.vi_display_name(key) == name
        )
        if not keys:
            raise VIReferenceUnresolved(
                f"The VI named {name!r} is not loaded with this VI."
            )
        if len(keys) > 1:
            raise VIReferenceUnresolved(
                f"The name {name!r} matches {len(keys)} loaded VIs."
            )
        return ReferencedVI(name, keys[0])

    def _loaded_by_path(self, text: str, terminal_id: str) -> ReferencedVI:
        """The loaded VI at ``text``, a path read relative to the VI holding the
        node when it is not absolute."""
        caller = self.terminal_vi(terminal_id)
        base = Path(caller).parent if caller else Path()
        wanted = os.path.normpath(base / text)
        for key in sorted(self._vi_nodes):
            if os.path.normpath(key) == wanted:
                return ReferencedVI(self.vi_display_name(key), key)
        raise VIReferenceUnresolved(
            f"The VI at {self.vi_label(wanted)!r} is not loaded with this VI."
        )


def is_this_vi(node: AnyGraphNode) -> bool:
    """Whether ``node`` is the "This VI" constant: a reference to the VI holding
    the diagram, a VI Server reference of class VI that names no front-panel
    control."""
    return (
        isinstance(node, LocalVariableNode)
        and not node.is_write
        and node.control_terminal_id is None
        and any(
            t.lv_type and t.lv_type.server_class is ServerClass.VI
            for t in node.terminals
        )
    )


def is_this_application(node: AnyGraphNode) -> bool:
    """Whether ``node`` is the "This Application" constant: a reference to the
    running application, a VI Server reference of class Application that names no
    front-panel control."""
    return (
        isinstance(node, LocalVariableNode)
        and not node.is_write
        and node.control_terminal_id is None
        and any(
            t.lv_type and t.lv_type.server_class is ServerClass.APPLICATION
            for t in node.terminals
        )
    )


def _same_vi(found: list[ReferencedVI]) -> ReferencedVI:
    """The one VI every entry of ``found`` holds (several come from the frames
    of a case structure)."""
    first = found[0]
    if any(other.key != first.key for other in found[1:]):
        raise VIReferenceUnresolved(
            "The frames of a case structure give different VIs, so which VI it "
            "holds is only known when the diagram runs."
        )
    options = {other.options for other in found}
    return ReferencedVI(
        first.name, first.key, options.pop() if len(options) == 1 else None
    )


def _is_vi_reference(const: ConstantNode) -> bool:
    return const.lv_type is not None and const.lv_type.underlying_type == "VIRefnum"


def _is_front_panel_window_output(node: AnyGraphNode, source: WireEnd) -> bool:
    """Whether ``source`` is the reference out of an FP.Open or FP.Close Invoke
    Node of the VI class: the reference it was given (the default VI object, the
    VI holding the node, when none is wired)."""
    return (
        isinstance(node, PrimitiveNode)
        and node.object_name == VI_CLASS
        and node.method_code in (VI_FP_OPEN_METHOD_CODE, VI_FP_CLOSE_METHOD_CODE)
        and any(
            t.id == source.terminal_id and t.index == _REFERENCE_OUT_INDEX
            for t in node.terminals
        )
    )


def _is_open_vi_reference_output(node: AnyGraphNode, source: WireEnd) -> bool:
    return (
        isinstance(node, PrimitiveNode)
        and node.prim_id == OPEN_VI_REFERENCE_PRIM_ID
        and source.index == OPEN_VI_REFERENCE_INDEX
    )


def _input_at(node: AnyGraphNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def is_on_vi_reference(node: PrimitiveNode) -> bool:
    """Whether ``node``, a Property Node, is on a VI reference: a node whose heap
    class is ``VI``, or whose reference input is a VI Server reference of class VI
    (``ServerClass.VI``), as the input of a strict VI reference is (the node's
    class is then ``VI (strict)`` or ``VI Interface Type N``)."""
    if node.object_name == VI_CLASS:
        return True
    reference = _input_at(node, _PROPERTY_REFERENCE_INDEX)
    return (
        reference is not None
        and reference.lv_type is not None
        and reference.lv_type.server_class is ServerClass.VI
    )


def _describe(node: AnyGraphNode) -> str:
    """``node`` as a LabVIEW user names it."""
    if node.kind == "constant":
        return "a constant"
    if node.kind == "vi" and node.id == node.vi_path:
        return f"the control {node.name!r}" if node.name else "a control"
    return f"{node.name or node.node_type or 'a node'}"


def _read_property_code(node: PrimitiveNode, terminal_id: str) -> int | None:
    """The PropItemCode of the single-step property a Property Node reads at
    output ``terminal_id``."""
    for prop, term in correlate_property_terminals(
        node.properties, node.terminals, node.property_value_terminal_ids
    ):
        if term is not None and term.id == terminal_id and len(prop.segments) == 1:
            return prop.segments[0].code
    return None


def _is_path(const: ConstantNode) -> bool:
    return const.lv_type is not None and const.lv_type.underlying_type == "Path"
