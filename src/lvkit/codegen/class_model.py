"""The model of the classes a run generates: for each LabVIEW class, the Python
names of its fields and members (``class_attributes``) and the shape of each
method.

A class is one Python class in one module. Its member VIs are its methods and
properties:

* a dynamic-dispatch VI, and a static VI that takes the class wire, is an
  instance method (the class input is ``self``);
* a static VI with no class input is a ``@staticmethod``;
* the VIs of a property folder are a property.

A method's ``MethodShape`` is what the definition and every call read, so the two
agree: its parameters (by keyword), what it returns (nothing, the one result, or
a result class of two or more) and whether its class output is a result. A
dynamic-dispatch call names the declaration while the object may be a descendant,
so the shape is computed over the declaration and every loaded override.

One model per graph and class is cached for the run (the graph is not changed
while a run generates)."""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from lvkit.class_property import PropertyRole
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.error_behaviour import ErrorBehaviour
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import GraphNode, VINode
from lvkit.type_names import ROOT_CLASS, derive_python_name

from .ast_utils import build_result_class_name, interface_names
from .class_attributes import (
    Accessor,
    AccessorPane,
    ClassAttributes,
    accessor_pane,
    is_private,
    method_name,
    property_accessors,
    storage_names,
)
from .import_resolver import NameImport
from .receiver_flow import carries_receiver

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph
    from lvkit.models import LVType, Terminal

    from .import_resolver import ImportResolver


class MethodKind(StrEnum):
    """How a member VI is called in Python."""

    DYNAMIC = "dynamic"  # dynamic dispatch: ``receiver.method(...)``
    INSTANCE = "instance"  # static, takes the class wire: ``receiver.method(...)``
    STATIC = "static"  # takes none: ``Class.method(...)``


@dataclass(frozen=True)
class MethodShape:
    """One member VI as a Python method.

    Only connector-pane terminals are in a shape. ``parameters`` maps the pane
    index of each input other than the class wire to its keyword (a property
    accessor leaves its error in out), and ``required`` are the indices of those a
    caller must wire; ``result_fields`` maps the pane index of each result to its
    field of the result class ``result_class`` (named when there is a result).
    ``results`` are the pane indices returned, in pane order, and include the
    class output when ``returns_receiver``: the class output is not the object
    the method was called on, changed in place, in this VI or in a VI that
    overrides it."""

    key: str
    class_key: str
    kind: MethodKind
    name: str
    receiver_in: int | None
    receiver_out: int | None
    parameters: dict[int, str]
    required: frozenset[int]
    results: tuple[int, ...]
    result_fields: dict[int, str]
    returns_receiver: bool
    result_class: str | None
    root_key: str
    root_class_key: str


# The methods the class body has that no VI makes (``class_builder/init_builder``).
GENERATED_NAMES = ("__init__",)


class MemberKind(StrEnum):
    """What a name of a class is in Python."""

    METHOD = "method"
    PROPERTY = "property"


@dataclass
class ClassModel:
    """The Python names and methods of one class.

    ``names`` are the names the class's objects answer to that are not private,
    the class's own and those it inherits, and what each is; ``inherited`` are
    those of the class it descends from."""

    key: str
    name: str
    attributes: ClassAttributes
    shapes: dict[str, MethodShape] = field(default_factory=dict)
    names: dict[str, MemberKind] = field(default_factory=dict)
    inherited: dict[str, MemberKind] = field(default_factory=dict)
    # A member VI whose Python name is that of another member of the class, or
    # that a class it descends from has as the other kind (a method and a
    # property): the reason it is not defined; its calls are gaps.
    clashes: dict[str, str] = field(default_factory=dict)
    # A member VI that is defined but cannot run: the reason it raises.
    unavailable: dict[str, str] = field(default_factory=dict)


@dataclass
class _RunState:
    models: dict[str, ClassModel] = field(default_factory=dict)
    returns: dict[str, bool] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)


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
    graph: InMemoryVIGraph,
    lv_type: LVType | None,
    caller_vi_key: str | None,
    node_label: str,
) -> ClassAttributes | None:
    """The attributes of the class a wire of type ``lv_type`` carries, found among
    the calling VI's own records, for the ``node_label`` node (a Bundle/Unbundle By
    Name or In Place Element) on its private data; ``None`` for a wire that is
    not a loaded class. Only the class's own VIs reach its private data, so the
    node in any other VI is a gap: that VI is broken in LabVIEW."""
    if lv_type is None:
        return None
    key = graph.class_type_key(lv_type)
    if key is None:
        return None
    _require_own_class(graph, key, caller_vi_key, f"{node_label} on the private data")
    return class_model(graph, key).attributes


def _require_own_class(
    graph: InMemoryVIGraph, class_key: str, caller_key: str | None, what: str
) -> None:
    """``what`` of the class at ``class_key`` is reached from ``caller_key``,
    which must be one of the class's own VIs (written in its body, where Python
    finds a private name). LabVIEW lets no other VI reach it, so anything else
    is a VI broken in LabVIEW: a gap. When the VI's own record claims the class
    (``claimed_owner``, by path), the class file and the VI disagree, and the
    reason says so."""
    caller_class = graph.owning_class_key(caller_key) if caller_key else None
    if caller_class == class_key:
        return
    if caller_key and graph.claimed_owner(caller_key) == class_key:
        why = "the VI names that class as its owner while the class does not list it"
    else:
        why = "the class does not list this VI"
    raise ConversionGap(
        f"{what} of the class {graph.key_label(class_key)}, but {why}, so the VI "
        "is broken in LabVIEW"
    )


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


def property_accessor_vi(
    graph: InMemoryVIGraph, class_key: str, path: str, role: PropertyRole
) -> str | None:
    """The accessor VI that reads (``GETTER``) or writes (``SETTER``) the class
    property at ``path`` in the project tree of the class at ``class_key`` or of
    the nearest class it descends from that lists one, as a class Property Node
    row names it; None when none of them does."""
    key: str | None = class_key
    while key is not None:
        for member in graph.class_member_keys(key):
            access = graph.get_method_access(member)
            if (
                access is not None
                and access.is_accessor
                and access.accessor_path == path
                and access.accessor_type == role.value
            ):
                return member
        key = graph.class_parent_key(key)
    return None


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
    inherited = dict(class_model(graph, parent_key).names) if parent_key else {}
    accessors = property_accessors(graph, class_key)
    model = ClassModel(
        class_key,
        python_class_name(class_key),
        ClassAttributes(),
        inherited=inherited,
    )
    for key in graph.class_member_keys(class_key):
        shape = _shape(graph, class_key, key, accessor=key in accessors)
        access = graph.get_method_access(key)
        if shape.receiver_in is None and access is not None and not access.is_static:
            model.unavailable[key] = (
                f"The dynamic-dispatch VI {graph.vi_display_name(key)} has no input "
                f"of its class {model.name}, so there is no object to dispatch on"
            )
            if accessors.pop(key, None) is not None:
                shape = _shape(graph, class_key, key, accessor=False)
        elif key in accessors and shape.returns_receiver:
            # An accessor that gives back another object is an ordinary method:
            # `obj.name` cannot stand for it.
            del accessors[key]
            shape = _shape(graph, class_key, key, accessor=False)
        elif key in accessors:
            behaviour = _accessor_error_behaviour(graph, class_key, key)
            if behaviour is None:
                # Its error out can carry an error of its own: an ordinary method
                # with error in and error out.
                del accessors[key]
                shape = _shape(graph, class_key, key, accessor=False)
            else:
                accessors[key] = replace(
                    accessors[key],
                    skips_on_error=behaviour is ErrorBehaviour.SKIPS_ON_ERROR,
                )
        model.shapes[key] = shape

    # The names the class body defines itself (``class_builder``) are taken.
    own: dict[str, tuple[MemberKind, str]] = {
        name: (MemberKind.METHOD, f"method {name} lvkit writes")
        for name in GENERATED_NAMES
    }

    def claim(key: str, name: str, kind: MemberKind, label: str) -> bool:
        held = own.get(name)
        if held is not None and held != (kind, label):
            model.clashes[key] = (
                f"The {label} and the {held[1]} of {model.name} are both "
                f"'{name}' in Python"
            )
            return False
        if inherited.get(name, kind) is not kind:
            model.clashes[key] = (
                f"The {label} of {model.name} is '{name}' in Python, a "
                f"{inherited[name].value} of a class {model.name} descends from"
            )
            return False
        own[name] = (kind, label)
        return True

    for key, accessor in list(accessors.items()):
        label = f"property {accessor.property_name!r}"
        if not claim(key, accessor.python_name, MemberKind.PROPERTY, label):
            del accessors[key]
    for key, shape in model.shapes.items():
        if key not in accessors and key not in model.clashes:
            label = f"method {graph.vi_display_name(key)}"
            claim(key, shape.name, MemberKind.METHOD, label)

    taken = [name for name in own if is_private(name)]
    model.attributes = ClassAttributes(
        storage_names(graph.class_own_fields(class_key), taken), accessors
    )
    model.names = dict(inherited)
    model.names.update(
        (name, kind) for name, (kind, _) in own.items() if not is_private(name)
    )
    return model


def _receivers(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> tuple[Terminal | None, Terminal | None]:
    """The class input and the class output of the VI ``vi_key`` of the class
    at ``class_key``."""
    inputs, _, outputs, _ = graph.vi_interface(vi_key)

    def is_class(t: Terminal) -> bool:
        return graph.is_class_wire(t.lv_type, class_key)

    return (
        next((t for t in inputs if is_class(t)), None),
        next((t for t in outputs if is_class(t)), None),
    )


def _family(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> tuple[str, str, list[str]]:
    """``(root class, root VI, every VI of the family)`` of the dynamic-dispatch
    VI ``vi_key``: the declaration and each override in the loaded descendants.
    A VI that is not dynamic dispatch is its own family. Only a VI the graph
    holds as a member of its class is in the family: the class generated for an
    ancestor whose VI was not loaded has no such method, so the topmost loaded
    VI is the declaration Python sees, and its connector pane is the
    signature."""
    access = graph.get_method_access(vi_key)
    if access is None or access.is_static:
        return class_key, vi_key, [vi_key]
    name = Path(vi_key).name
    root_class, root_key = class_key, vi_key
    parent = graph.class_parent_key(class_key)
    while parent is not None:
        found = _member_method(graph, parent, name)
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
            found = _member_method(graph, child, name)
            if found is not None:
                keys.append(found[1])
        pending.extend(graph.class_child_keys(child))
    return root_class, root_key, keys


def _member_method(
    graph: InMemoryVIGraph, class_key: str, name: str
) -> tuple[str, str] | None:
    """``(owner class, VI)`` of the method file ``name`` of the class at
    ``class_key`` or its nearest ancestor that lists it, when the graph holds
    that VI as a member of its class; None otherwise."""
    found = graph.class_method(class_key, name)
    if found is None or found[1] not in graph.class_member_keys(found[0]):
        return None
    return found


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


def _accessor_error_behaviour(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> ErrorBehaviour | None:
    """What the accessor VI ``vi_key`` and every VI that overrides it, or that
    it overrides, do with an incoming error, when they all do the same and it
    leaves their error out the incoming error (``passes_error_along``); None
    otherwise: a call of one of them may report an error of its own, so the
    accessor is an ordinary method."""
    _, _, keys = _family(graph, class_key, vi_key)
    behaviours = {graph.vi_error_behaviour(key) for key in keys}
    if len(behaviours) != 1:
        return None
    behaviour = behaviours.pop()
    return behaviour if behaviour.passes_error_along else None


def _family_returns_receiver(
    graph: InMemoryVIGraph, class_key: str, vi_key: str
) -> bool:
    root_class, _, keys = _family(graph, class_key, vi_key)
    return any(
        _own_returns_receiver(graph, graph.owning_class_key(key) or root_class, key)
        for key in keys
    )


def _shape(
    graph: InMemoryVIGraph, class_key: str, vi_key: str, *, accessor: bool
) -> MethodShape:
    """The method the member VI ``vi_key`` is. The error in and error out of a
    property ``accessor`` are not a parameter or a result: ``obj.name`` reads and
    ``obj.name = value`` writes the value alone."""
    access = graph.get_method_access(vi_key)
    receiver_in, receiver_out = _receivers(graph, class_key, vi_key)
    if receiver_in is None:
        kind = MethodKind.STATIC
    else:
        is_static = access is not None and access.is_static
        kind = MethodKind.INSTANCE if is_static else MethodKind.DYNAMIC
    root_class, root_key, _ = _family(graph, class_key, vi_key)
    returns = _family_returns_receiver(graph, class_key, vi_key)

    r_in, r_off_in, r_out, _ = graph.vi_interface(root_key)
    names = interface_names(r_in, r_off_in, r_out)
    root_receiver_in, _ = _receivers(graph, root_class, root_key)
    root_receiver_index = root_receiver_in.index if root_receiver_in else None
    parameters = {
        t.index: names.parameters[t.id]
        for t in r_in
        if t.id in names.parameters
        and t.index != root_receiver_index
        and not (accessor and t.is_error_cluster)
    }
    required = frozenset(
        t.index for t in r_in if t.index in parameters and is_required(t, "input")
    )
    results: list[int] = []
    result_fields: dict[int, str] = {}
    for t in r_out:
        if t.id not in names.fields or (accessor and t.is_error_cluster):
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
        name=method_name(graph, vi_key),
        receiver_in=receiver_in.index if receiver_in else None,
        receiver_out=receiver_out.index if receiver_out else None,
        parameters=parameters,
        required=required,
        results=tuple(results),
        result_fields=result_fields,
        returns_receiver=returns,
        result_class=(
            build_result_class_name(Path(root_key).name) if results else None
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


def class_reference(
    graph: InMemoryVIGraph | None,
    resolver: ImportResolver | None,
    lv_type: LVType,
    what: str,
) -> NameImport:
    """The import and the Python name of the class ``lv_type`` names. LabVIEW
    Object, the class every class descends from, is Python's ``object`` (a
    generated class with no parent has no base; no import). A class that is not
    loaded, or has no module, is a gap whose reason starts with ``what`` (the
    node or default it is for)."""
    if lv_type.classname == ROOT_CLASS:
        return NameImport(statement="", name="object")
    class_key = graph.class_type_key(lv_type) if graph is not None else None
    if graph is None or class_key is None or resolver is None:
        raise ConversionGap(
            f"{what} {lv_type.classname} names a class that is "
            "not loaded; add it to the project or a --search-path"
        )
    return class_import(graph, class_key, resolver)


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
    """Why the member VI ``vi_key`` has no function in its class (a polymorphic
    VI, a VI that was not found, one whose name another member has), or None."""
    class_key = graph.owning_class_key(vi_key)
    if class_key is None:
        return None
    model = class_model(graph, class_key)
    if vi_key in model.clashes:
        return model.clashes[vi_key]
    if graph.is_stub(vi_key) or graph.get_poly_variants(vi_key):
        return f"The VI {graph.vi_display_name(vi_key)} is not a method of {model.name}"
    return None


def check_private_access(
    graph: InMemoryVIGraph,
    name: str,
    member_key: str,
    class_key: str,
    caller_key: str | None,
    use: str,
) -> None:
    """A private member ``name`` (the VI ``member_key``) of the class at
    ``class_key`` is reached only from that class's own VIs (LabVIEW lets no
    other VI reach it), which are written in the class body where Python finds
    the name; ``use`` of it ("A call of", "A reference to") from anything else is
    a gap."""
    if is_private(name):
        _require_own_class(
            graph,
            class_key,
            caller_key,
            f"{use} the private VI {graph.vi_display_name(member_key)}",
        )


def member_reference(
    graph: InMemoryVIGraph,
    shape: MethodShape,
    resolver: ImportResolver,
    caller_key: str | None,
) -> tuple[str, str]:
    """``(Class.method, the import of the class)``: what a reference to a VI of a
    class is; for the read or write VI of a property, the property's
    ``fget`` / ``fset``. The object the method works on is passed as its ``self``
    keyword. A member with no function is a gap."""
    reason = not_a_method(graph, shape.key)
    if reason is not None:
        raise ConversionGap(reason)
    imported = class_import(graph, shape.class_key, resolver)
    accessor = class_model(graph, shape.class_key).attributes.accessors.get(shape.key)
    name = shape.name if accessor is None else accessor.python_name
    check_private_access(
        graph, name, shape.key, shape.class_key, caller_key, "A reference to"
    )
    if accessor is None:
        return f"{imported.name}.{name}", imported.statement
    side = "fget" if accessor.role is PropertyRole.GETTER else "fset"
    return f"{imported.name}.{name}.{side}", imported.statement


def result_class_imports(
    graph: InMemoryVIGraph, model: ClassModel, resolver: ImportResolver
) -> list[str]:
    """The imports of the result classes that the methods of the class return and
    that another class's module defines (an override returns its declaration's)."""
    by_class: dict[str, set[str]] = {}
    for shape in model.shapes.values():
        if (
            shape.result_class is not None
            and has_result_class(model, shape)
            and shape.root_class_key != model.key
        ):
            by_class.setdefault(shape.root_class_key, set()).add(shape.result_class)
    statements = []
    for root_class, names in sorted(by_class.items()):
        statement = resolver.names_import(root_class, names)
        if statement:
            statements.append(statement)
    return statements


def has_result_class(model: ClassModel, shape: MethodShape) -> bool:
    """Whether the method ``shape`` returns a result class: it has a result and is
    not the getter or setter of a property (those return their value itself)."""
    accessors = model.attributes.accessors
    return shape.result_class is not None and shape.key not in accessors


def declares_result_class(model: ClassModel, shape: MethodShape) -> bool:
    """Whether the module of the class defines the result class of ``shape``."""
    return has_result_class(model, shape) and shape.root_class_key == model.key
