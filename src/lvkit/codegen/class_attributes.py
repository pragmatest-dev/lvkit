"""How a class's private data fields, properties and methods are spelled in Python.

- A private data field belongs to its class alone: it is stored as
  ``self.__<field>`` and read and written only inside the class's own methods.
  Python writes such a name with the class's own name in front (``self._C__count``
  in the class ``C``), so a parent's and a child's field of one name are two
  values.
- The VIs of a property folder are a Python property named by the property's long
  name: the read VI its getter, the write VI its setter, each written from its own
  diagram (``class_builder/properties.py``). A child's property of the long name
  of an ancestor's has the same spelling, so it overrides it.
- Any other member VI is a method named by its file.

Access levels belong to the VIs: a name is ``name`` for a public VI, ``_name`` for
a protected or community one and ``__name`` for a private one; a property takes
the widest level of its read and write VIs. An accessor whose connector pane is
not a getter or a setter, or a setter that does not hand back the object it
changed, is an ordinary method.

This module is the one place that names a class's members and fields;
``__init__``, Bundle/Unbundle By Name, In Place Element and the calls of methods
and accessors all read the names from here."""

from __future__ import annotations

import keyword
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole, value_terminals

from .ast_utils import (
    field_attribute_names,
    field_attribute_paths,
    to_function_name,
    to_var_name,
)
from .receiver_flow import carries_receiver

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import ClusterField, Terminal

_PROTECTED_PREFIX = "_"
_PRIVATE_PREFIX = "__"

# How wide each access level is, from the widest; a level this does not list is
# as narrow as private.
_BREADTH = {"public": 0, "protected": 1, "community": 1, "private": 2}
_NARROWEST = 2


def _breadth(scope: str) -> int:
    return _BREADTH.get(scope, _NARROWEST)

# What a field's storage takes after its name when a private member of the class
# has the name the storage would have.
_STORAGE_SUFFIX = "_value"


def member_name(base: str, scope: str) -> str:
    """The name a member of access level ``scope`` has in Python: ``base`` when
    public, ``__<base>`` when private, ``_<base>`` otherwise (protected,
    community). The leading underscores of ``base`` are dropped first, so only a
    private name starts with ``__`` (``is_private``), and a private name never
    ends in ``__`` (Python leaves such a name as written)."""
    if scope == "private":
        return _PRIVATE_PREFIX + (base.strip("_") or "var")
    stem = base.lstrip("_") or "var"
    if keyword.iskeyword(stem):
        stem += "_"
    return stem if scope == "public" else _PROTECTED_PREFIX + stem


def is_private(name: str) -> bool:
    """Whether ``name`` (spelled by ``member_name``) is a private member's or a
    field's: Python stores it with the name of the class it is written in."""
    return name.startswith(_PRIVATE_PREFIX) and not name.endswith(_PRIVATE_PREFIX)


def run_time_name(class_name: str, name: str) -> str:
    """The name Python stores the member ``name`` of the class ``class_name``
    under, as ``getattr`` reads it outside the class body."""
    if is_private(name):
        return f"_{class_name.lstrip('_')}{name}"
    return name


def method_name(graph: InMemoryVIGraph, vi_key: str) -> str:
    """The name of the member VI ``vi_key`` as a method: its file name, spelled
    by its access level."""
    access = graph.get_method_access(vi_key)
    scope = access.scope if access is not None else "public"
    return member_name(to_function_name(Path(vi_key).name), scope)


@dataclass(frozen=True)
class Accessor:
    """One VI of a property folder: its role and ``python_name``, the name of the
    property it is the getter or the setter of."""

    key: str
    property_name: str
    role: PropertyRole
    python_name: str


@dataclass(frozen=True)
class ClassAttributes:
    """The Python names of one class: ``fields`` the storage of each private data
    field the class declares (not its ancestors'), in order; ``accessors`` the VIs
    of its property folders that are a property, by key."""

    fields: list[str] = field(default_factory=list)
    accessors: dict[str, Accessor] = field(default_factory=dict)


def storage_names(fields: list[ClusterField], taken: Collection[str]) -> list[str]:
    """The storage ``__<field>`` of each private data field (the names
    ``field_attribute_names`` gives the fields, distinct); a field whose storage
    would have a name in ``taken`` (a private member of the class) is
    ``__<field>_value``."""
    names: list[str] = []
    used = set(taken)
    for base in field_attribute_names(f.name for f in fields):
        candidate = member_name(base, "private")
        while candidate in used:
            candidate += _STORAGE_SUFFIX
        used.add(candidate)
        names.append(candidate)
    return names


@dataclass
class _Member:
    key: str
    scope: str
    role: PropertyRole


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
        members.append(_Member(key, access.scope, role))
    return groups


def property_accessors(graph: InMemoryVIGraph, class_key: str) -> dict[str, Accessor]:
    """The accessor VIs of the class at ``class_key`` that are a property, by key:
    each named by its property's long name, spelled by the widest access level of
    the property's read and write VIs."""
    accessors: dict[str, Accessor] = {}
    for property_name, members in _members(graph, class_key).items():
        widest = min((m.scope for m in members), key=_breadth)
        python_name = member_name(to_var_name(property_name), widest)
        for m in members:
            accessors[m.key] = Accessor(m.key, property_name, m.role, python_name)
    return accessors


def class_field_paths(
    attributes: ClassAttributes, fields: list[ClusterField]
) -> list[list[str]]:
    """The attribute path of every field slot of the private data a class
    declares (see ``field_attribute_paths``): a field of the class is its
    storage, what is inside a field keeps its names."""
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
