"""The flattened form of a class's own level, written into the class.

A flattened LabVIEW class object is the cluster of the private data of each level
of its hierarchy (``codegen/classes_module.py`` writes the header and walks the
levels). Each generated class holds what is its own: ``__lv_version__`` (its
version, NI: a renamed class "resets the version number to 1.0.0.0"),
``__lv_flatten__`` (the bytes of the cluster of the private data fields it
declares) and ``__lv_unflatten__`` (reads that cluster from ``data`` at ``pos`` into
the object, giving the position after it), both taking the byte order as a
``struct`` prefix (big-endian ``'>'`` by default, native ``'='``, little-endian
``'<'``). They are the same functions a Flatten To String or Unflatten From String
of a class value calls, so a class has one pair of them, in the module that defines
it. A class with a
private data field that has no flattened form gets functions that raise
``FlattenedClassError`` naming the class, the field and the place, so a wire that
may hold other classes still works for them.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.models import ClusterField, unique_names

from ..ast_utils import to_var_name
from ..class_attributes import run_time_name
from ..class_model import python_class_name
from ..classes_module import CLASSES_MODULE_NAME, FLATTEN_ERROR_NAME
from ..context import CodeGenContext
from ..flat import (
    BYTE_ORDER_PREFIX,
    Field,
    Functions,
    Raw,
    Record,
    UnsupportedLayout,
    layout_of,
)

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph

    from ..class_model import ClassModel
    from ..import_resolver import ImportResolver
    from .shared_state import ModuleParts

# The version of a class that records none.
_UNRECORDED_VERSION = (1, 0, 0, 0)


def class_version(graph: InMemoryVIGraph, key: str) -> tuple[int, ...]:
    """The version of the class at ``key``: four numbers."""
    text = graph.get_class_version(key)
    if text is not None:
        parts = text.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return tuple(int(p) for p in parts)
    return _UNRECORDED_VERSION


class _FlattenBuilderMixin:
    """``__lv_version__``, ``__lv_flatten__`` and ``__lv_unflatten__`` of a
    class."""

    if TYPE_CHECKING:
        _graph: InMemoryVIGraph
        _import_resolver: ImportResolver
        _parts: ModuleParts
        _model: ClassModel

    def _build_flatten(
        self, class_key: str, fields: list[ClusterField]
    ) -> list[ast.stmt]:
        """The statements of the class body that make the class's own level
        flattenable."""
        graph = self._graph
        ctx = CodeGenContext(graph=graph, import_resolver=self._import_resolver)
        self._parts.share_with(ctx)
        label = python_class_name(class_key)
        attributes = [
            run_time_name(label, storage) for storage in self._model.attributes.fields
        ]
        names = unique_names(f.name for f in fields)
        built: list[Field] = []
        refusal: str | None = None
        for f, name in zip(fields, names):
            try:
                built.append(Field(to_var_name(name), layout_of(f.type, ctx)))
            except UnsupportedLayout as error:
                refusal = (
                    f"the private data field {name!r} of the class "
                    f"{graph.vi_display_name(class_key)} is {error}"
                )
                break
        if refusal is None:
            source = _level_source(
                Record(label, False, tuple(built)),
                attributes,
                [Functions(ctx, prefix) for prefix in BYTE_ORDER_PREFIX.values()],
            )
        else:
            source = _refusing_source(refusal)
            self._parts.imports.add(
                self._import_resolver.classes_import(FLATTEN_ERROR_NAME)
                or f"from .{CLASSES_MODULE_NAME} import {FLATTEN_ERROR_NAME}"
            )
        self._parts.imports.update(ctx.imports)
        version = ast.parse(f"__lv_version__ = {class_version(graph, class_key)!r}")
        return [*version.body, *ast.parse(source).body]


def _level_source(
    record: Record, attributes: list[str], all_orders: list[Functions]
) -> str:
    """The two methods that write and read the cluster ``record`` of the fields
    stored under ``attributes``, in the byte order they are given (a ``struct``
    prefix, big-endian by default): one writer and one reader function for each
    byte order, chosen by it."""
    writers: dict[str, str] = {}
    readers: dict[str, str] = {}
    for fns in all_orders:
        [written] = record.flatten("value", True, 0, fns)
        assert isinstance(written, Raw)
        writers[fns.order] = written.expression.removesuffix("(value)")
        readers[fns.order] = record.reader(fns, True)
    write_by_order = "{" + ", ".join(f"{k!r}: {v}" for k, v in writers.items()) + "}"
    read_by_order = "{" + ", ".join(f"{k!r}: {v}" for k, v in readers.items()) + "}"
    values = "".join(f"getattr(self, {a!r}), " for a in attributes)
    return (
        "def __lv_flatten__(self, order='>'):\n"
        f"    return {write_by_order}[order](({values}))\n"
        "\n"
        "def __lv_unflatten__(self, data, pos, order='>'):\n"
        f"    values, pos = {read_by_order}[order](data, pos)\n"
        f"    for attribute, field in zip({tuple(attributes)!r}, values):\n"
        "        setattr(self, attribute, field)\n"
        "    return pos\n"
    )


def _refusing_source(what: str) -> str:
    """The two methods of a level whose private data cannot be flattened: they
    raise, naming ``what``."""
    return (
        "def __lv_flatten__(self, order='>'):\n"
        f"    raise {FLATTEN_ERROR_NAME}('cannot flatten ' + {what!r} "
        "+ ' (value ' + repr(self) + ')')\n"
        "\n"
        "def __lv_unflatten__(self, data, pos, order='>'):\n"
        f"    raise {FLATTEN_ERROR_NAME}('cannot read ' + {what!r} "
        "+ ' (at byte ' + str(pos) + ')')\n"
    )
