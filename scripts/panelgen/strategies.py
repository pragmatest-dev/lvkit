"""Per-control-type strategies: the ONE place a LabVIEW front-panel
``control_type`` decides what it becomes in generated Python -- the State
field (type + default), the widget (state_gen / panel_gen used to fan out on
a ``control_type`` string at three separate sites; see each method below for
which site it replaces), and its column spec when it is a field/element
inside an array. ``strategy_for(control)`` is the single dispatch point,
mirroring ``lvkit.codegen.nodes``'s isinstance dispatch but keyed on the
front-panel's own ``control_type`` string discriminator instead of a typed
node class.

Composition, not per-depth special-casing, is what makes nesting work:
``ClusterStrategy`` holds/derives its children's own strategies and recurses
into them; ``ArrayStrategy`` delegates to its ELEMENT's strategy (a
``ClusterStrategy`` for an array of clusters, or a scalar strategy otherwise).
A nested cluster inside an array-of-clusters therefore falls out of
``ClusterStrategy.emit_array_field`` recursing into its own children's
``emit_array_field`` -- no separate "nested" code path.
"""

from __future__ import annotations

import ast
from typing import ClassVar

from lvkit.parser.models import ParsedFPControl

from .naming import pascal_case, unique_field_names

# The NAME (not the value) of the shared caption-class constant a generated
# panel imports from `controls` (controls.theme.CAPTION_CLS) and references
# BARE in emitted source -- `ui.label(...).classes(CAPTION_CLS)` -- instead of
# inlining the literal, so a widget's caption and controls.array_control's own
# caption can never drift apart (see ControlStrategy.runtime_imports).
_CAPTION_CLS_NAME = "CAPTION_CLS"

# A usable minimum of visible array rows, so a tiny FP box isn't a 1-row
# peephole (the modern AG Grid presentation; the rest scroll).
_MIN_ARRAY_ROWS = 4
# An array OF CLUSTERS is a multi-column table: each cluster is one horizontal
# ROW of a standard height (not the cluster's tall vertical FP layout), under a
# column-header row.
CLUSTER_ROW_H = 28
_CLUSTER_CLASSES = "border rounded-md p-2 gap-1"

_EMPTY_FACTORY = {"[]": "list", "list()": "list", "{}": "dict", "dict()": "dict"}


def _dataclass_default_rhs(default: str) -> str:
    """A dataclass forbids a bare mutable default (``list``/``dict``), so route
    any mutable literal through ``default_factory``. Keyed on the literal, not
    on a control type -- so a future decoded array/cluster default is handled
    the same way as today's empty ``[]``."""
    stripped = default.strip()
    if stripped in _EMPTY_FACTORY:
        return f"field(default_factory={_EMPTY_FACTORY[stripped]})"
    if stripped[:1] in "[{":
        return f"field(default_factory=lambda: {stripped})"
    return default


def _num_prop(props: dict[str, str], key: str) -> float | None:
    """A numeric property (``StdNumMin``/``StdNumMax``) as a number, or None if
    absent/unparseable. int when integral so it serialises cleanly."""
    raw = props.get(key)
    if raw is None:
        return None
    try:
        f = float(raw)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def _range_from_props(props: dict[str, str]) -> tuple[bool, float | None, float | None]:
    """(integer, num_min, num_max) from a part's own properties -- the ONE place
    a numeric field's data range/representation is derived from StdNumMin/
    StdNumMax, whether the caller found those properties on a scalar array's
    nested ELEMENT part (``ArrayStrategy.geometry``) or on a plain control's
    own SELF part (``_numeric_range``)."""
    num_min = _num_prop(props, "StdNumMin")
    num_max = _num_prop(props, "StdNumMax")
    integer = isinstance(num_min, int) and isinstance(num_max, int)
    return integer, num_min, num_max


def _numeric_range(control: ParsedFPControl) -> tuple[bool, float | None, float | None]:
    """(integer, num_min, num_max) for a numeric control, wherever its own
    StdNumMin/StdNumMax properties live: a scalar array's nested ELEMENT part,
    or a plain field's own SELF part (parser.vi._parse_fp_parts surfaces both
    the same way -- one part_id=None part per control). This is what makes a
    numeric field's range come out the same whether it's a top-level
    scalar-array element or a cluster field (standalone or inside an
    array-of-clusters)."""
    part = next((p for p in control.parts if p.part_id is None), None)
    if part is None:
        return True, None, None
    return _range_from_props(part.props)


class ControlStrategy:
    """One LabVIEW front-panel ``control_type``'s Python shape. Subclasses
    register the ``control_type`` strings they own via ``control_types``;
    ``strategy_for`` dispatches on that."""

    control_types: ClassVar[tuple[str, ...]] = ()
    py_type: ClassVar[str] = "str"

    def __init__(self, control: ParsedFPControl) -> None:
        self.control = control

    # ---- state_gen: the State dataclass field -------------------------------

    def default_rhs(self) -> str:
        """Python source for this control's bare default value (before any
        dataclass ``field(default_factory=...)`` wrapping)."""
        return '""'

    def state_field(self, fname, reserve_class_name, emit_class) -> str:
        """One line of the State/nested dataclass body for this control.
        ``reserve_class_name``/``emit_class`` are only used by
        ``ClusterStrategy`` (a nested cluster becomes its own dataclass,
        emitted before this field's line)."""
        rhs = _dataclass_default_rhs(self.default_rhs())
        return f"    {fname}: {self.py_type} = {rhs}"

    # ---- panel_gen: widget emission ------------------------------------------

    def _widget_expr(self) -> str:
        raise NotImplementedError

    def emit_widget(
        self, owner_expr: str, field_name: str, indent: int, var_path: list[str]
    ) -> list[str]:
        """Default scalar shape: a caption above a bound widget box -- shared
        by every leaf control type (numeric/string/bool/enum/unknown). A
        control renders its label as a CAPTION ABOVE the box, faithful to the
        VI (the label part sits above the control box, not inside it); the
        widget itself carries no internal label."""
        control = self.control
        prefix = " " * indent
        inner = " " * (indent + 4)
        var = "_w_" + "_".join(var_path)
        label = control.name or field_name
        lines = [
            f"{prefix}with ui.column().classes('w-full gap-1 no-wrap'):",
            f"{inner}ui.label({label!r}).classes({_CAPTION_CLS_NAME})",
            f"{inner}{var} = {self._widget_expr()}",
        ]
        if control.is_indicator:
            # Output: one-way state -> widget, so Run's results display reactively.
            lines.append(f"{inner}{var}.bind_value_from({owner_expr}, {field_name!r})")
            lines.append(f"{inner}{var}.disable()")
        else:
            # Input: two-way, so edits latch into State for the next Run.
            lines.append(f"{inner}{var}.bind_value({owner_expr}, {field_name!r})")
        return lines

    # ---- an array field/element's column spec --------------------------------

    def emit_array_field(self, key: str, header: str) -> str:
        """Source for one ``ArrayField(...)`` column spec, when this control is
        a field of a cluster that is itself an array's element."""
        return f"ArrayField({key!r}, {header!r}, {self.control.control_type!r})"

    # ---- what `controls` names this control needs at runtime -----------------

    def runtime_imports(self) -> set[str]:
        return {_CAPTION_CLS_NAME}


class UnknownStrategy(ControlStrategy):
    """Fallback for a ``control_type`` this generator doesn't know: a plain str
    ``ui.input`` with a TODO comment. Front panels routinely mix controls the
    generator doesn't know about yet, and skipping the whole panel over one odd
    control is worse than emitting a clearly-marked TODO widget for it."""

    py_type = "str"

    def _widget_expr(self) -> str:
        return "ui.input().props('outlined dense').classes('w-full')"

    def emit_widget(self, owner_expr, field_name, indent, var_path) -> list[str]:
        prefix = " " * indent
        todo = (
            f"{prefix}# TODO: unsupported control_type "
            f"{self.control.control_type!r} -- rendered as a text input"
        )
        return [todo, *super().emit_widget(owner_expr, field_name, indent, var_path)]


class StringStrategy(ControlStrategy):
    control_types = ("stdString",)
    py_type = "str"

    def default_rhs(self) -> str:
        raw = self.control.default_value
        if raw is None:
            return '""'
        # Already a quoted Python string literal source, e.g. '"foo"'.
        try:
            ast.literal_eval(raw)
            return raw
        except (ValueError, SyntaxError):
            return '""'

    def _widget_expr(self) -> str:
        return "ui.input().props('outlined dense').classes('w-full')"


class PathStrategy(ControlStrategy):
    control_types = ("stdPath",)
    py_type = "str"

    def default_rhs(self) -> str:
        raw = self.control.default_value
        return repr(raw) if raw else '""'

    def emit_widget(self, owner_expr, field_name, indent, var_path) -> list[str]:
        # A real path control: an outlined field with a filesystem Browse on an
        # input (controls.path_control), which does its own state binding.
        control = self.control
        prefix = " " * indent
        inner = " " * (indent + 4)
        label = control.name or field_name
        return [
            f"{prefix}with ui.column().classes('w-full gap-1 no-wrap'):",
            f"{inner}ui.label({label!r}).classes({_CAPTION_CLS_NAME})",
            f"{inner}path_control({owner_expr}, {field_name!r}, "
            f"readonly={control.is_indicator})",
        ]

    def runtime_imports(self) -> set[str]:
        return {"CAPTION_CLS", "path_control"}


class NumericStrategy(ControlStrategy):
    control_types = ("stdNum", "stdNumeric")
    py_type = "float"

    def default_rhs(self) -> str:
        raw = self.control.default_value
        if raw is None:
            return "0.0"
        try:
            float(raw)
            return raw
        except ValueError:
            return "0.0"

    def _widget_expr(self) -> str:
        return "ui.number().props('outlined dense').classes('w-full')"

    def emit_array_field(self, key: str, header: str) -> str:
        # A numeric field's range/representation, derived the SAME way as a
        # scalar array's own element (see _numeric_range) -- an array-of-
        # -clusters numeric column carries its real integer/num_min/num_max,
        # not just its bare type.
        integer, num_min, num_max = _numeric_range(self.control)
        return (
            f"ArrayField({key!r}, {header!r}, {self.control.control_type!r}, "
            f"integer={integer}, num_min={num_min}, num_max={num_max})"
        )


class BoolStrategy(ControlStrategy):
    control_types = ("stdBool",)
    py_type = "bool"

    def default_rhs(self) -> str:
        raw = self.control.default_value
        if raw is None:
            return "False"
        return "True" if raw.strip() == "True" else "False"

    def _widget_expr(self) -> str:
        return "ui.switch()"


class EnumStrategy(ControlStrategy):
    """stdEnum and stdRing (a ring is a dropdown of fixed options, like an
    enum)."""

    control_types = ("stdEnum", "stdRing")
    py_type = "str"

    def default_rhs(self) -> str:
        control = self.control
        raw = control.default_value
        if raw is None:
            return '""'
        # Ring defaults are never decoded (only stdEnum is, matching the
        # parser's own DefaultData decoding); a ring's default therefore
        # always falls through to the zero value below.
        if control.control_type == "stdEnum":
            try:
                ast.literal_eval(raw)
                return raw
            except (ValueError, SyntaxError):
                pass
            if control.enum_values:
                return repr(control.enum_values[0])
        return '""'

    def _widget_expr(self) -> str:
        return (
            f"ui.select({self.control.enum_values!r})"
            ".props('outlined dense').classes('w-full')"
        )

    def emit_array_field(self, key: str, header: str) -> str:
        control = self.control
        values = (
            f", values={list(control.enum_values)!r}" if control.enum_values else ""
        )
        return f"ArrayField({key!r}, {header!r}, {control.control_type!r}{values})"


class ClusterStrategy(ControlStrategy):
    """A stdClust: COMPOSITE over its fields' own strategies. Holds no fixed
    Python type of its own -- ``state_field``/``emit_widget``/
    ``emit_array_field`` all recurse into ``strategy_for(child)`` for each
    field, so nesting (cluster-of-cluster, cluster-of-array, ...) falls out of
    that recursion rather than a per-depth special case."""

    control_types = ("stdClust",)

    def state_field(self, fname, reserve_class_name, emit_class) -> str:
        nested_name = reserve_class_name(pascal_case(self.control.name) + "State")
        emit_class(self.control.children, nested_name)
        return f"    {fname}: {nested_name} = field(default_factory={nested_name})"

    def emit_widget(self, owner_expr, field_name, indent, var_path) -> list[str]:
        control = self.control
        prefix = " " * indent
        label = control.name or field_name
        lines = [
            f"{prefix}with ui.column().classes({_CLUSTER_CLASSES!r}):",
            f"{prefix}    ui.label({label!r})"
            ".classes('text-xs font-semibold text-gray-500')",
        ]
        child_owner = f"{owner_expr}.{field_name}"
        child_names = unique_field_names(control.children)
        for child in control.children:
            child_field = child_names[child.uid]
            child_path = [*var_path, child_field]
            lines.extend(
                strategy_for(child).emit_widget(
                    child_owner, child_field, indent + 4, child_path
                )
            )
        return lines

    def emit_array_field(self, key: str, header: str) -> str:
        # A cluster nested inside an array-of-clusters' element cluster becomes
        # a COLUMN GROUP: one ArrayField per child, recursively -- so a nested
        # cluster falls out of composition, not a special case.
        fnames = unique_field_names(self.control.children)
        specs = ", ".join(
            strategy_for(ch).emit_array_field(fnames[ch.uid], ch.name)
            for ch in self.control.children
        )
        return f"ArrayField({key!r}, {header!r}, 'stdClust', fields=[{specs}])"

    def runtime_imports(self) -> set[str]:
        imports: set[str] = set()
        for child in self.control.children:
            imports |= strategy_for(child).runtime_imports()
        return imports


class ArrayStrategy(ControlStrategy):
    """indArr (and the defensive "array" alias): COMPOSITE, delegating to its
    ELEMENT's strategy -- a ``ClusterStrategy`` for an array of clusters (one
    ``ArrayField`` column per field), or a scalar strategy otherwise (a single
    ``value`` column folded straight into ``array_control(...)``'s own
    kwargs)."""

    control_types = ("indArr", "array")
    py_type = "list"

    def default_rhs(self) -> str:
        return "[]"

    def geometry(self) -> tuple[int, int, str, bool, float | None, float | None]:
        """(cell_h, visible, element_type, integer, num_min, num_max), read
        from the VI's front panel (never guessed): ``cell_h`` = one element
        cell's height, ``visible`` = how many WHOLE cells the box the developer
        drew shows, ``element_type`` = the cell's control class (``stdNum``/
        ``stdString``/...), and for a numeric element the representation +
        data range via ``_numeric_range``."""
        control = self.control
        element = next((p for p in control.parts if p.part_id is None), None)
        outer_h = control.bounds[2] - control.bounds[0]
        if element is None:
            cell_h = 24
            return cell_h, max(1, outer_h // cell_h), "stdNum", True, None, None
        cell_h = max(1, element.bounds[2] - element.bounds[0])
        # Whole cells that fit the box the developer drew (LabVIEW never shows a
        # partial row) -- the count falls out of the real bounds + real cell
        # height -- but floored to a usable minimum so a small FP box isn't a
        # 1-row peephole (the modern grid presentation; scroll reveals the rest).
        visible = max(_MIN_ARRAY_ROWS, outer_h // cell_h)
        integer, num_min, num_max = _range_from_props(element.props)
        return cell_h, visible, element.part_class, integer, num_min, num_max

    def is_cluster_array(self) -> bool:
        return bool(self.control.children)

    def emit_array_field(self, key: str, header: str) -> str:
        # An ARRAY field of a cluster that is itself an array's element (array-in-
        # cluster-in-array) is a 2D cell: a variable-length array inside one grid
        # cell isn't editable as a sub-grid. DELIBERATELY degrade to a text column
        # (not an accidental base-class fallthrough) -- the value shows as text.
        return f"ArrayField({key!r}, {header!r}, {self.control.control_type!r})"

    def emit_widget(self, owner_expr, field_name, indent, var_path) -> list[str]:
        # LabVIEW-style array control (index display + whole element cells; see
        # controls.array_control). Its geometry is READ FROM THE VI: the parser
        # exposes the index display (partID 8002) and element cell as parts, so
        # the index-column width, cell height, visible-cell count and element
        # widget all come from the real front panel, not invented constants.
        control = self.control
        prefix = " " * indent
        var = "_w_" + "_".join(var_path)
        label = control.name or field_name
        cell_h, visible, element_type, integer, num_min, num_max = self.geometry()

        if control.children:
            # Array OF CLUSTERS: one typed column per cluster field (state is a
            # list of dicts, matching the codegen's list[dict] for a cluster).
            fnames = unique_field_names(control.children)
            specs = ", ".join(
                strategy_for(ch).emit_array_field(fnames[ch.uid], ch.name)
                for ch in control.children
            )
            return [
                f"{prefix}{var} = array_control({owner_expr}, {field_name!r}, "
                f"readonly={control.is_indicator}, label={label!r}, "
                f"cell_h={CLUSTER_ROW_H}, visible={visible}, "
                f"fields=[{specs}])"
            ]

        enum_arg = ""
        if element_type in EnumStrategy.control_types and control.enum_values:
            enum_arg = f", enum_values={list(control.enum_values)!r}"
        return [
            f"{prefix}{var} = array_control({owner_expr}, {field_name!r}, "
            f"readonly={control.is_indicator}, label={label!r}, "
            f"cell_h={cell_h}, visible={visible}, "
            f"element_type={element_type!r}, integer={integer}, "
            f"num_min={num_min}, num_max={num_max}{enum_arg})"
        ]

    def runtime_imports(self) -> set[str]:
        imports = {"array_control"}
        if self.control.children:
            imports.add("ArrayField")
        return imports


_STRATEGY_CLASSES: tuple[type[ControlStrategy], ...] = (
    NumericStrategy,
    StringStrategy,
    BoolStrategy,
    PathStrategy,
    EnumStrategy,
    ClusterStrategy,
    ArrayStrategy,
)

_BY_CONTROL_TYPE: dict[str, type[ControlStrategy]] = {
    control_type: cls for cls in _STRATEGY_CLASSES for control_type in cls.control_types
}


def strategy_for(control: ParsedFPControl) -> ControlStrategy:
    """The single dispatch point from a control's ``control_type`` string to
    its ``ControlStrategy``, falling back to ``UnknownStrategy`` for anything
    not registered above -- front panels routinely mix controls this generator
    doesn't know about yet."""
    cls = _BY_CONTROL_TYPE.get(control.control_type, UnknownStrategy)
    return cls(control)
