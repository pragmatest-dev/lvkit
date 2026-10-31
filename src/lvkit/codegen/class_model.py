"""The model of the classes a run generates: for each LabVIEW class, the Python
names of its attributes (``class_attributes``) and the shape of each method.

A class is one Python class in one module. Its member VIs are its methods:

* a dynamic-dispatch VI, and a static VI that takes the class wire, is an
  instance method (the class input is ``self``);
* a static VI with no class input is a ``@staticmethod``.

A method's ``MethodShape`` is what the definition and every call read, so the two
agree: its parameters (by keyword), what it returns (nothing, the one result, or
a ``NamedTuple`` of two or more) and whether its class output is a result. A
dynamic-dispatch call names the declaration while the object may be a descendant,
so the shape is computed over the declaration and every loaded override.

One model per graph and class is cached for the run (the graph is not changed
while a run generates)."""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import GraphNode, VINode
from lvkit.type_names import derive_python_name

from .accessor_field import carries_receiver
from .ast_utils import (
    build_result_class_name,
    interface_names,
    to_function_name,
)
from .class_attributes import (
    Accessor,
    AccessorKind,
    AccessorPane,
    ClassAttributes,
    accessor_pane,
    class_attributes,
    member_name,
)
from .value_copy import holds_refnum

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import LVType, Terminal

    from .import_resolver import ImportResolver, NameImport


class MethodKind(str, Enum):
    """How a member VI is called in Python."""

    DYNAMIC = "dynamic"  # dynamic dispatch: ``receiver.method(...)``
    INSTANCE = "instance"  # static, takes the class wire: ``receiver.method(...)``
    STATIC = "static"  # takes none: ``Class.method(...)``


@dataclass(frozen=True)
class MethodShape:
    """One member VI as a Python method.

    ``parameters`` maps the pane index of each input other than the class wire
    and the error cluster to its keyword; ``result_fields`` maps the pane index of
    each result to its field of the ``NamedTuple`` ``result_class`` (named only
    when there are two or more results). ``results`` are the pane indices
    returned, in pane order, and include the class output when
    ``returns_receiver``: the class output is not the object the method was
    called on, changed in place, in this VI or in a VI that overrides it."""

    key: str
    class_key: str
    kind: MethodKind
    name: str
    receiver_in: int | None
    receiver_out: int | None
    parameters: dict[int, str]
    results: tuple[int, ...]
    result_fields: dict[int, str]
    returns_receiver: bool
    result_class: str | None
    root_key: str
    root_class_key: str


@dataclass
class ClassModel:
    """The Python names and methods of one class."""

    key: str
    name: str
    attributes: ClassAttributes
    shapes: dict[str, MethodShape] = field(default_factory=dict)
    # A member VI whose Python name is also an attribute or property, or the name
    # of another member of the class: the reason it cannot be a method.
    clashes: dict[str, str] = field(default_factory=dict)


@dataclass
class _RunState:
    models: dict[str, ClassModel] = field(default_factory=dict)
    returns: dict[str, bool] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)
    cyclic_classes: set[str] | None = None


_STATES: weakref.WeakKeyDictionary[InMemoryVIGraph, _RunState] = (
    weakref.WeakKeyDictionary()
)


def _state(graph: InMemoryVIGraph) -> _RunState:
    state = _STATES.get(graph)
    if state is None:
        state = _STATES[graph] = _RunState()
    return state


def class_model(graph: InMemoryVIGraph, class_key: str) -> ClassModel:
    """The model of the class at ``class_key``."""
    state = _state(graph)
    model = state.models.get(class_key)
    if model is None:
        model = _build_model(graph, class_key)
        state.models[class_key] = model
    return model


def type_attributes(
    graph: InMemoryVIGraph, lv_type: LVType | None, caller_vi_key: str | None
) -> ClassAttributes | None:
    """The attributes of the class a wire of type ``lv_type`` carries, found among
    the calling VI's own links; ``None`` for a wire that is not a loaded class."""
    if lv_type is None:
        return None
    key = graph.class_type_key(lv_type, caller_vi_key)
    return class_model(graph, key).attributes if key is not None else None


def accessor_of(
    graph: InMemoryVIGraph, vi_key: str
) -> tuple[Accessor, AccessorPane] | None:
    """The accessor the VI ``vi_key`` is in its class's property folders and its
    pane, or None for a VI no property folder lists."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None:
        return None
    accessor = class_model(graph, class_key).attributes.accessors.get(vi_key)
    if accessor is None:
        return None
    return accessor, accessor_pane(graph, class_key, vi_key)


def member_shape(graph: InMemoryVIGraph, vi_key: str) -> MethodShape | None:
    """The shape of the VI ``vi_key`` as a method of the class that lists it; None
    for a VI no class lists."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None:
        return None
    return class_model(graph, class_key).shapes.get(vi_key)


def python_class_name(class_key: str) -> str:
    """The Python name of the class generated for the class file at ``class_key``."""
    return derive_python_name(Path(class_key).name)


def _build_model(graph: InMemoryVIGraph, class_key: str) -> ClassModel:
    parent_key = graph.class_parent_key(class_key)
    attributes = class_attributes(
        graph,
        class_key,
        class_model(graph, parent_key).attributes if parent_key else None,
    )
    model = ClassModel(class_key, python_class_name(class_key), attributes)
    taken: dict[str, str] = {}
    model.clashes.update(attributes.clashes)
    for key in graph.class_member_keys(class_key):
        shape = _shape(graph, class_key, key)
        model.shapes[key] = shape
        access = graph.get_method_access(key)
        if shape.receiver_in is None and access is not None and not access.is_static:
            model.clashes[key] = (
                f"The dynamic-dispatch VI {graph.vi_display_name(key)} has no input "
                f"of its class {model.name}, so there is no object to dispatch on"
            )
            continue
        accessor = attributes.accessors.get(key)
        if accessor is not None and shape.returns_receiver:
            # An accessor that gives back another object is an ordinary method:
            # `obj.name` cannot stand for it.
            del attributes.accessors[key]
            accessor = None
        if accessor is not None:
            continue
        label = graph.vi_display_name(key)
        if shape.name in attributes.names:
            model.clashes[key] = (
                f"The method {label} and an attribute or property of "
                f"{model.name} are both '{shape.name}' in Python"
            )
        elif shape.name in taken:
            model.clashes[key] = (
                f"The methods {taken[shape.name]} and {label} of {model.name} "
                f"are both '{shape.name}' in Python"
            )
        else:
            taken[shape.name] = label
    return model


def _scope_of(graph: InMemoryVIGraph, vi_key: str) -> str:
    access = graph.get_method_access(vi_key)
    return access.scope if access is not None else "public"


def _method_name(graph: InMemoryVIGraph, vi_key: str) -> str:
    return member_name(to_function_name(Path(vi_key).name), _scope_of(graph, vi_key))


def _receivers(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> tuple[Terminal | None, Terminal | None]:
    """The class input and the class output of the VI ``vi_key`` of the class
    at ``class_key``."""
    inputs, _, outputs, _ = graph.vi_interface(vi_key)

    def is_class(t: Terminal) -> bool:
        return graph.is_class_wire(t.lv_type, vi_key, class_key)

    return (
        next((t for t in inputs if is_class(t)), None),
        next((t for t in outputs if is_class(t)), None),
    )


def _family(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> tuple[str, str, list[str]]:
    """``(root class, root VI, every VI of the family)`` of the dynamic-dispatch
    VI ``vi_key``: the declaration and each override in the loaded descendants.
    A VI that is not dynamic dispatch is its own family."""
    access = graph.get_method_access(vi_key)
    if access is None or access.is_static:
        return class_key, vi_key, [vi_key]
    name = Path(vi_key).name
    root_class, root_key = class_key, vi_key
    parent = graph.class_parent_key(class_key)
    while parent is not None:
        found = graph.class_method(parent, name)
        if found is None:
            break
        root_class, root_key = found
        parent = graph.class_parent_key(root_class)
    keys = [root_key]
    pending = graph.class_child_keys(root_class)
    seen: set[str] = set()
    while pending:
        child = pending.pop(0)
        if child in seen:
            continue
        seen.add(child)
        if graph.class_declares(child, name):
            found = graph.class_method(child, name)
            assert found is not None
            keys.append(found[1])
        pending.extend(graph.class_child_keys(child))
    return root_class, root_key, keys


def _own_returns_receiver(graph: InMemoryVIGraph, class_key: str, vi_key: str) -> bool:
    """Whether the class output of the VI is not the object it was called on,
    changed in place (a method that calls itself is taken to change it in
    place)."""
    state = _state(graph)
    if vi_key in state.returns:
        return state.returns[vi_key]
    if vi_key in state.active:
        return False
    state.active.add(vi_key)
    try:
        receiver_in, receiver_out = _receivers(graph, class_key, vi_key)
        if receiver_out is None:
            result = False
        elif receiver_in is None:
            result = True
        else:

            def preserves(node: GraphNode) -> bool:
                callee = node.callee_key if isinstance(node, VINode) else None
                owner = graph.owning_class_key(callee) if callee else None
                if callee is None or owner is None:
                    return False
                return not _family_returns_receiver(graph, owner, callee)

            result = not carries_receiver(
                graph, receiver_out.id, receiver_in.id, preserves
            )
    finally:
        state.active.discard(vi_key)
    state.returns[vi_key] = result
    return result


def _family_returns_receiver(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> bool:
    root_class, _, keys = _family(graph, class_key, vi_key)
    return any(
        _own_returns_receiver(graph, graph.owning_class_key(key) or root_class, key)
        for key in keys
    )


def _shape(graph: InMemoryVIGraph, class_key: str, vi_key: str) -> MethodShape:
    access = graph.get_method_access(vi_key)
    receiver_in, receiver_out = _receivers(graph, class_key, vi_key)
    if receiver_in is None:
        kind = MethodKind.STATIC
    else:
        is_static = access is not None and access.is_static
        kind = MethodKind.INSTANCE if is_static else MethodKind.DYNAMIC
    root_class, root_key, _ = _family(graph, class_key, vi_key)
    returns = _family_returns_receiver(graph, class_key, vi_key)

    r_in, r_off_in, r_out, r_off_out = graph.vi_interface(root_key)
    names = interface_names(r_in, r_off_in, r_out, r_off_out)
    root_receiver_in, _ = _receivers(graph, root_class, root_key)
    root_receiver_index = root_receiver_in.index if root_receiver_in else None
    parameters = {
        t.index: names.parameters[t.id]
        for t in [*r_in, *r_off_in]
        if t.id in names.parameters and t.index != root_receiver_index
    }
    results: list[int] = []
    result_fields: dict[int, str] = {}
    for t in [*r_out, *r_off_out]:
        if t.is_error_cluster or t.id not in names.fields:
            continue
        is_receiver = receiver_out is not None and t.index == receiver_out.index
        if is_receiver and not returns:
            continue
        results.append(t.index)
        result_fields[t.index] = names.fields[t.id]
    return MethodShape(
        key=vi_key,
        class_key=class_key,
        kind=kind,
        name=_method_name(graph, vi_key),
        receiver_in=receiver_in.index if receiver_in else None,
        receiver_out=receiver_out.index if receiver_out else None,
        parameters=parameters,
        results=tuple(results),
        result_fields=result_fields,
        returns_receiver=returns,
        result_class=(
            build_result_class_name(Path(root_key).name) if len(results) >= 2 else None
        ),
        root_key=root_key,
        root_class_key=root_class,
    )


def base_import(
    graph: InMemoryVIGraph, class_key: str, resolver: ImportResolver
) -> NameImport | None:
    """The import of the class the class at ``class_key`` descends from: the class
    the graph holds as its parent, imported from the module generated for it;
    None for a root class. A parent the class names that is not loaded, or has no
    module, is a gap."""
    parent_key = graph.class_parent_key(class_key)
    if parent_key is None:
        parent_name = graph.class_parent_name(class_key)
        if parent_name is None:
            return None
        raise ConversionGap(
            f"The parent class {parent_name} of {resolver.shown(class_key)} was "
            "not found; add it to the project or a --search-path"
        )
    return class_import(graph, parent_key, resolver)


def class_import(
    graph: InMemoryVIGraph, class_key: str, resolver: ImportResolver
) -> NameImport:
    """The import of the generated class of the class at ``class_key``; a class
    with no module is a gap."""
    try:
        return resolver.node_import(class_key)
    except KeyError:
        raise ConversionGap(
            f"The class {resolver.shown(class_key)} was not found; add it to the "
            "project or a --search-path"
        ) from None


def not_a_method(graph: InMemoryVIGraph, vi_key: str) -> str | None:
    """Why the member VI ``vi_key`` is not a method of its class (an attribute or
    property accessor, a polymorphic VI, a VI that was not found), or None."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None:
        return None
    label = graph.vi_display_name(vi_key)
    model = class_model(graph, class_key)
    accessor = model.attributes.accessors.get(vi_key)
    if accessor is not None:
        what = "attribute" if accessor.kind is AccessorKind.ATTRIBUTE else "property"
        return (
            f"The VI {label} is the {what} '{accessor.python_name}' of "
            f"{model.name}, which has no function to refer to"
        )
    if graph.is_stub(vi_key) or graph.get_poly_variants(vi_key):
        return f"The VI {label} is not a method of {model.name}"
    return None


def member_reference(
    graph: InMemoryVIGraph, shape: MethodShape, resolver: ImportResolver
) -> tuple[str, str]:
    """``(Class.method, the import of the class)``: what a reference to a VI of a
    class is. The object the method works on is passed as its ``self`` keyword.
    A member that is no method is a gap."""
    reason = not_a_method(graph, shape.key)
    if reason is not None:
        raise ConversionGap(reason)
    imported = class_import(graph, shape.class_key, resolver)
    return f"{imported.name}.{shape.name}", imported.statement


def result_class_imports(
    graph: InMemoryVIGraph, model: ClassModel, resolver: ImportResolver
) -> list[str]:
    """The imports of the result classes that the methods of the class return and
    that another class's module defines (an override returns its declaration's)."""
    by_class: dict[str, set[str]] = {}
    for shape in model.shapes.values():
        if shape.result_class is not None and shape.root_class_key != model.key:
            by_class.setdefault(shape.root_class_key, set()).add(shape.result_class)
    statements = []
    for root_class, names in sorted(by_class.items()):
        statement = resolver.names_import(root_class, names)
        if statement:
            statements.append(statement)
    return statements


def declares_result_class(model: ClassModel, shape: MethodShape) -> bool:
    """Whether the module of the class defines the result class of ``shape``."""
    return shape.result_class is not None and shape.root_class_key == model.key


def lazy_imports(graph: InMemoryVIGraph, class_key: str) -> bool:
    """Whether the module of the class at ``class_key`` is in an import cycle with
    other generated modules (it calls a VI that calls it back), so the imports
    its methods need go inside the methods that use them."""
    state = _state(graph)
    if state.cyclic_classes is None:
        state.cyclic_classes = _classes_in_import_cycles(graph)
    return class_key in state.cyclic_classes


def _classes_in_import_cycles(graph: InMemoryVIGraph) -> set[str]:
    """The classes that lie on a cycle of the calls between the modules, where a
    class and its member VIs are one module and a class also depends on its
    parent."""
    module_of: dict[str, str] = {}
    for key in graph.list_class_keys():
        module_of[key] = key
        for member in graph.class_member_keys(key):
            module_of[member] = key
    modules = nx.DiGraph()
    for caller, callee in graph.call_edges():
        a, b = module_of.get(caller, caller), module_of.get(callee, callee)
        if a != b:
            modules.add_edge(a, b)
    for key in sorted(set(module_of.values())):
        parent = graph.class_parent_key(key)
        if parent is not None:
            modules.add_edge(key, parent)
    cyclic: set[str] = set()
    for component in nx.strongly_connected_components(modules):
        if len(component) > 1:
            cyclic.update(k for k in component if module_of.get(k) == k)
    return cyclic


def shares_refnums(graph: InMemoryVIGraph, class_key: str) -> bool:
    """Whether the class at ``class_key``, or a class it descends from, holds a
    refnum in its private data (its copies share the refnum)."""
    if any(holds_refnum(f.type) for f in graph.class_own_fields(class_key)):
        return True
    parent = graph.class_parent_key(class_key)
    return parent is not None and shares_refnums(graph, parent)
