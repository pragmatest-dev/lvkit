"""A ``.ctl`` typedef as a graph read model.

``load_typedef`` (``loading.py``) registers each control as a path-keyed
``typedef`` node; this module is what every view reads it through:
``get_typedef`` -> :class:`TypedefInfo` (identity, root type, fields with their
front-panel defaults, what it uses / is owned by / is used by),
``list_typedefs`` and ``get_typedef_front_panel``. ``load_ctl_by_path``
(``load_ctl.py``) is the single-control counterpart of ``load_vi_by_path``.

``used_by`` is whatever loaded VIs / classes / typedefs point at the control in
THIS graph, so it is only complete over a graph that loaded its users -- a
standalone control load has none.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import networkx as nx

from ..models import ClusterField, LVType, LVTypeKind, ScalarValue
from ..parser.models import ParsedFPControl, ParsedFrontPanel
from .node_kinds import OWNERSHIP_RELS, NodeType, node_type_of

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TypedefField:
    """One cluster field of a typedef: its LabVIEW type, the default the
    control's front panel records for it (an enum/ring default is the item
    NAME), and -- for a nested cluster, or an array of clusters -- the element's
    own ``fields``. An array field's saved values are ``elements`` (empty when
    it has none, or when its elements are clusters, whose values ride on
    ``fields``)."""

    name: str
    lv_type: LVType | None
    default: ScalarValue
    fields: tuple[TypedefField, ...]
    elements: tuple[ScalarValue, ...] = ()


class FieldWalk(NamedTuple):
    """One field met by :func:`walk_typedef_fields`: its ``seq`` in the whole tree
    (depth-first order), its containing field's ``parent_seq`` (None at the top),
    and its ``depth`` below the starting level."""

    field: TypedefField
    seq: int
    parent_seq: int | None
    depth: int


def walk_typedef_fields(
    fields: tuple[TypedefField, ...], depth: int = 0
) -> Iterator[FieldWalk]:
    """Every field of ``fields`` and of the clusters nested in them, depth-first."""
    seq = 0

    def visit(
        level: tuple[TypedefField, ...], parent: int | None, d: int
    ) -> Iterator[FieldWalk]:
        nonlocal seq
        for f in level:
            here = seq
            seq += 1
            yield FieldWalk(f, here, parent, d)
            yield from visit(f.fields, here, d + 1)

    yield from visit(fields, None, depth)


@dataclass(frozen=True)
class TypedefRef:
    """A file this typedef uses, is owned by, or is used by. ``node_type`` is
    the kind of node it is in the graph (a VI, a class, a library, a typedef);
    ``path`` is None for a stub (a file that isn't on disk) -- never a guessed
    path."""

    node_type: NodeType
    qualified: str
    path: str | None


@dataclass(frozen=True)
class TypedefInfo:
    """One loaded ``.ctl`` typedef. ``root_type`` is the control's own type -- a
    cluster (``fields``), an enum/ring (``values``), a scalar, ... -- and
    ``default`` its own default (empty for a cluster, whose defaults ride on
    ``fields``)."""

    key: str
    name: str
    root_type: LVType
    default: ScalarValue
    fields: tuple[TypedefField, ...]
    uses: tuple[TypedefRef, ...]
    owned_by: tuple[TypedefRef, ...]
    used_by: tuple[TypedefRef, ...]
    elements: tuple[ScalarValue, ...] = ()


def _plain_quote(text: str) -> str:
    return f'"{text}"'


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _value_literal(
    value: str,
    lv_type: LVType | None,
    quote: Callable[[str], str],
    word: Callable[[str], str],
) -> str:
    """One recorded value as text: a ``String`` value through ``quote``; a number
    bare; anything else (an enum / ring item name, a path) through ``word``."""
    if lv_type is not None and lv_type.underlying_type == "String":
        return quote(value)
    return value if _is_number(value) else word(value)


def default_literal(
    default: ScalarValue,
    lv_type: LVType | None,
    elements: tuple[ScalarValue, ...] = (),
    quote: Callable[[str], str] = _plain_quote,
    word: Callable[[str], str] = str,
) -> str | None:
    """A recorded default as text: saved ``elements`` as ``[a, b]`` (each by the
    element type's rule); a ``String`` value through ``quote``; a number bare; any
    other value -- an enum / ring item name -- through ``word``. None when there is
    none. The one rule every surface shares; each supplies its own ``quote`` /
    ``word`` (lvnet escapes both; ``describe`` and the docs page do not) and its own
    prefix. NOTE it renders an enum item BARE where a VI terminal's default quotes
    any string: a control's default is an item of its own type, not a string value.
    """
    if elements:
        element_type = lv_type.element_type if lv_type is not None else None
        return (
            "["
            + ", ".join(
                _value_literal(str(e), element_type, quote, word) for e in elements
            )
            + "]"
        )
    if default is None:
        return None
    return _value_literal(str(default), lv_type, quote, word)


def field_default_literal(
    f: TypedefField,
    quote: Callable[[str], str] = _plain_quote,
    word: Callable[[str], str] = str,
) -> str | None:
    """A field's recorded default as text (see :func:`default_literal`)."""
    return default_literal(f.default, f.lv_type, f.elements, quote, word)


def _panel_item(ctrl: ParsedFPControl, raw: ScalarValue) -> str | None:
    """The item a control's own recorded items (``enum_values``, in dropdown
    order) name for the ordinal ``raw``: for a ring/enum whose items only the front
    panel knows (its type-map type is a plain integer)."""
    try:
        index = int(str(raw))
    except ValueError:
        return None
    return ctrl.enum_values[index] if 0 <= index < len(ctrl.enum_values) else None


def _default_of(ctrl: ParsedFPControl | None, lv_type: LVType | None) -> ScalarValue:
    """``ctrl``'s recorded default, with an enum/ring ordinal shown as its item
    name. An ordinal that is no item of the type is kept as recorded and
    logged -- the control's data is inconsistent, but the read still works."""
    if ctrl is None or ctrl.default_value is None:
        return None
    if lv_type is not None and lv_type.kind == LVTypeKind.CLUSTER:
        return None  # a cluster's defaults are its fields'
    if lv_type is not None and lv_type.values:
        item = lv_type.item_for(ctrl.default_value)
        if item is None:
            logger.warning(
                "typedef default %r is not an item of %s",
                ctrl.default_value,
                ctrl.name,
            )
            return ctrl.default_value
        return item
    return _panel_item(ctrl, ctrl.default_value) or ctrl.default_value


def _element_type(lv_type: LVType | None) -> LVType | None:
    """An array's element type; the type itself for anything else."""
    if lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        return lv_type.element_type
    return lv_type


def _elements_of(
    ctrl: ParsedFPControl | None, lv_type: LVType | None
) -> tuple[ScalarValue, ...]:
    """An array control's saved element values, an enum/ring element shown as its
    item name. Empty when there are none or the elements are clusters."""
    if ctrl is None or lv_type is None or lv_type.kind != LVTypeKind.ARRAY:
        return ()
    if not all(isinstance(v, str) for v in ctrl.element_values):
        return ()
    element = lv_type.element_type
    return tuple(
        (element.item_for(v) if element is not None and element.values else None) or v
        for v in ctrl.element_values
        if isinstance(v, str)
    )


def _typedef_fields(
    fields: list[ClusterField] | None, controls: list[ParsedFPControl]
) -> tuple[TypedefField, ...]:
    """``fields`` with each field's front-panel default, matched to its control
    BY LABEL (cluster fields are identified by label, never caption)."""
    by_name = {c.name: c for c in controls}
    result = []
    for f in fields or []:
        ctrl = by_name.get(f.name)
        inner = _element_type(f.type)
        nested = _typedef_fields(
            inner.fields if inner is not None else None,
            ctrl.children if ctrl is not None else [],
        )
        result.append(
            TypedefField(
                f.name,
                f.type,
                _default_of(ctrl, f.type),
                nested,
                _elements_of(ctrl, f.type),
            )
        )
    return tuple(result)


def _sorted_refs(refs: list[TypedefRef]) -> tuple[TypedefRef, ...]:
    return tuple(sorted(refs, key=lambda r: (r.qualified, r.path or "")))


class TypedefMixin:
    """The typedef queries of ``InMemoryVIGraph``."""

    # Defined on InMemoryVIGraph in core.py.
    _dep_graph: nx.DiGraph

    if TYPE_CHECKING:
        # Resolved via MRO from QueryMixin.
        def is_stub(self, key: str) -> bool: ...
        def _owner_key(self, key: str, owner_type: NodeType) -> str | None: ...

    def _typedef_node(self, key: str) -> Mapping[str, Any]:
        """The dep-graph node data of the loaded (non-stub) typedef ``key``;
        ValueError when ``key`` isn't one."""
        if not self.is_typedef(key) or self.is_stub(key):
            raise ValueError(f"Not a loaded typedef: {key}")
        return self._dep_graph.nodes[key]

    def typedef_name(self, key: str) -> str:
        """The name of the typedef at path ``key`` -- loaded or a stub: its file
        name, qualified by the library that owns it (the same whichever way it was
        loaded). A class's private-data control is named by its file alone; its
        class is in ``owned_by``. ValueError when ``key`` isn't a typedef."""
        if not self.is_typedef(key):
            raise ValueError(f"Not a typedef: {key}")
        leaf = Path(key).name
        library = self._owner_key(key, NodeType.LIBRARY)
        if library is None:
            return leaf
        return f"{self._dep_graph.nodes[library].get('qname', library)}:{leaf}"

    def _typedef_ref(self, other: str) -> TypedefRef:
        data = self._dep_graph.nodes[other]
        return TypedefRef(
            node_type_of(data),
            data.get("qname") or Path(other).name,
            None if self.is_stub(other) else other,
        )

    def is_typedef(self, key: str) -> bool:
        """True when ``key`` is a typedef node, loaded or a stub."""
        return (
            key in self._dep_graph
            and self._dep_graph.nodes[key].get("node_type") == NodeType.TYPEDEF
        )

    def list_typedefs(self) -> list[str]:
        """The path keys of every loaded (non-stub) typedef, ordered by display
        name then path."""
        keys = [
            n
            for n in self._dep_graph.nodes
            if self.is_typedef(n) and not self.is_stub(n)
        ]
        return sorted(keys, key=lambda k: (self.typedef_name(k), k))

    def get_typedef(self, key: str) -> TypedefInfo:
        """The loaded typedef at path ``key``. Raises ValueError when it isn't a
        loaded typedef (unlike ``get_class_hierarchy``, which returns None for a
        miss: a caller holding a path key asked for a typedef that must be there),
        or when a hand-built node has no root type (``load_typedef`` never stores
        one)."""
        node = self._typedef_node(key)
        root = node.get("root_type")
        if root is None:
            raise ValueError(f"Typedef has no resolvable root type: {key}")
        panel = node.get("front_panel")
        controls = panel.controls if panel is not None else []
        root_ctrl = controls[0] if controls else None

        # A control that points at itself is neither its own user nor its own
        # dependency.
        preds = [p for p in self._dep_graph.predecessors(key) if p != key]
        succs = [s for s in self._dep_graph.successors(key) if s != key]
        owners = [
            p
            for p in preds
            if (self._dep_graph.get_edge_data(p, key) or {}).get("rel")
            in OWNERSHIP_RELS
        ]
        return TypedefInfo(
            key=key,
            name=self.typedef_name(key),
            root_type=root,
            default=_default_of(root_ctrl, root),
            elements=_elements_of(root_ctrl, root),
            fields=_typedef_fields(
                node.get("fields"), root_ctrl.children if root_ctrl else []
            ),
            uses=_sorted_refs([self._typedef_ref(s) for s in succs]),
            owned_by=_sorted_refs([self._typedef_ref(p) for p in owners]),
            used_by=_sorted_refs(
                [self._typedef_ref(p) for p in preds if p not in owners]
            ),
        )

    def get_typedef_front_panel(self, key: str) -> ParsedFrontPanel | None:
        """The typedef's own front-panel layout, or None when it carries none.
        ValueError when ``key`` isn't a loaded typedef."""
        return self._typedef_node(key).get("front_panel")
