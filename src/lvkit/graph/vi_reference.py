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

# Property Node on the VI class: the PropItemCode a file records for the Name and
# the Path property (the same codes pair with those names in every Property Node
# of the VI class in the corpus).
VI_CLASS = "VI"
VI_NAME_PROPERTY_CODE = 500
VI_PATH_PROPERTY_CODE = 501

# The Property Node's VI reference input.
_PROPERTY_REFERENCE_INDEX = 0


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
        raise VIReferenceUnresolved(
            f"The VI reference is wired from {_describe(node)}, so which VI it "
            "holds is only known when the diagram runs."
        )

    def holds_vi_reference(self, terminal_id: str) -> bool:
        """True when the wire into ``terminal_id`` starts at a Static VI Reference
        or at an Open VI Reference."""
        origins = self.wire_origins(terminal_id)
        return bool(origins) and all(
            (isinstance(n, ConstantNode) and _is_vi_reference(n))
            or _is_open_vi_reference_output(n, s)
            or is_this_vi(n)
            for n, s in origins
        )

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

    def _opened_by(
        self, path_term: Terminal, node: AnyGraphNode, source: WireEnd
    ) -> ReferencedVI:
        if isinstance(node, PrimitiveNode) and node.object_name == VI_CLASS:
            code = _read_property_code(node, source.terminal_id)
            reference = _input_at(node, _PROPERTY_REFERENCE_INDEX)
            if code in (VI_NAME_PROPERTY_CODE, VI_PATH_PROPERTY_CODE) and reference:
                return self.referenced_vi(reference.id)
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
        raise VIReferenceUnresolved(f"The VI at {text!r} is not loaded with this VI.")


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
