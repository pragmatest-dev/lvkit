"""The flattened form of a value of a LabVIEW class (``ClassLayout``).

The format and the Python that writes and reads it are in ``flat_class_source``. A
class value is flattened by the class of the object it holds, which may be any
descendant of the class its wire names, so the module gets a table of the classes
that wire can hold (``class_family``: the class, its ancestors and its loaded
descendants; every loaded class for a LabVIEW Object wire), each with the
functions that flatten and read the private data cluster of its own level. A
class held in another class's private data is a field of that level's cluster.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.graph import InMemoryVIGraph
from lvkit.models import ClusterField, LVType, unique_names
from lvkit.primitive_resolver import NativeError
from lvkit.type_names import ROOT_CLASS

from .ast_utils import to_var_name
from .class_attributes import run_time_name
from .class_model import class_model, class_module, python_class_name
from .context import CodeGenContext
from .flat import (
    Field,
    Functions,
    Layout,
    LineWriter,
    Part,
    Raw,
    Record,
    UnsupportedLayout,
    layout_of,
)
from .flat_class_source import FLATTEN_CLASS, UNFLATTEN_CLASS
from .native_failure import own_exception_class

# The exception an unflatten of a class that does not read raises; it carries the
# LabVIEW error code of what was wrong in ``code`` (1401, 1403 or 1527). The
# Unflatten From String entry of the catalog lists the same failure, so the
# module defines the class once whichever of them asks first.
FLATTENED_CLASS_ERROR = NativeError(
    exception="FlattenedClassError",
    base="Exception",
    code=1403,
    code_attribute="code",
)

# The version of a class that records none (NI: a renamed class "resets the version
# number to 1.0.0.0").
_UNRECORDED_VERSION = (1, 0, 0, 0)


def class_family(graph: InMemoryVIGraph, key: str | None) -> list[str]:
    """The keys of the classes a wire of the class at ``key`` can hold: the class,
    its ancestors and its loaded descendants, sorted; every loaded class for
    ``None`` (a LabVIEW Object wire)."""
    everything = graph.list_class_keys()
    if key is None:
        return sorted(everything)
    keys = {key, *graph.class_ancestor_keys(key)}
    keys.update(k for k in everything if key in graph.class_ancestor_keys(k))
    return sorted(keys)


def _version(graph: InMemoryVIGraph, key: str) -> tuple[int, ...]:
    text = graph.get_class_version(key)
    if text is not None:
        parts = text.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return tuple(int(p) for p in parts)
    return _UNRECORDED_VERSION


@dataclass(frozen=True)
class ClassLayout(Layout):
    """A value of a class (``key``, the class file's key; None for LabVIEW
    Object): its level, class name, versions and the private data of each level."""

    key: str | None
    label: str

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return self.label

    def _graph(self, fns: Functions) -> InMemoryVIGraph:
        graph = fns.ctx.graph
        if graph is None:
            raise UnsupportedLayout(
                f"a LabVIEW class value ({self.label}) with no graph to list the "
                "classes it can hold"
            )
        return graph

    def _imports(self, fns: Functions) -> None:
        for line in ("import importlib", "import struct"):
            fns.ctx.add_import(line)
        fns.ctx.add_module_function(own_exception_class(FLATTENED_CLASS_ERROR))

    def _table(self, fns: Functions) -> str:
        """The module-level table of the classes this wire can hold, added to the
        module on first use; its name."""
        graph = self._graph(fns)
        registry = fns.ctx.flat_functions
        full_key = ("classes", fns.order, self.key)
        if full_key in registry:
            return registry[full_key]
        taken = set(registry.values())
        base = f"_lv_flat_classes_{to_var_name(self.label)}"
        name, serial = base, 2
        while name in taken:
            name, serial = f"{base}_{serial}", serial + 1
        registry[full_key] = name
        resolver = fns.ctx.import_resolver
        rows: list[ast.expr] = []
        keys: list[ast.expr | None] = []
        for class_key in class_family(graph, self.key):
            level = self._level(fns, class_key)
            keys.append(ast.Constant(value=graph.vi_display_name(class_key)))
            rows.append(
                ast.Tuple(
                    elts=[
                        ast.Constant(value=class_module(resolver, class_key)),
                        ast.Constant(value=python_class_name(class_key)),
                        ast.Tuple(
                            elts=[
                                ast.Constant(value=n)
                                for n in _version(graph, class_key)
                            ],
                            ctx=ast.Load(),
                        ),
                        ast.Tuple(
                            elts=[
                                ast.Constant(value=a)
                                for a in self._attributes(graph, class_key)
                            ],
                            ctx=ast.Load(),
                        ),
                        ast.Name(id=level[0], ctx=ast.Load()),
                        ast.Name(id=level[1], ctx=ast.Load()),
                    ],
                    ctx=ast.Load(),
                )
            )
        fns.ctx.add_module_global(name, ast.Dict(keys=keys, values=rows))
        return name

    def _attributes(self, graph: InMemoryVIGraph, class_key: str) -> list[str]:
        """The names a class's own private data fields are stored under, as
        ``getattr`` reads them outside the class."""
        class_name = python_class_name(class_key)
        return [
            run_time_name(class_name, storage)
            for storage in class_model(graph, class_key).attributes.fields
        ]

    def _level(self, fns: Functions, class_key: str) -> tuple[str, str]:
        """The names of the functions that flatten and read the cluster of the
        private data the class at ``class_key`` declares (a tuple of its fields)."""
        graph = self._graph(fns)
        fields: list[ClusterField] = graph.get_class_fields(class_key) or []
        names = unique_names(f.name for f in fields)
        label = python_class_name(class_key)
        built: list[Field] = []
        for f, name in zip(fields, names):
            try:
                built.append(Field(to_var_name(name), layout_of(f.type, fns.ctx)))
            except UnsupportedLayout as error:
                return self._refusing_level(fns, class_key, label, name, str(error))
        record = Record(label=label, named=False, fields=tuple(built))
        write = record.flatten("value", True, 0, fns)
        assert len(write) == 1 and isinstance(write[0], Raw)
        return (
            write[0].expression.removesuffix("(value)"),
            record.reader(fns, True),
        )

    def _refusing_level(
        self, fns: Functions, class_key: str, label: str, field: str, reason: str
    ) -> tuple[str, str]:
        """The functions of a class level whose private data has a field with no
        flattened form: they raise when a value of the class is flattened or read,
        naming the class, the field and the place, so a wire that may hold other
        classes still works for them."""
        graph = self._graph(fns)
        what = (
            f"the private data field {field!r} of the class "
            f"{graph.vi_display_name(class_key)} is {reason}"
        )

        def write(name: str) -> str:
            return (
                f"def {name}(value):\n"
                f"    raise FlattenedClassError('cannot flatten ' + "
                f"{what!r} + ' (value ' + repr(value) + ')')\n"
            )

        def read(name: str) -> str:
            return (
                f"def {name}(data, pos):\n"
                f"    error = FlattenedClassError('cannot read ' + "
                f"{what!r} + ' (at byte ' + str(pos) + ')')\n"
                f"    error.code = 1403\n"
                f"    raise error\n"
            )

        key = ("refusing", class_key)
        return (
            fns.named("flatten", label, key, write),
            fns.named("unflatten", label, key, read),
        )

    def flatten(self, value, prefix, depth, fns) -> list[Part]:
        self._imports(fns)
        table = self._table(fns)
        name = fns.named(
            "flatten",
            self.label,
            ("class", self.key),
            lambda n: _fill(FLATTEN_CLASS, n, table, fns.order),
        )
        return [Raw(f"{name}({value})")]

    def unflatten(self, target, prefix, out: LineWriter, fns) -> None:
        out.add(f"{target}, pos = {self.reader(fns, prefix)}(data, pos)")

    def reader(self, fns, prefix) -> str:
        self._imports(fns)
        table = self._table(fns)
        return fns.named(
            "unflatten",
            self.label,
            ("class", self.key),
            lambda n: _fill(UNFLATTEN_CLASS, n, table, fns.order),
        )


def _fill(template: str, name: str, table: str, order: str) -> str:
    return (
        template.replace("$NAME", name)
        .replace("$TABLE", table)
        .replace("$ORDER", order)
    )


# The (module, class) pairs whose private data is being checked, so a class that
# holds itself (directly or through another class) is checked once.
_CHECKING: set[tuple[int, str | None]] = set()


def class_layout(lv_type: LVType, ctx: CodeGenContext) -> ClassLayout:
    """The layout of a value whose type is the class ``lv_type`` names. The type of
    every private data field of that class and its ancestors is checked here, so a
    class with a field that has no flattened form (a Picture) is a gap of the node
    that asked, as any type with no layout is. A LabVIEW Object wire, and a
    descendant a wire of a class may hold, is only known at run time: a class with
    such a field gets functions that raise there (``_refusing_level``)."""
    graph = ctx.graph
    name = lv_type.classname or "a class"
    if lv_type.classname == ROOT_CLASS:
        layout = ClassLayout(None, ROOT_CLASS)
    else:
        key = graph.class_type_key(lv_type) if graph is not None else None
        if key is None:
            raise UnsupportedLayout(
                f"a LabVIEW class value ({name}) of a class that is not loaded; add "
                "it to the project or a --search-path"
            )
        layout = ClassLayout(key, name)
    assert graph is not None
    token = (id(ctx.flat_functions), layout.key)
    if token in _CHECKING:
        return layout
    _CHECKING.add(token)
    try:
        static = (
            []
            if layout.key is None
            else [layout.key, *graph.class_ancestor_keys(layout.key)]
        )
        for class_key in static:
            for field in graph.get_class_fields(class_key) or []:
                try:
                    layout_of(field.type, ctx)
                except UnsupportedLayout as error:
                    raise UnsupportedLayout(
                        f"a LabVIEW class value ({layout.label}), whose private "
                        f"data field {field.name!r} of the class "
                        f"{graph.vi_display_name(class_key)} is {error}"
                    ) from error
    finally:
        _CHECKING.discard(token)
    return layout
