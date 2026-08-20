"""Built-in component names for LabVIEW MeasureData types (waveform / digital
data).

A MeasureData value is an OPAQUE built-in structure — its component fields are
NOT serialized in the VI's VCTP (unlike a user cluster), so a Bundle/Unbundle-By-
Name or Get-<X>-Components node's field index has nothing to resolve against.
These names come from a built-in table keyed by the type's ``Flavor`` instead.

Clean-room: the component names and their order are the PUBLIC, documented
LabVIEW waveform/digital-data components (NI docs — e.g. the "Get Waveform
Components" function page), never licensed source. The index is the node's stored
field index; gaps are the type's internal fields (e.g. the analog waveform's
error cluster at 3–6) that aren't user-extractable components.
"""

from __future__ import annotations

# Every waveform flavor — analog (Float64Waveform, Float32Waveform, Int*/UInt*/
# Complex*…) AND DigitalWaveform — shares this component layout, differing only in
# Y's element type (a digital waveform's Y is its digital data). field index ->
# component name; 3–6 are the internal error cluster (not extractable components).
_WAVEFORM: dict[int, str] = {0: "t0", 1: "dt", 2: "Y", 7: "attributes"}

# Exact-flavor tables for non-analog-waveform MeasureData.
_BY_FLAVOR: dict[str, dict[int, str]] = {
    "Digitaldata": {0: "transitions", 1: "data"},
}


def measure_data_field_name(flavor: str | None, field_index: int | None) -> str | None:
    """Component name for a MeasureData ``flavor`` at ``field_index``, or ``None``
    when the flavor/index isn't a known extractable component (caller keeps its
    own bracketed-index fallback)."""
    if not flavor or field_index is None:
        return None
    table = _BY_FLAVOR.get(flavor)
    if table is None and flavor.endswith("Waveform"):
        table = _WAVEFORM
    return table.get(field_index) if table else None
