"""Pico Technology's LabVIEW examples (ISC license): VIs saved in LabVIEW 8.5, 9.0
and 10.0 with Call Library Function Nodes, loaded with the shared VIs of their
submodule."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.pipeline import generate_python
from tests.corpus_graphs import load_corpus_vi

pytestmark = pytest.mark.needs_samples

ROOT = Path(".lvkit/cache/samples/picosdk-ni-labview-examples")
TRIGGER = ROOT / "ps2000a/PicoScope2000aLib/PicoScope2000aAdvancedTriggerSettings.vi"
GET_READINGS = ROOT / "usbtc08/USBTC08Lib/GetReadings.vi"
ERROR_HANDLER = ROOT / "ps2000/PicoScope2000Lib/PicoScope2000Errorhandler.vi"
STATUS = ROOT / "shared/PicoStatus.vi"


def _written(vi: Path, tmp_path: Path) -> Path:
    result = generate_python(vi, tmp_path, search_paths=[ROOT])
    return Path(result["vi_paths"][str(vi.resolve())])


def test_a_vi_saved_in_labview_85_loads_with_the_vi_it_calls() -> None:
    graph, key = load_corpus_vi(STATUS, ROOT)
    names = {node.name for node in graph.iter_nodes(key)}
    assert "Merge Errors.vi" in names, names


def test_call_library_nodes_call_the_whole_function_name() -> None:
    graph, key = load_corpus_vi(TRIGGER, ROOT)
    functions = {
        node.ext_func.function
        for node in graph.iter_nodes(key)
        if getattr(node, "ext_func", None) is not None
    }
    assert "ps2000aSetTriggerChannelDirections" in functions
    assert "ps2000aSetTriggerChannelDirecti" not in functions


def test_index_array_of_a_gap_constant_generates(tmp_path: Path) -> None:
    written = _written(GET_READINGS, tmp_path)
    assert not written.name.endswith(".error.py"), written.read_text()[:300]


def test_an_error_codes_typedef_unbundles_into_its_fields(tmp_path: Path) -> None:
    written = _written(ERROR_HANDLER, tmp_path)
    source = written.read_text()
    assert not written.name.endswith(".error.py"), source[:300]
    assert "len(None)" not in source


STREAMING = ROOT / "ps2000/PicoScope2000ExampleStreaming.vi"
WRITE_TDMS = ROOT / "shared/PicoWriteTDMS.vi"


def test_an_unbundle_element_stored_as_minus_one_is_a_gap_the_and_reads_as_none(
    tmp_path: Path,
) -> None:
    source = _written(STREAMING, tmp_path).read_text()
    assert (
        "An element of 'Bundle/Unbundle By Name' stores its field index as -1, "
        "which lvkit does not decode yet"
    ) in source
    assert "        if out_1 and greater_or_equal:" in source
    assert "'' and" not in source


def test_a_bundle_into_a_waveform_builds_the_value(tmp_path: Path) -> None:
    source = _written(WRITE_TDMS, tmp_path).read_text()
    assert "SimpleNamespace(t0=" in source
    assert "A Bundle into" not in source
