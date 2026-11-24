"""The flattened form of a value of a LabVIEW class (``ClassLayout``).

The format and the Python that writes and reads the header are in
``classes_module`` (the package's ``lv_classes.py``): a class value is flattened by
the class of the object it holds, which may be any descendant of the class its
wire names, and read into the class its data names, looked up in the package's
``CLASSES``. The cluster of the private data of each level is the class's own
``__lv_flatten__`` / ``__lv_unflatten__`` (``class_builder/flatten.py``). A class
held in another class's private data is a field of that level's cluster, flattened
the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.models import LVType
from lvkit.type_names import ROOT_CLASS

from .classes_module import (
    CLASSES_MODULE_NAME,
    FLATTEN_CLASS_NAME,
    UNFLATTEN_CLASS_NAME,
)
from .context import CodeGenContext
from .flat import (
    Layout,
    LineWriter,
    Part,
    Raw,
    UnsupportedLayout,
    layout_of,
)
from .import_resolver import ImportResolver


def _import_classes(ctx: CodeGenContext, name: str) -> None:
    """Import ``name`` from the package's ``lv_classes`` module."""
    resolver = ctx.import_resolver
    statement = (
        resolver.classes_import(name) if isinstance(resolver, ImportResolver) else None
    )
    ctx.add_import(statement or f"from .{CLASSES_MODULE_NAME} import {name}")


@dataclass(frozen=True)
class ClassLayout(Layout):
    """A value of a class (``key``, the class file's key; None for LabVIEW
    Object): flattened by the class of the object, read into the class its data
    names. ``label`` is the class's qualified name."""

    key: str | None
    label: str

    def fixed_size(self) -> int | None:
        return None

    def helper_label(self) -> str:
        return self.label

    def flatten(self, value, prefix, depth, fns) -> list[Part]:
        _import_classes(fns.ctx, FLATTEN_CLASS_NAME)
        return [Raw(f"{FLATTEN_CLASS_NAME}({value}, {fns.order!r})")]

    def unflatten(self, target, prefix, out: LineWriter, fns) -> None:
        out.add(f"{target}, pos = {self.reader(fns, prefix)}(data, pos)")

    def reader(self, fns, prefix) -> str:
        """The function ``(data, pos) -> (object, pos)`` that reads this class's
        value: any class for a LabVIEW Object wire, else the class, its ancestors
        and its descendants."""
        _import_classes(fns.ctx, UNFLATTEN_CLASS_NAME)
        within = None if self.key is None else self.label
        return fns.named(
            "unflatten",
            self.label,
            ("class", self.key),
            lambda n: (
                f"def {n}(data, pos):\n"
                f"    return {UNFLATTEN_CLASS_NAME}"
                f"(data, pos, {within!r}, {fns.order!r})\n"
            ),
        )


# The (module, class) pairs whose private data is being checked, so a class that
# holds itself (directly or through another class) is checked once.
_CHECKING: set[tuple[int, str | None]] = set()


def class_layout(lv_type: LVType, ctx: CodeGenContext) -> ClassLayout:
    """The layout of a value whose type is the class ``lv_type`` names. The type of
    every private data field of that class and its ancestors is checked here, so a
    class with a field that has no flattened form (a Variant) is a gap of the node
    that asked, as any type with no layout is. A LabVIEW Object wire, and a
    descendant a wire of a class may hold, is only known at run time: a class with
    such a field gets functions that raise there
    (``class_builder/flatten.py``)."""
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
        assert graph is not None
        layout = ClassLayout(key, graph.vi_display_name(key))
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
