"""How a class's private data fields and property accessors are spelled in Python.

One rule, applied to each property folder of the class file:

- every accessor in the folder only reads or writes the one private data field
  (``accessor_field.accessed_field``), and none of them overrides or is
  overridden by an accessor of another loaded class: the field itself is the
  attribute, named by the property and spelled by the accessor's scope (``count``
  for a public one, ``_count`` otherwise); the accessor VIs write no code and
  their calls read and write the attribute;
- otherwise the accessors are a Python ``@property`` (the read VI) and its setter
  (the write VI), each written from its own diagram, under the property's plain
  name whatever its scope; their calls use ``obj.name``;
- an accessor whose connector pane is not a getter or a setter, or a setter that
  does not hand back the object it changed, is an ordinary method (left out of
  ``ClassAttributes.accessors``).

A field no attribute accessor names is the attribute ``_<field>`` (the storage of
a property too). A method is spelled ``name`` when public and ``_name`` otherwise.
This module is the one place that names a class's attributes; ``__init__``,
Bundle/Unbundle By Name and the calls of accessors all read the names from here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole, value_terminals

from .accessor_field import accessed_field, carries_receiver
from .ast_utils import field_attribute_paths, to_var_name

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import ClusterField, Terminal

NON_PUBLIC_PREFIX = "_"


def member_name(base: str, scope: str) -> str:
    """The name a method of ``scope`` has in Python: ``base`` when public,
    ``_<base>`` for a private, protected or community one."""
    return base if scope == "public" else NON_PUBLIC_PREFIX + base


# Scopes from the widest; a scope this does not list is as narrow as private.
_SCOPES = ("public", "protected", "private")


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
    data field the class declares (not its ancestors'); ``accessors`` the VIs of
    its property folders by key; ``names`` every attribute and property name of
    the class and its ancestors."""

    fields: list[str] = field(default_factory=list)
    accessors: dict[str, Accessor] = field(default_factory=dict)
    names: frozenset[str] = frozenset()


@dataclass
class _Member:
    key: str
    scope: str
    role: PropertyRole
    field_index: int | None = None  # among the class's own fields
    in_override_chain: bool = False


def _fits(
    role: PropertyRole | None,
    inputs: list[Terminal],
    outputs: list[Terminal],
    is_class: Callable[[Terminal], bool],
) -> bool:
    """Whether a pane is a getter's or a setter's: it takes the class wire, and
    its one value goes out (getter) or in (setter) with no other value beside it."""
    if role is None:
        return False
    if not any(is_class(t) for t in inputs):
        return False
    if role is PropertyRole.GETTER:
        return True
    return not value_terminals(outputs, is_class)


def in_override_chain(graph: InMemoryVIGraph, class_key: str, vi_key: str) -> bool:
    """Whether the VI ``vi_key`` of the class at ``class_key`` overrides a VI of an
    ancestor or is overridden by one of a loaded descendant: the VI of the same
    file name."""
    name = Path(vi_key).name
    parent_key = graph.class_parent_key(class_key)
    if parent_key is not None and graph.class_method(parent_key, name) is not None:
        return True
    pending = graph.class_child_keys(class_key)
    seen: set[str] = set()
    while pending:
        child = pending.pop()
        if child in seen:
            continue
        seen.add(child)
        if graph.class_declares(child, name):
            return True
        pending.extend(graph.class_child_keys(child))
    return False


def _hands_back_receiver(
    graph: InMemoryVIGraph,
    inputs: list[Terminal],
    outputs: list[Terminal],
    is_class: Callable[[Terminal], bool],
) -> bool:
    """Whether an accessor's class output is the object it was given, changed in
    place: only then can ``obj.name`` and ``obj.name = value`` stand for the call."""
    class_in = next((t for t in inputs if is_class(t)), None)
    class_out = next((t for t in outputs if is_class(t)), None)
    if class_in is None or class_out is None:
        return class_in is not None
    return carries_receiver(graph, class_out.id, class_in.id, lambda node: True)


def _members(graph: InMemoryVIGraph, class_key: str) -> dict[str, list[_Member]]:
    """The VIs of each property folder of the class, by property name, in the
    order the class file lists them. A VI whose pane is not a getter or a setter,
    or a second getter or setter of one property, is left out."""
    groups: dict[str, list[_Member]] = {}
    for key in graph.class_member_keys(class_key):
        access = graph.get_method_access(key)
        if access is None or not access.is_accessor or access.accessor_field is None:
            continue
        role = PropertyRole(access.accessor_type) if access.accessor_type else None
        inputs, _, outputs, _ = graph.vi_interface(key)

        def is_class(t: Terminal, key: str = key) -> bool:
            return graph.is_class_wire(t.lv_type, key, class_key)

        if not _fits(role, inputs, outputs, is_class):
            continue
        assert role is not None
        if not _hands_back_receiver(graph, inputs, outputs, is_class):
            continue
        members = groups.setdefault(access.accessor_field, [])
        if any(m.role is role for m in members):
            continue
        member = _Member(
            key,
            access.scope,
            role,
            in_override_chain=in_override_chain(graph, class_key, key),
        )
        member.field_index = accessed_field(
            inputs, outputs, graph, class_key, role, key
        )
        members.append(member)
    return groups


def class_attributes(
    graph: InMemoryVIGraph,
    class_key: str,
    parent: ClassAttributes | None = None,
) -> ClassAttributes:
    """The attributes of the class at ``class_key``; ``parent`` are those of the
    class it descends from, worked out when not given."""
    if parent is None:
        parent_key = graph.class_parent_key(class_key)
        parent = (
            class_attributes(graph, parent_key) if parent_key else ClassAttributes()
        )
    own_fields = graph.class_own_fields(class_key)
    groups = _members(graph, class_key)

    accessors: dict[str, Accessor] = {}
    taken = set(parent.names)
    attribute_of_field: dict[int, str] = {}
    properties: list[tuple[str, str, list[_Member]]] = []
    for property_name, members in groups.items():
        indices = {m.field_index for m in members}
        index = next(iter(indices)) if len(indices) == 1 else None
        if (
            index is not None
            and index < len(own_fields)
            and index not in attribute_of_field
            and not any(m.in_override_chain for m in members)
        ):
            spelled = member_name(
                to_var_name(property_name),
                min((m.scope for m in members), key=_breadth),
            )
            attribute_of_field[index] = spelled
            for m in members:
                accessors[m.key] = Accessor(
                    m.key, property_name, m.role, AccessorKind.ATTRIBUTE, spelled
                )
        else:
            properties.append((property_name, to_var_name(property_name), members))
    for property_name, plain, members in properties:
        taken.add(plain)
        for m in members:
            accessors[m.key] = Accessor(
                m.key, property_name, m.role, AccessorKind.PROPERTY, plain
            )
    attributes: list[str] = []
    for i, own in enumerate(own_fields):
        base = attribute_of_field.get(i) or NON_PUBLIC_PREFIX + to_var_name(own.name)
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base}_{n}"
        taken.add(candidate)
        attributes.append(candidate)
    return ClassAttributes(
        fields=attributes,
        accessors=accessors,
        names=frozenset(taken),
    )


def class_field_paths(
    attributes: ClassAttributes, fields: list[ClusterField]
) -> list[list[str]]:
    """The attribute path of every field slot of the private data a class
    declares (see ``field_attribute_paths``): a field of the class is the
    attribute the class names it, what is inside a field keeps its names."""
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
    inputs, _, outputs, _ = graph.vi_interface(vi_key)

    def is_class(t: Terminal) -> bool:
        return graph.is_class_wire(t.lv_type, vi_key, class_key)

    def first(terminals: list[Terminal]) -> int | None:
        return terminals[0].index if terminals else None

    return AccessorPane(
        class_in=first([t for t in inputs if is_class(t)]),
        value_in=first(value_terminals(inputs, is_class)),
        class_out=first([t for t in outputs if is_class(t)]),
        value_out=first(value_terminals(outputs, is_class)),
    )
