"""A VI saved by LabVIEW 8.2 or 8.5 keeps its constants' values in its default
data, in sections laid out before LabVIEW 8.6 (``_pylabview_lv8``). Real corpus
VIs of both versions are read through describe, lvnet and generate and show the
values. (The front-panel controls of such a VI take no default from that default
data: ``test_default_data_controls.py``.)"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.extractor import extract_llb
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests.corpus_graphs import load_corpus_vi

_SAMPLES = Path(".lvkit/cache/samples").resolve()
_PICO = _SAMPLES / "picosdk-ni-labview-examples"
_TRIM = (
    _SAMPLES
    / "JKI-VI-Tester/source/Build Support/Package Builder Utilities"
    / "Trim Whitespace__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
)
_NOT_DECODED = "not decoded"


def _views(vi: Path) -> tuple[str, str, str, list[Gap]]:
    """The describe text, verbose lvnet text and generated source of ``vi``, and
    the gaps generating it found."""
    graph, key = load_corpus_vi(vi)
    module = build_netlist_from_graph(graph, key)
    gaps: list[Gap] = []
    source = build_module(graph.get_vi_context(key), key, graph=graph, gaps=gaps)
    return (
        describe_vi(graph, key),
        render_lvnet(module, display_name="T.vi", verbose=True),
        source,
        gaps,
    )


@pytest.mark.needs_samples
def test_8_2_regex_constants_are_read() -> None:
    describe, lvnet, source, gaps = _views(_TRIM)
    assert r"String = '^[\\t\\n\\r\\s]*'" in describe
    assert r"String = '[\\t\\n\\r\\s]*$'" in describe
    assert r"'^[\\\\t\\\\n\\\\r\\\\s]*'" in lvnet
    assert r"'[\\\\t\\\\n\\\\r\\\\s]*$'" in lvnet
    assert _NOT_DECODED not in describe + lvnet
    assert source.count("re.search(") == 2
    assert gaps == []


@pytest.mark.needs_samples
def test_8_2_case_frames_carry_their_selector_values() -> None:
    describe, lvnet, source, _ = _views(_TRIM)
    assert 'Frame "0, 1":' in describe
    assert 'Frame "0, 2":' in describe
    assert 'frame "0", "1" :' in lvnet
    assert 'frame "0", "2" :' in lvnet
    assert "0 <= location <= 1" in source


@pytest.mark.needs_samples
def test_8_5_error_string_constants_are_read() -> None:
    describe, lvnet, source, _ = _views(_PICO / "shared" / "PicoErrorString2.vi")
    for text in (
        "String = 'PICO_STATUS OK '",
        "String = 'Error code '",
        "String = 'PICO_STATUS Unknown Error '",
        "String = '\\r'",
        "I32 = -1",
    ):
        assert text in describe
    assert "y        : I32 = -1" in lvnet
    assert _NOT_DECODED not in describe + lvnet
    assert "string = 'PICO_STATUS OK '" in source
    assert "'PICO_STATUS Unknown Error ' + '\\r' + 'Error code '" in source
    assert "-1 == index_of_element" in source


@pytest.mark.needs_samples
def test_8_5_status_codes_are_read() -> None:
    describe, lvnet, source, _ = _views(_PICO / "shared" / "PicoStatus2.vi")
    for code in (39, 281, 282, 286):
        assert f"y        : U32 = {code}" in lvnet
        assert f"{code} == pico_status" in source
    assert _NOT_DECODED not in describe + lvnet


@pytest.mark.needs_samples
def test_8_5_library_member_constants_are_read() -> None:
    library = _PICO / "ps3000/32-bit-examples/PicoScope3000.llb"
    member = extract_llb(library) / "PicoScope3000UnitInfo.vi"
    describe, lvnet, source, _ = _views(member)
    assert "(unnamed): I16 = 256" in describe
    assert "(unnamed): I32 = 7" in describe
    assert "in   stringLength        : I16    = 256" in lvnet
    assert "for i in range(7):" in source
    assert _NOT_DECODED not in describe + lvnet
