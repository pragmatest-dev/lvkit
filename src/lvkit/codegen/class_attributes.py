"""How a class's private data fields and property accessors are spelled in Python.

One rule, applied to each property folder of the class file:

- every accessor in the folder only reads or writes the one private data field
  (``accessor_field.accessed_field``): the field itself is the attribute, named by
  the property and spelled by the accessor's scope; the accessor VIs write no
  code and their calls read and write the attribute;
- otherwise the accessors are a Python ``@property`` (the read VI) and its setter
  (the write VI), each written from its own diagram; their calls use ``obj.name``;
- an accessor whose connector pane is not a getter or a setter is an ordinary
  method (left out of ``ClassAttributes.accessors``).

A field no property names is the attribute ``_<field>``. This module is the one
place that names a class's attributes; ``__init__``, Bundle/Unbundle By Name and
the calls of accessors all read the names from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole, value_terminals
from lvkit.type_defaults import _is_class_refnum

from .accessor_field import accessed_field
from .ast_utils import field_attribute_paths, to_var_name, unique_names

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import ClusterField, LVType, Terminal

PROTECTED_PREFIX = "_"
PRIVATE_PREFIX = "__"

# Scopes from the widest; a scope this does not list is as narrow as private.
_SCOPES = ("public", "protected", "private")


def scope_prefix(scope: str) -> str:
    """The prefix a method or property of ``scope`` has in Python: none for
    public, ``_`` for protected, ``__`` for private (and community)."""
    if scope == "public":
        return ""
    if scope == "protected":
        return PROTECTED_PREFIX
    return PRIVATE_PREFIX


def _breadth(scope: str) -> int:
    return _SCOPES.index(scope) if scope in _SCOPES else len(_SCOPES)


class AccessorKind(str, Enum):
    """What an accessor VI of a property folder is in the generated class (an
    accessor whose pane fits neither is an ordinary method and is not listed)."""

    ATTRIBUTE = "attribute"
    PROPERTY = "property"


@dataclass(frozen=True)
class Accessor:
    """One VI of a property folder: its role and how it is written;
    ``python_name`` is the attribute or the property."""

    key: str
    property_name: str
    role: PropertyRole | None
    kind: AccessorKind
    python_name: str


@dataclass(frozen=True)
class ClassAttributes:
    """The attributes of one class: ``fields`` holds the attribute of each private
    data field, inherited fields first; ``accessors`` the VIs of its property
    folders by key; ``names`` every attribute and property name of the class and
    its ancestors."""

    fields: list[str] = field(default_factory=list)
    inherited: int = 0
    accessors: dict[str, Accessor] = field(default_factory=dict)
    names: frozenset[str] = frozenset()

    @property
    def own_fields(self) -> list[str]:
        """The attributes of the fields the class declares itself."""
        return self.fields[self.inherited :]


def unclaimed_field_attributes(field_names: list[str]) -> list[str]:
    """The attribute ``_<field>`` of each private data field no property names;
    fields whose labels become one Python name each get their own."""
    return ["_" + name for name in unique_names([to_var_name(n) for n in field_names])]


@dataclass
class _Member:
    key: str
    scope: str
    role: PropertyRole | None
    field_index: int | None = None  # among the class's own fields


def _fits(
    role: PropertyRole | None,
    inputs: list[Terminal],
    outputs: list[Terminal],
    class_name: str,
) -> bool:
    """Whether a pane is a getter's or a setter's: it takes the class wire, and
    its one value goes out (getter) or in (setter) with no other value beside it."""
    if role is None:
        return False
    if not any(t.lv_type and _is_class_refnum(t.lv_type, class_name) for t in inputs):
        return False
    if role is PropertyRole.GETTER:
        return True
    return not value_terminals(outputs, class_name)


def _members(
    graph: InMemoryVIGraph, class_key: str, inherited: int
) -> dict[str, list[_Member]]:
    """The VIs of each property folder of the class, by property name, in the
    order the class file lists them. A VI whose pane is not a getter or a setter,
    or a second getter or setter of one property, is left out."""
    class_name = graph.vi_display_name(class_key)
    groups: dict[str, list[_Member]] = {}
    for key in graph.class_member_keys(class_key):
        access = graph.get_method_access(key)
        if access is None or not access.is_accessor or access.accessor_field is None:
            continue
        role = PropertyRole(access.accessor_type) if access.accessor_type else None
        inputs, _, outputs, _ = graph.vi_interface(key)
        if not _fits(role, inputs, outputs, class_name):
            continue
        assert role is not None
        members = groups.setdefault(access.accessor_field, [])
        if any(m.role is role for m in members):
            continue
        member = _Member(key, access.scope, role)
        ordinal = accessed_field(inputs, outputs, graph, class_name, role, key)
        if ordinal is not None and ordinal >= inherited:
            member.field_index = ordinal - inherited
        members.append(member)
    return groups


def class_attributes(graph: InMemoryVIGraph, class_key: str) -> ClassAttributes:
    """The attributes of the class at ``class_key``."""
    parent_key = graph.class_parent_key(class_key)
    parent = class_attributes(graph, parent_key) if parent_key else ClassAttributes()
    inherited = len(parent.fields)
    own_fields = graph.class_own_fields(class_key)
    groups = _members(graph, class_key, inherited)

    accessors: dict[str, Accessor] = {}
    taken = set(parent.names)
    attribute_of_field: dict[int, str] = {}
    properties: list[tuple[str, str, list[_Member]]] = []
    for property_name, members in groups.items():
        spelled = _spelled(property_name, [m.scope for m in members])
        indices = {m.field_index for m in members}
        index = next(iter(indices)) if len(indices) == 1 else None
        if index is not None and index < len(own_fields) and (
            index not in attribute_of_field
        ):
            attribute_of_field[index] = spelled
            for m in members:
                accessors[m.key] = Accessor(
                    m.key, property_name, m.role, AccessorKind.ATTRIBUTE, spelled
                )
        else:
            properties.append((property_name, spelled, members))
    for property_name, spelled, members in properties:
        taken.add(spelled)
        for m in members:
            accessors[m.key] = Accessor(
                m.key, property_name, m.role, AccessorKind.PROPERTY, spelled
            )
    attributes: list[str] = []
    for i, own in enumerate(own_fields):
        base = attribute_of_field.get(i) or "_" + to_var_name(own.name)
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base}_{n}"
        taken.add(candidate)
        attributes.append(candidate)
    return ClassAttributes(
        fields=[*parent.fields, *attributes],
        inherited=inherited,
        accessors=accessors,
        names=frozenset(taken),
    )


def type_attributes(
    graph: InMemoryVIGraph, lv_type: LVType | None, caller_vi_key: str | None
) -> ClassAttributes | None:
    """The attributes of the class a wire of type ``lv_type`` carries, found among
    the calling VI's own links; ``None`` for a wire that is not a loaded class."""
    if lv_type is None:
        return None
    key = graph.class_type_key(lv_type, caller_vi_key)
    return class_attributes(graph, key) if key is not None else None


def _spelled(property_name: str, scopes: list[str]) -> str:
    """A property's name in Python: its long name behind the prefix of the widest
    scope of its accessors, so every VI that may call one may reach the name."""
    return scope_prefix(min(scopes, key=_breadth)) + to_var_name(property_name)


def class_field_paths(
    attributes: ClassAttributes, fields: list[ClusterField]
) -> list[list[str]]:
    """The attribute path of every field slot of a class's private data (see
    ``field_attribute_paths``): a field of the class is the attribute the class
    names it, what is inside a field keeps its names."""
    paths = field_attribute_paths(fields)
    result: list[list[str]] = []
    top = -1
    for path in paths:
        if len(path) == 1:
            top += 1
        result.append([attributes.fields[top], *path[1:]])
    return result


@dataclass(frozen=True)
class AccessorPane:
    """The connector pane positions of an accessor VI's class wire and value."""

    class_in: int | None
    value_in: int | None
    class_out: int | None
    value_out: int | None


def accessor_pane(graph: InMemoryVIGraph, class_key: str, vi_key: str) -> AccessorPane:
    """Where on the pane of the accessor VI ``vi_key`` of the class at
    ``class_key`` its class wire and its value are, so a call finds the terminals
    of the wire and of the value."""
    class_name = graph.vi_display_name(class_key)
    inputs, _, outputs, _ = graph.vi_interface(vi_key)

    def first(terminals: list[Terminal]) -> int | None:
        return terminals[0].index if terminals else None

    def is_class(t: Terminal) -> bool:
        return t.lv_type is not None and _is_class_refnum(t.lv_type, class_name)

    return AccessorPane(
        class_in=first([t for t in inputs if is_class(t)]),
        value_in=first(value_terminals(inputs, class_name)),
        class_out=first([t for t in outputs if is_class(t)]),
        value_out=first(value_terminals(outputs, class_name)),
    )


def accessor_of(
    graph: InMemoryVIGraph, vi_key: str
) -> tuple[Accessor, AccessorPane] | None:
    """The accessor the VI ``vi_key`` is in its class's property folders and its
    pane, or None for a VI no property folder lists."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None:
        return None
    accessor = class_attributes(graph, class_key).accessors.get(vi_key)
    if accessor is None:
        return None
    return accessor, accessor_pane(graph, class_key, vi_key)
