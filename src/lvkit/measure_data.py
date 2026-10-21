"""Built-in layout of LabVIEW MeasureData types (waveform / digital data).

A MeasureData value is an OPAQUE built-in structure -- its component fields are
NOT serialized in the VI's VCTP (unlike a user cluster), so a Bundle/Unbundle-By-
Name or Get-<X>-Components node's field index has nothing to resolve against, and
its stored value has no type to read it with. Both come from one built-in table
(``data/measure_data.json``) keyed by the type's exact ``Flavor`` -- the
``MEASURE_DATA_FLAVOR`` member name pylabview writes to the extracted XML: the
component names by field index, and the flattened value layout.

Clean-room: the component names and their order are the PUBLIC, documented
LabVIEW waveform/digital-data components (NI docs -- e.g. the "Get Waveform
Components" function page), and the value layout is NI's "Flattened Data" page
(a waveform flattens like a cluster), never licensed source. The index is the
node's stored field index; gaps are the type's internal fields (e.g. the analog
waveform's error cluster at 3-6) that aren't user-extractable components.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]

from ._data import data_dir as _bundled_data_dir
from .models import ClusterField, LVType, LVTypeKind

# The closed set of flavor names pylabview can emit as <Flavor> (it writes the
# enum MEMBER NAME via enumOrIntToName(td.dtFlavor())). Every key in the data
# file's "flavors" map must be one of these -- a mismatch is a typo, caught loud
# at load time rather than silently never matching.
_VALID_FLAVORS: frozenset[str] = frozenset(
    m.name for m in _lv_datatype.MEASURE_DATA_FLAVOR
)

# The flavor of a timestamp, which is read directly (not as a cluster of parts).
TIMESTAMP_FLAVOR: str = _lv_datatype.MEASURE_DATA_FLAVOR.TimeStamp.name

# The primitive type names a value layout may use (pylabview's type codes).
_VALID_PRIMITIVES: frozenset[str] = frozenset(m.name for m in _lv_datatype.TD_FULL_TYPE)


@dataclass(frozen=True)
class MeasureFlavor:
    """One flavor's table entry: its extractable components by field index (empty
    when it has none) and its flattened value as a type (None when the layout is
    not known)."""

    components: dict[int, str]
    value_type: LVType | None


# flavor name -> its entry, built once from the data file.
_TABLE: dict[str, MeasureFlavor] | None = None


class _LayoutBuilder:
    """Builds the ``LVType`` of a value layout spec (see the data file's
    ``_comment`` for the spec grammar), validating every name it meets."""

    def __init__(self, value_layouts: dict[str, object]) -> None:
        self._layouts = value_layouts

    def build(
        self, spec: object, samples: LVType | None = None, path: tuple[str, ...] = ()
    ) -> LVType:
        if isinstance(spec, str):
            if spec not in _VALID_PRIMITIVES:
                raise ValueError(
                    f"measure_data.json: {spec!r} is not a TD_FULL_TYPE member"
                )
            return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=spec)
        if not isinstance(spec, dict):
            raise ValueError(f"measure_data.json: bad value spec {spec!r}")
        if "measure" in spec:
            flavor = spec["measure"]
            if flavor not in _VALID_FLAVORS:
                raise ValueError(
                    f"measure_data.json: unknown flavor {flavor!r} in a value spec"
                )
            return LVType(
                kind=LVTypeKind.PRIMITIVE,
                underlying_type="MeasureData",
                measure_flavor=flavor,
            )
        if "array" in spec:
            dimensions = spec.get("dimensions", 1)
            if not isinstance(dimensions, int):
                raise ValueError(
                    f"measure_data.json: array dimensions {dimensions!r} not an int"
                )
            return LVType(
                kind=LVTypeKind.ARRAY,
                underlying_type="Array",
                element_type=self.build(spec["array"], samples, path),
                dimensions=dimensions,
            )
        if "cluster" in spec:
            fields = spec["cluster"]
            if not isinstance(fields, list):
                raise ValueError(f"measure_data.json: bad cluster spec {fields!r}")
            return LVType(
                kind=LVTypeKind.CLUSTER,
                underlying_type="Cluster",
                fields=[
                    ClusterField(name=name, type=self.build(field, samples, path))
                    for name, field in fields
                ],
            )
        if "layout" in spec:
            name = spec["layout"]
            if name not in self._layouts:
                raise ValueError(
                    f"measure_data.json: value spec names undefined layout "
                    f"{name!r} (have {sorted(self._layouts)})"
                )
            if name in path:
                raise ValueError(
                    f"measure_data.json: value layout {name!r} contains itself"
                )
            given = spec.get("samples")
            inner = self.build(given, samples, path) if given is not None else None
            return self.build(self._layouts[name], inner, (*path, name))
        if spec.get("samples") is True:
            if samples is None:
                raise ValueError(
                    "measure_data.json: a layout uses its samples but none were given"
                )
            return samples
        raise ValueError(f"measure_data.json: bad value spec {spec!r}")


def _components(fields: object, where: str) -> dict[int, str]:
    if not isinstance(fields, dict):
        raise ValueError(
            f"measure_data.json: layout {where!r} must be an object of "
            f"index -> component name"
        )
    resolved: dict[int, str] = {}
    for idx, field in fields.items():
        if not isinstance(field, str):
            raise ValueError(
                f"measure_data.json: layout {where!r} index {idx!r} maps to "
                f"non-string component name {field!r}"
            )
        # idx is a JSON object key -> always str, so int() raises only
        # ValueError (never TypeError) on a non-integer key.
        try:
            resolved[int(idx)] = field
        except ValueError:
            raise ValueError(
                f"measure_data.json: layout {where!r} has non-integer index {idx!r}"
            ) from None
    return resolved


def _load_table() -> dict[str, MeasureFlavor]:
    """Load and resolve the flavor table from the data file (lazy, one-time).

    The file names component ``layouts`` and flattened ``value_layouts`` once and
    assigns each flavor to them (so the 15 flavors that share the waveform
    layout name it once). Validated on load: every flavor key must be a real
    ``MEASURE_DATA_FLAVOR`` name, every component layout and value layout it
    names must be defined and every primitive a real ``TD_FULL_TYPE`` member --
    else ``ValueError`` (no silent misses).

    Gracefully degrades to an empty table if the data file is missing (matches
    the rest of the resolver layer -- callers keep their own bracketed-index
    fallback), but a PRESENT-but-invalid file raises.
    """
    global _TABLE
    if _TABLE is not None:
        return _TABLE

    data_path = _bundled_data_dir() / "measure_data.json"
    if not data_path.exists():
        _TABLE = {}
        return _TABLE

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("layouts"), dict)
        or not isinstance(raw.get("value_layouts"), dict)
        or not isinstance(raw.get("flavors"), dict)
    ):
        raise ValueError(
            "measure_data.json: expected a top-level object with 'layouts', "
            "'value_layouts' and 'flavors' objects"
        )
    layouts = {
        name: _components(fields, name) for name, fields in raw["layouts"].items()
    }
    builder = _LayoutBuilder(raw["value_layouts"])

    table: dict[str, MeasureFlavor] = {}
    for flavor, entry in raw["flavors"].items():
        if flavor not in _VALID_FLAVORS:
            raise ValueError(
                f"measure_data.json: unknown flavor {flavor!r} -- not a "
                f"MEASURE_DATA_FLAVOR member ({sorted(_VALID_FLAVORS)})"
            )
        if not isinstance(entry, dict):
            raise ValueError(
                f"measure_data.json: flavor {flavor!r} must be an object, "
                f"got {entry!r}"
            )
        components: dict[int, str] = {}
        if "components" in entry:
            layout_name = entry["components"]
            if layout_name not in layouts:
                raise ValueError(
                    f"measure_data.json: flavor {flavor!r} references undefined "
                    f"layout {layout_name!r} (have {sorted(layouts)})"
                )
            components = layouts[layout_name]
        value_type = builder.build(entry["value"]) if "value" in entry else None
        table[flavor] = MeasureFlavor(components, value_type)

    _TABLE = table
    return _TABLE


def measure_data_field_name(flavor: str | None, field_index: int | None) -> str | None:
    """Component name for a MeasureData ``flavor`` at ``field_index``, or ``None``
    when the flavor/index isn't a known extractable component (caller keeps its
    own bracketed-index fallback)."""
    if not flavor or field_index is None:
        return None
    entry = _load_table().get(flavor)
    return entry.components.get(field_index) if entry else None


def measure_data_value_type(flavor: str | None) -> LVType | None:
    """The flattened structure of a MeasureData value of ``flavor`` (a cluster of
    its parts), or ``None`` for a flavor whose structure is not known."""
    entry = _load_table().get(flavor) if flavor else None
    return entry.value_type if entry else None
