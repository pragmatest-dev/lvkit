"""Shared pipeline models — used by parser, graph, and codegen layers.

Two groups of types:
- Primitive / wiring types (dataclasses): LVType, EnumValue, ClusterField
- Flow types (Pydantic BaseModel): Terminal hierarchy, Tunnel, PropertyDef,
  Frame hierarchy

The Frame hierarchy (CaseFrame/SequenceFrame/EventFrame) lives here because
the parser constructs these instances directly, so they must live in a
parser-names module — not inside graph/. Frame metadata (selector
values, ranges, …) is a pure projection out of the graph; a frame's actual
contents are read from ``InMemoryVIGraph.child_nodes``/``top_level_nodes``,
never stored on the Frame itself — graph/models.py's ``GraphNode``
subclasses are the single source of truth for node structure.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Literal

from pydantic import BaseModel

from .conversion_gap import ConversionGap
from .dotnet_types import DotNetType
from .lv_type_codes import BOOLEAN_TYPE_NAMES, STRING_TYPE_NAMES
from .string_literal import quote_string
from .type_names import derive_python_name, is_enum_member_name

# Type alias for scalar constant/default values
ScalarValue = str | int | float | bool | None

# ============================================================
# Shared types (used by both graph and codegen layers)
# ============================================================


@dataclass(frozen=True)
class ValuesNotKept:
    """An array value too large to keep: it was read to its end, so its extent in
    the file is exact, but none of its elements are stored. ``element_count`` is
    the product of ``dimension_sizes`` (one size per array dimension, slowest-varying
    first)."""

    element_count: int
    dimension_sizes: tuple[int, ...]
    # The elements exactly as the VI stores them (row-major, each flattened
    # big-endian), and their type: what a data file written for the array holds.
    # Empty / None when the extent was read from a type this does not slice.
    element_bytes: bytes = field(default=b"", repr=False, compare=False)
    element_type: LVType | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return f"<{self.element_count} elements, values not kept>"

    def reason(self) -> str:
        shape = " x ".join(str(size) for size in self.dimension_sizes)
        return (
            f"an array of {self.element_count} elements ({shape}) whose values "
            "are not kept"
        )


@dataclass(frozen=True)
class UndecodedPicture:
    """A Picture value that holds drawing data. The drawing is read past (its
    length is stored) but not turned into a value."""

    byte_count: int

    def __str__(self) -> str:
        return f"<Picture, {self.byte_count} bytes of drawing not decoded>"

    def reason(self) -> str:
        return f"a Picture of {self.byte_count} bytes whose drawing is not decoded"


@dataclass(frozen=True)
class UnreadPrivateData:
    """A class object that holds private data this could not read: its class is
    not loaded, or its stored levels do not match the class's. ``class_name`` is
    the class's file name."""

    class_name: str

    def __str__(self) -> str:
        return f"{self.class_name}(private data not read)"

    def reason(self) -> str:
        return f"an object of {self.class_name} whose private data is not read"


@dataclass(frozen=True)
class ValueNotStored:
    """A constant whose value is not in the block diagram: the VI keeps it in its
    default data (DFDS), which this does not decode yet."""

    def __str__(self) -> str:
        return "<value stored in the VI's default data, not decoded>"

    def reason(self) -> str:
        return (
            "a value the VI stores in its default data (DFDS), which lvkit does "
            "not decode yet"
        )


UnwritableValue = ValuesNotKept | UndecodedPicture | UnreadPrivateData | ValueNotStored


def find_unwritable(value: object) -> UnwritableValue | None:
    """The first part of a decoded value (an array's elements, a cluster's fields,
    recursively) that holds no value code generation could write, else None."""
    if isinstance(
        value, (ValuesNotKept, UndecodedPicture, UnreadPrivateData, ValueNotStored)
    ):
        return value
    if isinstance(value, dict):
        children: Iterable[object] = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = find_unwritable(child)
        if found is not None:
            return found
    return None


class LVTypeKind(str, Enum):
    """The family of a resolved ``LVType`` — its top-level discriminator.

    A CLOSED set: every ``LVType`` is built with one of these (an unrecognized
    LabVIEW type name collapses to ``PRIMITIVE`` in ``parser.type_mapping``); there
    is no "unknown" kind. ``(str, Enum)`` (not ``StrEnum`` — 3.11+) so a member IS
    its string and compares equal to a raw ``"primitive"`` literal drop-in — the
    ~40 construction sites and the comparisons all keep working.
    """

    PRIMITIVE = "primitive"
    ENUM = "enum"
    CLUSTER = "cluster"
    ARRAY = "array"
    RING = "ring"
    TYPEDEF_REF = "typedef_ref"
    CLASS = "class"


class VIKind(IntEnum):
    """What kind of file a VI-format file is, from the 2-byte code in the
    ``LVSR`` Instrument record (``<Instrument Type>``).

    ``IntEnum`` so a member equals the raw code read from the binary. The stored
    numbering is NOT the numbering of NI's documented VI Type property. Each code
    below was verified on the sample corpus (4,737 files, LabVIEW 9-25) by an
    independent structural test, in both directions:

    - 2: ordinary VI (4,031 files).
    - 4: global VI (12 files) -- the VI a global variable is saved as.
    - 6: control (635 files) -- every ``.ctl``: custom control, type definition
      and strict type definition alike.
    - 11: polymorphic VI (59 files).

    Any other code is not a member; see ``vi_kind_label``.
    """

    ORDINARY = 2
    GLOBAL = 4
    CONTROL = 6
    POLYMORPHIC = 11

    @property
    def label(self) -> str:
        """The kind in LabVIEW's words."""
        return _VI_KIND_LABELS[self]


_VI_KIND_LABELS: dict[VIKind, str] = {
    VIKind.ORDINARY: "VI",
    VIKind.GLOBAL: "Global VI",
    VIKind.CONTROL: "Control",
    VIKind.POLYMORPHIC: "Polymorphic VI",
}


def vi_kind_label(code: int) -> str:
    """Display text for a stored VI-kind code: the kind's LabVIEW name, or
    ``unknown (code N)`` for a code that is not a ``VIKind`` member."""
    try:
        return VIKind(code).label
    except ValueError:
        return f"unknown (code {code})"


class SharedVariableKind(str, Enum):
    """How a Shared Variable is published, as the variable type string the VI's
    block-diagram link table records for it (``Global`` is LabVIEW's recorded name
    for a single-process variable). ``(str, Enum)`` so a member equals the recorded
    string it was read from."""

    SINGLE_PROCESS = "Global"
    NETWORK = "Network"

    @property
    def label(self) -> str:
        """The kind in LabVIEW's words."""
        return _SHARED_VARIABLE_KIND_LABELS[self]


_SHARED_VARIABLE_KIND_LABELS: dict[SharedVariableKind, str] = {
    SharedVariableKind.SINGLE_PROCESS: "Single Process",
    SharedVariableKind.NETWORK: "Network-Published",
}


# The name a module spells a named type's class as -- its own, or an alias when
# two classes of one module derive the same name -- or None when the module has
# no class to import for the type.
TypeNamer = Callable[["LVType"], str | None]


@dataclass(frozen=True)
class PaneSlot:
    """One used terminal of a connector pane: its slot ``index``, whether it is an
    output, the type of the value it carries and, when its descriptor is labelled,
    the terminal's ``name``."""

    index: int
    is_output: bool
    type: LVType
    name: str | None = None


class ServerClass(str, Enum):
    """The VI Server class a generic reference (``LVObjCtl``) is of, when its type
    says (``CtlFlags``, ``labview-binary-format.md``)."""

    VI = "VI"
    APPLICATION = "Application"


@dataclass
class LVType:
    """LabVIEW type structure - unified representation for all types."""

    kind: LVTypeKind
    underlying_type: str | None = None
    ref_type: str | None = None
    classname: str | None = None
    # The VI Server class a generic reference (``LVObjCtl``) is of, when its type
    # says. None for any other reference.
    server_class: ServerClass | None = None
    # A User Event refnum's event name: the label of the data type wired to
    # Create User Event (for a type-definition data type, the label on the
    # constant's own instance of it, not the .ctl file name). None for every
    # other refnum and for an unlabeled data type.
    user_event_name: str | None = None
    # The .NET type of a .NET node's terminal, as the VI records it. The VI types
    # the terminal as a DotNet refnum (or, for a .NET enum, an I32); this is the
    # type the node actually passes.
    dotnet_type: DotNetType | None = None

    values: dict[str, EnumValue] | None = None
    fields: list[ClusterField] | None = None
    element_type: LVType | None = None
    # A Function type (a VI's connector pane): its used terminals in slot order.
    # A strictly typed VI refnum holds one as its ``element_type``. None for every
    # other type.
    # ``compare=False`` -- what a VI of this type must have, not type identity.
    pane: tuple[PaneSlot, ...] | None = field(default=None, compare=False)
    # A Map's value type (``element_type`` is its key type); None for every
    # other type.
    value_type: LVType | None = None
    dimensions: int | None = None
    typedef_path: str | None = None
    typedef_name: str | None = None
    # The typedef's ``.ctl`` file name and the class's ``.lvclass`` file name,
    # without the library chain ``typedef_name`` / ``classname`` carry in front of
    # them. ``compare=False`` -- the same identity, kept as its own part.
    typedef_file: str | None = field(default=None, compare=False)
    class_file: str | None = field(default=None, compare=False)
    description: str | None = None
    # Why this type could not be read from the file, when the VI records a
    # descriptor that did not decode; None for a type that read cleanly.
    unreadable: str | None = None
    # For a MeasureData type (waveform / digital data): its LabVIEW "flavor"
    # (e.g. "Float64Waveform", "DigitalWaveform", "Digitaldata"). These are
    # opaque built-in structures with no VCTP children, so their component names
    # come from a built-in table keyed by this flavor (see measure_data.py).
    measure_flavor: str | None = None
    # This type's OWN wire style, when it has one — read by the one wire lookup
    # (``render.style.wire_style``) so wire color/style is data-driven, not a
    # fixed per-family table. None for a type that uses the family default (every
    # built-in today); a LabVIEW class fills it from its ``.lvclass``.
    # ``compare=False`` — presentation, never type identity/coercion.
    wire_style: WireStyle | None = field(default=None, compare=False)
    # For a class instance whose ``.lvclass`` is loaded: the private-data fields
    # of each level of its hierarchy, oldest ancestor first (a flattened object
    # holds one cluster per level). None when the class is not loaded.
    # ``compare=False`` -- what a value of the type is read with, not its identity.
    class_levels: list[list[ClusterField]] | None = field(default=None, compare=False)

    def _refnum_leaf(self) -> bool:
        """Whether this type, or the element type of its arrays, is a refnum."""
        if self.kind == LVTypeKind.ARRAY and self.element_type is not None:
            return self.element_type._refnum_leaf()
        return self.kind == LVTypeKind.PRIMITIVE and self.underlying_type == "Refnum"

    def type_ref(self) -> str | None:
        """The LabVIEW name of the class or typedef this type names -- the
        reference a caller's dependencies are searched for -- or None for a type
        that names neither. A class refnum names its class; a named enum, ring,
        cluster or typedef reference names its typedef."""
        if self.kind == LVTypeKind.PRIMITIVE:
            if self.underlying_type == "Refnum":
                return self.classname
            return None
        if self.kind in (
            LVTypeKind.CLUSTER,
            LVTypeKind.ENUM,
            LVTypeKind.RING,
            LVTypeKind.TYPEDEF_REF,
        ):
            return self.typedef_name
        return None

    def python_class_name(self) -> str | None:
        """The class name generated code spells this type as, or None for a type
        that has no class of its own."""
        ref = self.type_ref()
        return derive_python_name(ref) if ref else None

    def named_types(self) -> Iterator[LVType]:
        """Every type this type's annotation names by class: this type when it
        has a class name, and each such element type of an array."""
        if self.python_class_name() is not None:
            yield self
        elif self.kind == LVTypeKind.ARRAY and self.element_type is not None:
            yield from self.element_type.named_types()

    def to_python(self, names: TypeNamer | None = None) -> str:
        """Render as Python type annotation string.

        ``names`` gives the name the module spells a named class or typedef as,
        or None when the module can import none. A named type that has none
        renders as its LabVIEW representation: an ``int`` for an enum or ring,
        ``Any`` otherwise.
        """
        class_name = self.python_class_name()
        if class_name is not None:
            local = class_name if names is None else names(self)
            if local is not None:
                return local
            return "int" if self.kind in (LVTypeKind.ENUM, LVTypeKind.RING) else "Any"
        if self.kind == LVTypeKind.PRIMITIVE:
            return _LV_TO_PYTHON_TYPE.get(self.underlying_type or "", "Any")
        elif self.kind == LVTypeKind.ARRAY:
            inner = self.element_type.to_python(names) if self.element_type else "Any"
            result = f"list[{inner}]"
            for _ in range((self.dimensions or 1) - 1):
                result = f"list[{result}]"
            return result
        elif self.kind == LVTypeKind.CLUSTER:
            if not self.classname:
                # An anonymous cluster is generated as a positional tuple.
                return "tuple"
            return "dict[str, Any]"
        elif self.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            return "int"
        return "Any"

    def enum_member_ref(
        self, ordinal: int, names: TypeNamer | None = None
    ) -> str | None:
        """``Class.member`` for the item of this named enum or ring whose value
        is ``ordinal``, spelled with the module's name for the class; None when
        the type has no importable class, no such item, or an item that cannot
        be spelled as an attribute."""
        class_name = self.python_class_name()
        member = self.member_name(ordinal)
        if class_name is None or member is None or not is_enum_member_name(member):
            return None
        if names is not None:
            class_name = names(self)
            if class_name is None:
                return None
        return f"{class_name}.{member}"

    def item_for(self, raw: ScalarValue) -> str | None:
        """The enum/ring item a recorded default names: ``raw`` is an ordinal
        (an int, or its decimal text). None when it isn't one, or no item of
        this type has that value."""
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return None
        try:
            return self.member_name(int(raw))
        except ValueError:
            return None

    def item_labels(self, spell: Callable[[str], str] = str) -> list[str]:
        """The items of this enum or ring in value order, each ``name`` when the
        values are 0, 1, 2, ... and ``name = value`` when they are not (a ring
        numbered by its own values). ``spell`` writes each name."""
        if not self.values:
            return []
        items = sorted(self.values.items(), key=lambda kv: kv[1].value)
        if [ev.value for _, ev in items] == list(range(len(items))):
            return [spell(name) for name, _ in items]
        return [f"{spell(name)} = {ev.value}" for name, ev in items]

    def member_name(self, ordinal: int) -> str | None:
        """The enum/ring item whose value is ``ordinal``, or None when this type
        has no such item (or no items loaded)."""
        for name, item in (self.values or {}).items():
            if item.value == ordinal:
                return name
        return None

    def type_descriptor(self, *, expand_named: bool = True) -> str:
        """Render a LabVIEW-faithful type label from this type's structure.

        The FAITHFUL counterpart to :meth:`to_python` — every non-codegen
        surface (describe/netlist/diff/queries/docs) must use this, never
        ``to_python()``, per the project's clean-room LAW: LabVIEW's strict
        type system (enum members, cluster fields, refnum kinds, numeric
        representation) is load-bearing and must never be silently narrowed
        to a Python annotation outside the code generator.

        - a type with a ``dotnet_type`` (a .NET node's terminal): the recorded
          .NET type name, ``MccDaq.DaqDeviceInterface``; as
          ``DotNet refnum{MccDaq.ErrorInfo}`` when the terminal is a DotNet
          refnum (or an array of them), the name keeping its own array suffix.
        - ``array``: bracket-nested element label, one pair per dimension
          (``[DBL]``, ``[[I32]]``); an unresolved element renders ``?``.
        - ``enum``/``ring``: ``{member, member, …}`` in ORDINAL order (by
          ``EnumValue.value``), prefixed with the stripped typedef stem when
          named (``MethodEnum{setUp, testMethod, tearDown}``) or the base
          word when anonymous (``enum{...}`` / ``ring{...}``).
        - ``cluster``/``typedef_ref``: ``"error cluster"`` for an error
          cluster; otherwise ``Name{f1, f2}`` (named + fields), ``Name``
          (named, fields unknown), ``cluster{f1, f2}`` (fields, unnamed), or
          ``"cluster"`` (neither).
        - ``primitive``: a class refnum renders its class name verbatim
          (``TestCase.lvclass``); a parametrized refnum shows its element in
          braces like a cluster/enum (``Queue refnum{error cluster}``,
          ``Notifier refnum{DBL}``; a User Event leads with its event name,
          ``UserEvent refnum{stopped: TF}``); otherwise ``"<ref_type> refnum"`` /
          ``"refnum"``. Other primitives map through the shared numeric/scalar
          token table, falling back to the raw ``underlying_type`` (never
          "Any"/"int") for an unmapped token.

        ``expand_named`` (default ``True``, every existing caller's
        behavior is UNCHANGED): when ``False`` (lvnet §10/§11's terse type
        renderer -- ``render_lvnet`` is the one caller that ever passes
        this), a NAMED enum/ring/cluster/typedef renders its bare stripped
        name ALONE (``lveventtype``, not ``lveventtype{Mouse Down, Mouse
        Up, …~300 more}``) — full member expansion is verbose-only (§11);
        an ANONYMOUS type (no name to fall back on) still renders its full
        structural form regardless, since there's nothing else faithful to
        show. Recurses into containers (``array``'s element, a parametrized
        refnum's element) with the SAME flag, so ``[NamedThing]``/
        ``refnum{NamedThing}`` also collapse to the name one level down.
        """
        if self.unreadable is not None:
            readable = replace(self, unreadable=None)
            return (
                f"{readable.type_descriptor(expand_named=expand_named)} "
                f"(unreadable {quote_string(self.unreadable)})"
            )
        if self.dotnet_type is not None:
            if self._refnum_leaf():
                return f"DotNet refnum{{{self.dotnet_type.name}}}"
            return self.dotnet_type.name
        if self.kind == LVTypeKind.ARRAY:
            dims = self.dimensions or 1
            inner = (
                self.element_type.type_descriptor(expand_named=expand_named)
                if self.element_type
                else "?"
            )
            return "[" * dims + inner + "]" * dims
        if self.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            if self.typedef_name:
                name = _strip_typedef_stem(self.typedef_name)
                if not expand_named:
                    return name
                return f"{name}{{{', '.join(self.item_labels())}}}"
            body = "{" + ", ".join(self.item_labels()) + "}"
            return ("enum" if self.kind == LVTypeKind.ENUM else "ring") + body
        if self.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
            if _is_error_cluster(self):
                return "Error"
            name = _strip_typedef_stem(self.typedef_name) if self.typedef_name else None
            if name and not expand_named:
                return name
            fields = ", ".join(f.name for f in (self.fields or []))
            if name and fields:
                return f"{name}{{{fields}}}"
            if name:
                return name
            if fields:
                return f"cluster{{{fields}}}"
            return "cluster"
        if self.kind == LVTypeKind.PRIMITIVE:
            if self.underlying_type == "Refnum":
                if self.classname:
                    return self.classname
                if self.ref_type:
                    # A parametrized refnum shows its element in braces, like a
                    # cluster/enum shows its members: ``Queue refnum{Error}``,
                    # ``Notifier refnum{DBL}``.
                    if self.element_type is not None:
                        inner = self.element_type.type_descriptor(
                            expand_named=expand_named
                        )
                        if self.user_event_name:
                            inner = f"{self.user_event_name}: {inner}"
                        return f"{self.ref_type} refnum{{{inner}}}"
                    return f"{self.ref_type} refnum"
                return "refnum"
            if self.underlying_type == "Set":
                return f"set{{{_member_descriptor(self.element_type, expand_named)}}}"
            if self.underlying_type == "Map":
                key = _member_descriptor(self.element_type, expand_named)
                value = _member_descriptor(self.value_type, expand_named)
                return f"map{{{key}: {value}}}"
            return _SCALAR_TYPE_DESCRIPTOR.get(
                self.underlying_type or "", self.underlying_type or "?"
            )
        return "?"


def _member_descriptor(member: LVType | None, expand_named: bool) -> str:
    """A Set or Map member's descriptor; ``?`` when its type is not known."""
    return member.type_descriptor(expand_named=expand_named) if member else "?"


class WireLineStyle(IntEnum):
    """A wire pen's ``Style`` — Solid/Dash/Dot/DashDot/DashDotDot. ``SOLID`` is
    the default and the only style built-in wires use; a LabVIEW class can pick
    another in its ``.lvclass``. ``IntEnum`` so a member equals the raw 0–4 the
    binary stores, drop-in."""

    SOLID = 0
    DASH = 1
    DOT = 2
    DASH_DOT = 3
    DASH_DOT_DOT = 4


@dataclass(frozen=True)
class WireStyle:
    """Everything a wire needs to draw itself — the ONE style object the ONE wire
    lookup (``render.style.wire_style``) returns and every drawing site renders.
    A type's OWN style (``LVType.wire_style``) overrides the default per-family
    color/width there; most types have none (the lookup derives theirs from the
    family), but a LabVIEW class carries one decoded from its ``.lvclass``.

    ``color``/``width``/``line_style`` describe a flat wire. ``edge_color`` +
    ``core_width`` add the class two-band look — an outer ``edge_color`` band at
    ``width`` with a ``color`` center of ``core_width`` on top; both None for a
    flat wire (``width`` is then the single stroke). New wire styles (e.g. a fill
    pattern) are added HERE, once, and every site renders them."""

    color: str
    width: float
    line_style: WireLineStyle = WireLineStyle.SOLID
    edge_color: str | None = None
    core_width: float | None = None
    # The pen's 8-row fill-pattern bitmap (one byte per row). Non-empty => the
    # class wire is patterned (drawn as a chain) rather than solid; () => solid.
    fill_pattern: tuple[int, ...] = ()


@dataclass
class EnumValue:
    """A single value in an enum typedef."""

    value: int
    description: str | None = None


def enum_values_from_labels(
    labels: list[str], values: list[int] | None = None
) -> dict[str, EnumValue]:
    """An ordered label list -> ``{label: EnumValue(value)}``. The one place
    enum/ring members get their values, shared by every type reconstructor
    (VCTP, CONP, FP-heap) so they can't drift.

    ``values`` are a ring's own item values (its heap ``ringSparseValues``,
    see ``parser.fp_heap_type.ring_sparse_values``), parallel to ``labels``.
    Without them, or when the two lists don't line up, each item's value is
    its ordinal, as for an enum."""
    if not values or len(values) != len(labels):
        values = list(range(len(labels)))
    return {label: EnumValue(value=v) for label, v in zip(labels, values)}


@dataclass
class ClusterField:
    """A field in a cluster."""

    name: str
    type: LVType | None = None


def _is_error_cluster(lv_type: LVType) -> bool:
    """Check if a type is an error cluster.

    Detects error clusters by:
    1. TypeDef name contains "error" (case-insensitive)
    2. Cluster with status/code/source fields
    """
    if lv_type.kind not in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return False

    # Check typedef name
    typedef_name = lv_type.typedef_name or ""
    if "error" in typedef_name.lower():
        return True

    # Check field names for error cluster pattern
    if lv_type.fields:
        field_names = {f.name.lower() for f in lv_type.fields}
        error_fields = {"status", "code", "source"}
        if error_fields <= field_names:
            return True

    return False


# control_type (FP control class) -> LVTypeKind, for a terminal whose LVType
# didn't resolve. Composites map to their kind; every scalar/refnum/path control
# is a PRIMITIVE. An unrecognized control (or None) yields None -- an honest
# "unknown family", never a guessed default.
_CONTROL_FAMILY_KIND: dict[str, LVTypeKind] = {
    "stdClust": LVTypeKind.CLUSTER,
    "udClassDDO": LVTypeKind.CLASS,
    "indArr": LVTypeKind.ARRAY,
    "stdArray": LVTypeKind.ARRAY,
    "stdRing": LVTypeKind.RING,
    "stdEnum": LVTypeKind.ENUM,
    "stdRefNum": LVTypeKind.PRIMITIVE,
    "stdColorNum": LVTypeKind.PRIMITIVE,
    "stdNum": LVTypeKind.PRIMITIVE,
    "stdDBL": LVTypeKind.PRIMITIVE,
    "stdSGL": LVTypeKind.PRIMITIVE,
    "stdBool": LVTypeKind.PRIMITIVE,
    "stdString": LVTypeKind.PRIMITIVE,
    "stdPath": LVTypeKind.PRIMITIVE,
}


def _control_kind(control_type: str | None) -> LVTypeKind | None:
    """The type KIND for an unresolved terminal, from its FP control class, or
    None when the control is unrecognized/absent (honest unknown)."""
    if not control_type:
        return None
    return _CONTROL_FAMILY_KIND.get(control_type)


class TypeResolutionNeeded(ConversionGap):
    """Raised when a named type dependency cannot be resolved.

    Same pattern as VILibResolutionNeeded / TerminalResolutionNeeded.
    The type is referenced but not loaded in the dep_graph.
    """

    def __init__(self, type_name: str, context: str = ""):
        self.type_name = type_name
        self.context = context
        msg = f"Type resolution needed for '{type_name}'"
        if context:
            msg += f" (referenced by {context})"
        super().__init__(msg)


class Terminal(BaseModel):
    """A connection point on a node. Edges connect to them."""

    model_config = {"arbitrary_types_allowed": True}

    id: str
    index: int
    direction: str  # "input" or "output"
    name: str | None = None
    # Display-only label (resolved def's terminal name, e.g. "x"/"difference").
    # Separate from ``name`` because ``name`` drives codegen variable naming;
    # this must never change generated code. Used by the renderer's connector-
    # pane hover / tooltip and by describe. See graph/construction.py.
    display_name: str | None = None
    # Full dotted navigation path to a NESTED Bundle/Unbundle-By-Name field
    # (e.g. "Strain Config.strain gage information.poisson ratio") — tooltip-only
    # detail beside the terse leaf ``display_name`` the glyph shows. None for a
    # top-level field. Set at the ``stamp_nmux_lane_names`` seam.
    field_path: str | None = None
    lv_type: LVType | None = None
    var_name: str | None = None  # set during codegen
    nmux_role: str | None = None  # "agg" or "list"
    nmux_field_index: int | None = None  # class field index
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    default_value: ScalarValue = None
    # Set when ``default_value`` shows a part (an array too large to keep, a
    # Picture's drawing) that code generation has no value to write for.
    unwritable: UnwritableValue | None = None
    inverted: bool = False  # "Not" applied to this terminal (e.g. cpdArith)
    # The DCO uid this terminal's heap ``<dco>`` names. Terminals that name the
    # same DCO are one value: an In Place Element structure's In/Out Element
    # port and its border node's cluster/array/index terminal have no wire
    # between them, only this shared DCO.
    dco_uid: str | None = None

    def python_type(self, names: TypeNamer | None = None) -> str:
        """Python type string derived from lv_type (see ``LVType.to_python``)."""
        return self.lv_type.to_python(names) if self.lv_type else "Any"

    def type_descriptor(self) -> str:
        """This terminal's exact type descriptor, or ``""`` when its type didn't
        resolve (``type_kind`` still identifies the family)."""
        return self.lv_type.type_descriptor() if self.lv_type else ""

    @property
    def type_kind(self) -> LVTypeKind | None:
        """The terminal's type KIND — from the resolved type, or derived from its
        FP control class when the type didn't resolve; None when genuinely
        unknown (never a guessed default)."""
        if self.lv_type is not None:
            return self.lv_type.kind
        return _control_kind(getattr(self, "control_type", None))

    def type_label(self) -> str:
        """FAITHFUL human type label: the exact ``type_descriptor()`` when the
        type resolved, else the type-KIND family word, else ``"unknown"``. The
        one shared label describe/netlist/diff use so they never diverge."""
        return self.type_descriptor() or (
            self.type_kind.value if self.type_kind else "unknown"
        )

    @property
    def is_error_cluster(self) -> bool:
        """Check if this terminal carries a LabVIEW error cluster.

        Primary check: type-based (_is_error_cluster on lv_type).
        Fallback: name-based, but ONLY when the type is unknown or is a
        cluster without field metadata.  If the type is known to be
        non-cluster (array, primitive, etc.), the name is irrelevant —
        "error" in a terminal name does not make an Array an error cluster.

        Name matching uses word boundaries: "error in" / "error out" as
        phrases, not substring "in" inside arbitrary words like "pass*in*g".
        """
        if self.lv_type:
            if _is_error_cluster(self.lv_type):
                return True
            # Type is known and is NOT an error cluster — trust the type.
            # Only fall through to name heuristic when type is missing.
            return False
        # No type info — fall back to name heuristic (word-boundary match).
        name = (self.name or "").lower()
        if "no error" in name:
            return True
        return bool(re.search(r"\berror\s+(in|out)\b", name))


def bundle_unbundle_name(
    terminals: list[Terminal],
    *,
    by_name: bool = False,
) -> str | None:
    """Human Bundle/Unbundle name from FIELD (``nmux_role=="list"``) terminal
    direction -- the ONE rule behind the whole cluster-mux family (nMux/mux/
    demux, the IPES cluster border node ``decomposeClusterNode``, ...): fields
    are INPUTS -> assembling a cluster (Bundle), fields are OUTPUTS -> taking
    one apart (Unbundle). The AGGREGATE (``nmux_role=="agg"``) terminal(s) are
    never counted. ``by_name`` selects the "By Name" wording (nMux /
    decomposeClusterNode access fields by name, not position). Returns None
    when there are no field terminals -- nothing to key the direction off;
    the caller decides the fallback (a static default name, or leaving an
    existing name untouched)."""
    field_terms = [t for t in terminals if t.nmux_role == "list"]
    if not field_terms:
        return None
    bundling = field_terms[0].direction == "input"
    if by_name:
        return "Bundle By Name" if bundling else "Unbundle By Name"
    return "Bundle" if bundling else "Unbundle"


class InPlaceBorderSide(str, Enum):
    """Which half of an In Place Element Structure's border a tile is: the READ
    tile takes the element out of the data, the WRITE tile puts it back."""

    READ = "read"
    WRITE = "write"


def inplace_border_side(
    node_type: str, terminals: list[Terminal]
) -> InPlaceBorderSide | None:
    """Which half of the structure's border a border node is, told apart purely
    by the terminals it carries -- no guessing:

    - ``decomposeClusterNode`` -- the field terminals are outputs on the read
      tile (Unbundle) and inputs on the write tile (Bundle).
    - ``decomposeDataValRefNode`` -- the ``DataValueRef`` refnum is the INPUT on
      the read tile (it derefs the reference) and the OUTPUT on the write tile
      (it hands the reference back).
    - ``decomposeArrayNode`` -- the read tile only indexes (array in, element
      out); the write tile replaces (array back OUT). An array-kind OUTPUT means
      the write tile.

    Returns ``None`` when the discriminating terminal isn't present."""
    if node_type == "decomposeClusterNode":
        fields = [t for t in terminals if t.nmux_role == "list"]
        if not fields:
            return None
        if fields[0].direction == "input":
            return InPlaceBorderSide.WRITE
        return InPlaceBorderSide.READ
    if node_type == "decomposeDataValRefNode":
        for t in terminals:
            lt = t.lv_type
            if lt is not None and lt.ref_type == "DataValueRef":
                if t.direction == "input":
                    return InPlaceBorderSide.READ
                return InPlaceBorderSide.WRITE
        return None
    if node_type == "decomposeArrayNode":
        has_array_out = any(
            t.lv_type is not None
            and t.lv_type.kind == LVTypeKind.ARRAY
            and t.direction == "output"
            for t in terminals
        )
        if has_array_out:
            return InPlaceBorderSide.WRITE
        return InPlaceBorderSide.READ
    return None


_INPLACE_BORDER_NAMES = {
    ("decomposeDataValRefNode", InPlaceBorderSide.READ): "DVR Read",
    ("decomposeDataValRefNode", InPlaceBorderSide.WRITE): "DVR Write",
    ("decomposeArrayNode", InPlaceBorderSide.READ): "Array Index",
    ("decomposeArrayNode", InPlaceBorderSide.WRITE): "Array Replace",
}


def inplace_border_name(node_type: str, terminals: list[Terminal]) -> str | None:
    """Faithful per-tile name for the two IN-PLACE-ELEMENT-STRUCTURE border
    nodes that carry no field shape (unlike ``decomposeClusterNode``, which
    goes through :func:`bundle_unbundle_name`): "DVR Read" / "DVR Write" and
    "Array Index" / "Array Replace", from :func:`inplace_border_side`.

    Returns ``None`` when the discriminating type isn't present (leave the
    node's fallback name untouched). Public NI docs: the border nodes are the
    'Data Value Reference Read / Write Element' and 'Array Index / Replace
    Elements' nodes -- these are the compact per-tile forms."""
    side = inplace_border_side(node_type, terminals)
    if side is None:
        return None
    return _INPLACE_BORDER_NAMES.get((node_type, side))


class FPTerminal(Terminal):
    """A front-panel control or indicator on a VINode.

    ``is_public`` says whether it is wired to the connector pane. An off-pane
    one (``is_public=False``) has ``index=None``: it occupies no connector-pane
    slot.
    """

    kind: Literal["fp"] = "fp"
    index: int | None
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    is_indicator: bool = False
    is_public: bool = True
    # The front-panel caption (a control's second, user-visible text next to
    # its label) and whether it is shown. The label stays the identity.
    caption: str | None = None
    caption_visible: bool = False
    control_type: str | None = None
    # The FRONT-PANEL DCO uid this BD terminal references — the BD↔FP bridge
    # (``fPTerm/<dco>@uid`` == the ``fPDCO@uid`` of the owning control). Unlike
    # ``id`` (which carries the BD ``fPTerm`` heap uid), this is the front-panel
    # OBJECT's identity, so it's the stable key for correlating "the same
    # control" across two versions of a VI — including a rename (same fp_dco_uid,
    # changed ``name``). See graph/diff.py's terminal correlation. None when the
    # BD terminal has no resolvable FP DCO (rare).
    fp_dco_uid: str | None = None
    default_value: ScalarValue = None
    enum_values: list[str] = []
    # Structure containment for a SPECIFIC on-diagram GLYPH of this control —
    # set only when the heap places this terminal inside a case/event/disable
    # frame or a stacked-sequence frame via an sRN's termList (a control used
    # as e.g. an Event Structure's registered event-source terminal — see
    # graph/construction.py's frame-attribution pass). None (the common case)
    # means no such frame-scoped placement was found — either the control is
    # genuinely VI-global, or it was never referenced via an sRN. FPTerminal
    # itself isn't a GraphNode (one FPTerminal is shared VI-wide, not one per
    # placement), so this can't just inherit GraphNode.parent/frame — it
    # mirrors that shape instead (see render/scene.py's frame-path lookup).
    parent: str | None = None
    frame: str | int | None = None


class DisableStructureKind(str, Enum):
    """Which disable-family structure a ``class="commentNode"`` is. All three
    share the heap element class and frame shape but LabVIEW draws them
    differently, so the render styles per kind (dotted border for the two
    disable kinds; solid border + a type icon for Type Specialization).

    Detected from stored fields, never the user-editable border label (see
    ``parser/nodes/disable.py::_disable_kind``):

    - DIAGRAM: a plain Diagram Disable Structure (Enabled / Disabled).
    - CONDITIONAL: a Conditional Disable Structure (per-symbol conditions +
      an else/Default), each frame carrying condition tokens.
    - TYPE_SPEC: a Type Specialization Structure (malleable-VI type cascade),
      marked by the heap's ``Tag0273``. Frames get a bare storage-order ``[i]``
      -- LabVIEW's [N] + Accepted/Declined/Ignored are a compile result it does
      not persist (see issue #31).
    """

    DIAGRAM = "diagram"
    CONDITIONAL = "conditional"
    TYPE_SPEC = "type_spec"


class TunnelMode(str, Enum):
    """A loop tunnel's BASE aggregation mode -- ``dco class="lpTun"`` ONLY (a
    ``None`` ``Tunnel.mode`` means "not a loop tunnel", not "unknown"; every
    other tunnel kind -- lSR/rSR/lMax/csTun/seqTun/... -- never carries a
    mode). This is one of LabVIEW's two independent tunnel axes; the second is
    the orthogonal ``Tunnel.conditional`` modifier (see there). Decoded from
    the lpTun dco's own child elements (see ``parser/nodes/base.py::
    _lp_tun_mode``):

    - INDEXING: auto-index / auto-aggregate is ENABLED -- read directly from
      the ``innerLpTunDCO``'s ``objFlags`` bit ``0x400000`` (a second, older
      encoding uses an explicit ``<TunnelType>``). On an OUTPUT tunnel this
      builds an array element-by-element (element inner -> array outer); on an
      INPUT tunnel it indexes the array (array outer -> element inner). Same
      flag, opposite directions -- LabVIEW calls both "indexing".
    - LAST_VALUE: OUTPUT tunnel with indexing DISABLED (``innerLpTunDCO``
      present, index bit clear) -- passes only the FINAL iteration's value.
    - CONCATENATING: ``<TunnelType>02</TunnelType>`` -- concatenates all
      iterations' inputs into one array of the same dimension (rare).
    - PASSTHROUGH: INPUT tunnel with indexing disabled, or a plain non-loop
      pass-through -- same value in and out every iteration.

    The ``CONDITIONAL`` menu item is NOT a mode -- it is the orthogonal
    ``Tunnel.conditional`` flag that layers onto ANY output base mode
    (conditional last-value / conditional indexing / conditional concatenate).
    """

    INDEXING = "INDEXING"
    LAST_VALUE = "LAST_VALUE"
    CONCATENATING = "CONCATENATING"
    PASSTHROUGH = "PASSTHROUGH"


class TunnelTerminal(Terminal):
    """An outer or inner tunnel terminal on a StructureNode."""

    kind: Literal["tunnel"] = "tunnel"
    tunnel_type: str = ""  # "lSR", "rSR", "lpTun", "lMax", "caseSel"
    boundary: str = ""  # "outer" or "inner"
    paired_id: str | None = None  # matching terminal on other side
    # For case-structure inner tunnels: the owning CaseFrame's selector_value.
    # None for outer tunnels and for non-case-structure tunnels (loop/sequence).
    frame: str | int | None = None
    # Carried straight from the parsed Tunnel (construction.py) so
    # graph/operations.py::_tunnels_from_terminals can round-trip them onto
    # the rebuilt Tunnel -- see Tunnel.mode/conditional/condition_terminal_uid/
    # sr_initialized/sr_stack_depth for what each means and which tunnel_type
    # populates it (condition_terminal is the qualified condition_terminal_uid).
    mode: TunnelMode | None = None
    conditional: bool = False
    condition_terminal: str | None = None
    sr_initialized: bool | None = None
    sr_stack_depth: int | None = None
    # Position of the frame an inner tunnel terminal sits in (see
    # ``Tunnel.frame_position``); ``frame`` above keeps the case selector value.
    frame_position: int | None = None


class Tunnel(BaseModel):
    """A tunnel connecting structure outer/inner terminals."""

    outer_terminal_uid: str
    inner_terminal_uid: str
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax", "caseSel", etc.
    paired_terminal_uid: str | None = None
    # Loop-tunnel BASE aggregation mode -- populated ONLY for ``tunnel_type ==
    # "lpTun"`` (see TunnelMode); None for every other tunnel_type.
    mode: TunnelMode | None = None
    # Orthogonal "Conditional" modifier (LabVIEW's Conditional menu item) --
    # True when an OUTPUT tunnel aggregates/keeps a value only on iterations
    # where a per-iteration boolean holds. Layers onto ANY base mode (see
    # TunnelMode); read from the lpTun dco's ``<IsConditional>True``. False for
    # non-conditional and for input tunnels (indexing has no conditional).
    conditional: bool = False
    # A Conditional tunnel's condition terminal -- the Boolean input inside the
    # loop that decides, per iteration, whether this tunnel takes the value.
    # The file links them through the lpTun's ``<LpTunConditionDCO>``; this is
    # the inner face of that condition dco. None when not Conditional.
    condition_terminal_uid: str | None = None
    # Shift-register STRUCTURE, not bits -- populated ONLY on the matching
    # tunnel_type, None on every other one:
    # - sr_initialized: True when this lSR tunnel's outer terminal carries an
    #   external wire (a value feeding the register before the loop starts).
    #   Set on tunnel_type == "lSR" tunnels only.
    sr_initialized: bool | None = None
    # - sr_stack_depth: this rSR's shift-register depth (len(lsrDCOList)) --
    #   1 for a normal (unstacked) shift register, N for an N-deep stacked
    #   one. Set on tunnel_type == "rSR" tunnels only.
    sr_stack_depth: int | None = None
    # Position, in its structure's frame list, of the frame this tunnel's inner
    # face sits in. Set for the structures whose inner faces are per frame
    # (Event, Sequence); None elsewhere.
    frame_position: int | None = None


class PropertySegment(BaseModel):
    """One step of a property row's path: its name and PropItemCode."""

    name: str
    code: int


class PropertyDef(BaseModel):
    """One Property Node row: the path of the property it reads or writes.

    ``segments`` holds one entry per path step, in order -- a plain property
    (``Value``) has one, a nested one (``Label.Text``) has two. ``name`` is the
    segment names joined with ``.``."""

    segments: list[PropertySegment]

    @property
    def name(self) -> str:
        return ".".join(seg.name for seg in self.segments)


# ============================================================
# Frame types (Pydantic) — shared between graph nodes and codegen
# ============================================================


class SelectorRange(BaseModel):
    """One selector range a case frame matches, from SelectRangeArray32.

    A frame can match several: a single value (``start == end``, e.g. ``3``),
    a closed range (``start..end``, e.g. ``3..10``), or an open range
    (``open_start``/``open_end`` for ``..5`` / ``3..``). LabVIEW reconstructs
    the selector label from these live — there is no stored per-frame label —
    so we preserve them faithfully for the renderer to format by type
    (enum item names, quoted strings, ``a, b``, ``a..b``)."""

    start: int
    end: int
    open_start: bool = False  # "..end" — no lower bound
    open_end: bool = False  # "start.." — no upper bound

    @property
    def is_single(self) -> bool:
        return self.start == self.end and not self.open_start and not self.open_end


class Frame(BaseModel):
    """Base frame — common fields for any structure frame."""

    model_config = {"arbitrary_types_allowed": True}

    uid: str | None = None
    inner_node_uids: list[str] = []
    # This frame's own saved background fill ("right-click > Background
    # Color" in the editor), decoded from its own ``<diag>``'s DIRECT-CHILD
    # ``<bgColor>`` (``parser.utils.heap_color``) — never a descendant's,
    # which could belong to a nested comment/attachment box instead. None
    # when absent/unparseable (falls back to the theme default fill).
    bg_color: str | None = None


class CaseFrame(Frame):
    """A frame in a case structure — selected by a selector value."""

    selector_value: str | int = 0
    is_default: bool = False
    # Faithful selector ranges from SelectRangeArray32 (numeric/enum
    # selectors). Empty for the default frame, booleans, and strings —
    # there ``selector_value`` already holds the display token. The renderer
    # formats these against the selector's resolved type.
    selector_ranges: list[SelectorRange] = []
    # Faithful selector strings for STRING selectors (a frame can match
    # several, e.g. ``"jpe", "jpeg", "jpg"``). Empty for non-string frames and
    # the default frame — there ``selector_value``/``selector_ranges`` carry
    # the token. ``selector_value`` holds the first string as the unique key.
    selector_strings: list[str] = []
    # For a Boolean selector: the value this frame matches (True or False). None
    # for every other selector type.
    boolean_value: bool | None = None
    # For an error-cluster selector: whether this is the "No Error" frame (the one
    # that matches status False).
    is_no_error: bool = False


class SequenceFrame(Frame):
    """A frame in a flat or stacked sequence — executes in order."""

    index: int = 0


class SequenceLocal(BaseModel):
    """A Stacked Sequence's sequence local: a value one frame writes and later
    frames read. The file lists one terminal per frame; ``frame_terminal_uids``
    keeps that order, so the list position is the frame index."""

    uid: str
    frame_terminal_uids: list[str] = []


class EventFrame(Frame):
    """A frame in an Event Structure — handles ONE registered event.

    Unlike a case frame, the active frame is chosen at RUNTIME by whichever
    event actually fires — there is no fixed selector wire/value, so this
    carries a display ``event_label`` instead of ``selector_value``.

    LabVIEW stores a per-frame label only for the frame it last displayed in
    the editor (heap ``selString``/``dIdx``) — there is no per-frame label
    table like a case's dataspace ``SelectorTable``. That ONE frame's label
    is the faithful, LabVIEW-rendered text (e.g. ``[3] "Control": Value
    Change`` or ``[0] Timeout``, brackets included — LabVIEW's own
    convention). Every OTHER frame's label is RECONSTRUCTED from its
    ``EventSpec`` heap entry (source control caption + event-type name, when
    both are resolvable) in the same bracketed format; an unresolvable
    control or an unconfirmed event-type code degrades gracefully rather
    than fabricating a name (see parser/nodes/event.py).
    """

    # Positional frame index (order in the Event Structure's frame list). The
    # active frame is chosen at runtime, so there is no selector_value to key
    # on — this position IS the stable correlation token, and it matches what
    # the renderer stamps (``construction.py`` → ``str(idx)``), so diff
    # frame-paths line up with the SVG's ``data-path`` for cross-pane lookup.
    index: int = 0
    event_label: str = ""


# ============================================================
# Utilities
# ============================================================

# LabVIEW's conventional short token for the numeric family + Boolean/Path —
# shared by the renderer's glyph labels (render/style.py's ``_TYPE_REPR``,
# terminal-icon text like a wire tooltip's "DBL"/"I32" box) AND
# ``LVType.type_descriptor()``'s faithful text label. These read identically in
# both contexts; String/Variant diverge (a glyph reads "abc"/"Var", a
# faithful text label reads the full word) and stay defined separately in
# each consumer — see ``_SCALAR_TYPE_DESCRIPTOR`` below and ``render/style.py``.
_NUMERIC_TYPE_DESCRIPTOR: dict[str, str] = {
    "NumFloat64": "DBL",
    "NumFloat32": "SGL",
    "NumFloatExt": "EXT",
    "NumComplex64": "CSG",
    "NumComplex128": "CDB",
    "NumComplexExt": "CXT",
    "NumInt8": "I8",
    "NumInt16": "I16",
    "NumInt32": "I32",
    "NumInt64": "I64",
    "NumUInt8": "U8",
    "NumUInt16": "U16",
    "NumUInt32": "U32",
    "NumUInt64": "U64",
    **{name: "TF" for name in BOOLEAN_TYPE_NAMES},
    "Path": "Path",
}

# The full faithful-label scalar map used by ``LVType.type_descriptor()`` — the
# shared numeric/Boolean/Path table plus the non-numeric tokens spelled out
# in full (a text label reads "String"/"Variant", never the glyph
# abbreviation "abc"/"Var" — see ``render/style.py::_TYPE_REPR`` for that).
_SCALAR_TYPE_DESCRIPTOR: dict[str, str] = {
    **_NUMERIC_TYPE_DESCRIPTOR,
    **{name: "String" for name in STRING_TYPE_NAMES},
    "Variant": "Variant",
    "LVVariant": "Variant",
}

_LV_TO_PYTHON_TYPE: dict[str, str] = {
    "NumInt8": "int",
    "NumInt16": "int",
    "NumInt32": "int",
    "NumInt64": "int",
    "NumUInt8": "int",
    "NumUInt16": "int",
    "NumUInt32": "int",
    "NumUInt64": "int",
    "NumFloat32": "float",
    "NumFloat64": "float",
    **{name: "str" for name in STRING_TYPE_NAMES},
    **{name: "bool" for name in BOOLEAN_TYPE_NAMES},
    "Path": "Path",
    "Variant": "Any",
    "LVVariant": "Any",
    "Refnum": "Any",
    "Void": "None",
}


# Control-type -> LVType, a module constant (immutable LVType literals) —
# built once, like ``_CONTROL_FAMILY_KIND`` above, not per call.
_CONTROL_TYPE_TO_LVTYPE: dict[str, LVType] = {
    "stdPath": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path"),
    "stdString": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
    "stdBool": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
    "stdNum": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    "stdDBL": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    "stdI32": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
    "stdI16": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt16"),
    "stdU32": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32"),
    "stdU16": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16"),
}


def control_type_to_lvtype(control_type: str) -> LVType | None:
    """Map a LabVIEW control type to LVType."""
    return _CONTROL_TYPE_TO_LVTYPE.get(control_type)


def _strip_typedef_stem(typedef_name: str) -> str:
    """The type's own display name from a possibly library-qualified
    ``typedef_name``: drop everything before the last ``:`` qualifier and
    strip a ``.ctl`` control-file extension. UNLIKE ``derive_python_name``,
    this is never mangled into a Python identifier — it's a LabVIEW label
    (``type_descriptor()``), not a codegen type name, so spaces/punctuation in the
    type's own name are kept verbatim (mirrors the stripping convention in
    ``graph/construction.py``'s ``_format_lv_type_for_display``)."""
    return typedef_name.rsplit(":", 1)[-1].replace(".ctl", "")


def leading_element(value: object) -> object:
    """The first element of a decoded array value, however many dimensions it
    has: a multi-dimensional array's value is lists nested one level per
    dimension (see ``parser.vi._decode_element``), so this walks down the first
    entry of each. ``None`` when an empty array is reached; a value that is not a
    list (a scalar, or a cluster's dict) is returned as is."""
    while isinstance(value, list):
        value = value[0] if value else None
    return value


def unique_names(names: Iterable[str]) -> list[str]:
    """``names`` with each repeat made distinct by a `` (2)``, `` (3)`` suffix, in
    order -- a cluster may hold several fields with the same label, and a value
    keyed by field name needs one key per field."""
    seen: Counter[str] = Counter()
    result = []
    for name in names:
        seen[name] += 1
        result.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return result


def contains_class_instance(lv_type: LVType | None) -> bool:
    """Whether ``lv_type`` is, or holds in a field, element, key or value, a class
    instance (a refnum naming a class)."""
    if lv_type is None:
        return False
    if lv_type.underlying_type == "Refnum" and lv_type.classname:
        return True
    return (
        contains_class_instance(lv_type.element_type)
        or contains_class_instance(lv_type.value_type)
        or any(contains_class_instance(f.type) for f in lv_type.fields or [])
    )
