"""Built-in component names for LabVIEW MeasureData types (waveform / digital
data).

A MeasureData value is an OPAQUE built-in structure -- its component fields are
NOT serialized in the VI's VCTP (unlike a user cluster), so a Bundle/Unbundle-By-
Name or Get-<X>-Components node's field index has nothing to resolve against.
These names come from a built-in table (``data/measure_data.json``) keyed by the
type's exact ``Flavor`` -- the ``MEASURE_DATA_FLAVOR`` member name pylabview
writes to the extracted XML -- instead.

Clean-room: the component names and their order are the PUBLIC, documented
LabVIEW waveform/digital-data components (NI docs -- e.g. the "Get Waveform
Components" function page), never licensed source. The index is the node's stored
field index; gaps are the type's internal fields (e.g. the analog waveform's
error cluster at 3-6) that aren't user-extractable components.
"""

from __future__ import annotations

import json

import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]

from ._data import data_dir as _bundled_data_dir

# The closed set of flavor names pylabview can emit as <Flavor> (it writes the
# enum MEMBER NAME via enumOrIntToName(td.dtFlavor())). Every key in the data
# file's "flavors" map must be one of these -- a mismatch is a typo, caught loud
# at load time rather than silently never matching.
_VALID_FLAVORS: frozenset[str] = frozenset(
    m.name for m in _lv_datatype.MEASURE_DATA_FLAVOR
)

# flavor name -> {field index: component name}, built once from the data file.
_TABLE: dict[str, dict[int, str]] | None = None


def _load_table() -> dict[str, dict[int, str]]:
    """Load and resolve the flavor -> {index: name} table from the data file
    (lazy, one-time).

    The file is a layout map: named component ``layouts`` plus a ``flavors``
    map assigning each flavor to a layout (so the 16 flavors that share the
    waveform layout name it once, not sixteen copies). Validated on load:
    every flavor key must be a real ``MEASURE_DATA_FLAVOR`` name and every
    value a defined layout -- else ``ValueError`` (no silent misses).

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
        or not isinstance(raw.get("flavors"), dict)
    ):
        raise ValueError(
            "measure_data.json: expected a top-level object with 'layouts' "
            "and 'flavors' objects"
        )
    layouts_raw: dict[str, object] = raw["layouts"]
    flavors_raw: dict[str, object] = raw["flavors"]

    # layout name -> {int index: component name}
    layouts: dict[str, dict[int, str]] = {}
    for name, fields in layouts_raw.items():
        if not isinstance(fields, dict):
            raise ValueError(
                f"measure_data.json: layout {name!r} must be an object of "
                f"index -> component name"
            )
        resolved: dict[int, str] = {}
        for idx, field in fields.items():
            if not isinstance(field, str):
                raise ValueError(
                    f"measure_data.json: layout {name!r} index {idx!r} maps to "
                    f"non-string component name {field!r}"
                )
            # idx is a JSON object key -> always str, so int() raises only
            # ValueError (never TypeError) on a non-integer key.
            try:
                resolved[int(idx)] = field
            except ValueError:
                raise ValueError(
                    f"measure_data.json: layout {name!r} has non-integer index {idx!r}"
                ) from None
        layouts[name] = resolved

    table: dict[str, dict[int, str]] = {}
    for flavor, layout_name in flavors_raw.items():
        if flavor not in _VALID_FLAVORS:
            raise ValueError(
                f"measure_data.json: unknown flavor {flavor!r} -- not a "
                f"MEASURE_DATA_FLAVOR member ({sorted(_VALID_FLAVORS)})"
            )
        if not isinstance(layout_name, str):
            raise ValueError(
                f"measure_data.json: flavor {flavor!r} layout ref must be a "
                f"string, got {layout_name!r}"
            )
        if layout_name not in layouts:
            raise ValueError(
                f"measure_data.json: flavor {flavor!r} references undefined "
                f"layout {layout_name!r} (have {sorted(layouts)})"
            )
        table[flavor] = layouts[layout_name]

    _TABLE = table
    return _TABLE


def measure_data_field_name(flavor: str | None, field_index: int | None) -> str | None:
    """Component name for a MeasureData ``flavor`` at ``field_index``, or ``None``
    when the flavor/index isn't a known extractable component (caller keeps its
    own bracketed-index fallback)."""
    if not flavor or field_index is None:
        return None
    layout = _load_table().get(flavor)
    return layout.get(field_index) if layout else None
