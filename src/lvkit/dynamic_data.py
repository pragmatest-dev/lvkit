"""The dynamic data type's flavor of LabVIEW's MeasureData, and its Python form.

NI, "Merge Signals": "Internally, an array of waveforms composes" a dynamic data
wire, so a dynamic data value in generated Python is the list of its waveforms,
``list[nitypes.waveform.AnalogWaveform]``.
"""

from __future__ import annotations

import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]

DYNAMIC_DATA_FLAVOR: str = _lv_datatype.MEASURE_DATA_FLAVOR.Dynamicdata.name
DYNAMIC_DATA_ANNOTATION = "list[nitypes.waveform.AnalogWaveform]"
