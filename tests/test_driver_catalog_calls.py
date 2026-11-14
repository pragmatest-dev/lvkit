"""A call of an NI driver VI the catalog maps becomes the catalog's Python even
when no vi.lib folder is configured (the call has no file of its own): a call whose
terminal names differ from the catalog's still gets the entry's template, and a
polymorphic call records the file of the variant it runs. A vi.lib VI the catalog
does not list is still the missing-VI gap."""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.pipeline import create_import_resolver
from lvkit.vilib_resolver import get_resolver
from tests.helpers import build_graph

TASKS = "<vilib>/DAQmx/configure/task.llb"
TIMING = "<vilib>/DAQmx/configure/timing.llb"
TRIGGER = "<vilib>/DAQmx/configure/trigger.llb"
FGEN = "<instrlib>/niFgen/niFgen.llb"
_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
_DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
_CLUSTER = LVType(kind=LVTypeKind.CLUSTER)


def _generate(
    tmp_path: Path,
    *,
    name: str,
    path: str,
    selector: str | None,
    terminals: list[tuple[int, str, str, LVType, str]],
    gaps: list[Gap],
) -> str:
    """The module of a caller whose one call records ``path`` for the VI ``name``.
    ``terminals`` are ``(index, direction, label, type, caller control name)``; an
    input with no control name is left unwired."""
    graph = InMemoryVIGraph()
    out = tmp_path / "pkg"
    out.mkdir()
    resolver = create_import_resolver(
        "pkg",
        out,
        {"caller.vi": out / "caller.py"},
        out / "caller.py",
        caller_key="caller.vi",
        graph=graph,
    )
    call_terminals = [
        FPTerminal(
            id=f"k:{i}", index=i, direction=direction, name=label, lv_type=lv_type
        )
        for i, direction, label, lv_type, _ in terminals
    ]
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name=name,
        node_type="iUse",
        qualified_path=path,
        poly_variant_name=selector,
        terminals=call_terminals,
    )
    inputs = [
        FPTerminal(
            id=f"fp:{i}",
            index=n,
            direction="input",
            name=control,
            is_public=True,
            is_indicator=False,
            lv_type=lv_type,
        )
        for n, (i, direction, _, lv_type, control) in enumerate(terminals)
        if direction == "input" and control
    ]
    wires = [
        Wire.from_terminals(from_terminal_id=f"fp:{i}", to_terminal_id=f"k:{i}")
        for i, direction, _, _, control in terminals
        if direction == "input" and control
    ]
    context = VIContext(name="caller.vi", inputs=inputs, outputs=[], data_flow=wires)
    built = build_graph(context, "caller.vi", [call], graph=graph)
    return build_module(
        context, "caller.vi", import_resolver=resolver, graph=built, gaps=gaps
    )


def test_start_task_is_the_catalogs_inline_python_without_a_vilib_folder(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    # The VI's own terminal is named "task/channels in", the catalog's "task in";
    # the wired "error in" is one the catalog has no number for, so the run learns
    # the call's terminals.
    source = _generate(
        tmp_path,
        name="DAQmx Start Task.vi",
        path=f"{TASKS}/DAQmx Start Task.vi",
        selector=None,
        terminals=[
            (0, "input", "task/channels in", _REF, "task"),
            (4, "output", "task out", _REF, ""),
            (11, "input", "error in", _CLUSTER, "error in"),
        ],
        gaps=gaps,
    )
    assert gaps == []
    line = next(ln.strip() for ln in source.splitlines() if ".start()" in ln)
    started: list[bool] = []

    class FakeTask:
        def start(self) -> None:
            started.append(True)

    exec(line, {"task": FakeTask()})  # noqa: S102
    assert started == [True]


def test_polymorphic_call_recording_the_variants_file_uses_that_entry(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Timing.vi",
        path=f"{TIMING}/DAQmx Timing (Implicit).vi",
        selector="Implicit",
        terminals=[
            (0, "input", "task/channels in", _REF, "task"),
            (1, "input", "sample mode", _DBL, "mode"),
            (2, "input", "samples per channel", _DBL, "samples"),
        ],
        gaps=gaps,
    )
    assert gaps == []
    assert "task.timing.cfg_implicit_timing(" in source


def test_selector_finds_the_variant_under_the_polymorphic_vis_name() -> None:
    resolver = get_resolver()
    # The call records the file NI's variant has, which the catalog names otherwise.
    recorded = (
        "<vilib>/DAQmx/create/channels.llb/DAQmx Create Channel (AI-Voltage-Basic).vi"
    )
    found = resolver.resolve_call(
        recorded,
        "AI Voltage",
        wrapper_fallback=False,
        vi_name="DAQmx Create Virtual Channel.vi",
    )
    assert found is not None
    assert found.name == "DAQmx Create Virtual Channel (AI Voltage).vi"
    # Without the polymorphic VI's name the variant is looked up under the recorded
    # file's name and is not found.
    assert resolver.resolve_call(recorded, "AI Voltage", wrapper_fallback=False) is None


def test_a_vilib_vi_the_catalog_does_not_list_is_still_the_missing_vi_gap(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Not A Real VI.vi",
        path=f"{TASKS}/DAQmx Not A Real VI.vi",
        selector=None,
        terminals=[(0, "input", "task in", _REF, "task")],
        gaps=gaps,
    )
    assert [g.kind for g in gaps] == ["missing_vi"]
    assert "NotImplementedError" in source


_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _run_call(source: str, marker: str, namespace: dict[str, object]) -> dict:
    """Run the one generated statement holding ``marker`` (and the imports the
    module has of a name the statement uses) in ``namespace``."""
    line = next(ln.strip() for ln in source.splitlines() if marker in ln)
    exec(line, namespace)  # noqa: S102
    return namespace


def test_an_unwired_input_the_call_labels_takes_its_number_from_the_label(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    _generate(
        tmp_path,
        name="DAQmx Timing.vi",
        path=f"{TIMING}/DAQmx Timing (Implicit).vi",
        selector="Implicit",
        terminals=[
            (0, "input", "task/channels in", _REF, "task"),
            (1, "input", "sample mode", _I32, "mode"),
            (2, "input", "samples per channel", _I32, ""),
        ],
        gaps=gaps,
    )
    # The input is found (no "unmapped" gap); NI's page gives it no default.
    assert [g.kind for g in gaps] == ["unknown_default"]


def test_a_selector_wrapped_over_two_lines_finds_the_variant(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Read.vi",
        path="<vilib>/DAQmx/read.llb/DAQmx Read (Digital 1D Bool 1Chan 1Samp).vi",
        selector="Digital 1D Bool\n1Chan 1Samp",
        terminals=[
            (0, "input", "task/channels in", _REF, "task"),
            (4, "output", "task out", _REF, ""),
            (6, "output", "data", _CLUSTER, ""),
            (9, "input", "timeout", _DBL, ""),
        ],
        gaps=gaps,
    )
    assert gaps == []
    reads: list[float] = []

    class FakeTask:
        def read(self, timeout: float) -> list[bool]:
            reads.append(timeout)
            return [True, False]

    _run_call(source, ".read(", {"task": FakeTask()})
    # NI: "The default timeout is 10 seconds."
    assert reads == [10.0]


class _FakeEdge(IntEnum):
    FALLING = 10171
    RISING = 10280


def _trigger(tmp_path: Path, gaps: list[Gap], edge_wired: bool) -> str:
    return _generate(
        tmp_path,
        name="DAQmx Trigger.vi",
        path=f"{TRIGGER}/DAQmx Start Trigger (Digital Edge).vi",
        selector="Start\nDigital Edge",
        terminals=[
            (0, "input", "task/channels in", _REF, "task"),
            (4, "output", "task out", _REF, ""),
            (5, "input", "source", _REF, "source"),
            (7, "input", "edge", _I32, "edge" if edge_wired else ""),
        ],
        gaps=gaps,
    )


def test_start_trigger_digital_edge_passes_the_source_and_the_edge(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    source = _trigger(tmp_path, gaps, edge_wired=True)
    assert gaps == []
    calls: list[tuple[str, object]] = []

    class FakeStartTrigger:
        def cfg_dig_edge_start_trig(
            self, trigger_source: str, trigger_edge: object
        ) -> None:
            calls.append((trigger_source, trigger_edge))

    class FakeTask:
        triggers = type("T", (), {"start_trigger": FakeStartTrigger()})()

    _run_call(
        source,
        ".cfg_dig_edge_start_trig(",
        {"task": FakeTask(), "source": "/Dev1/PFI0", "edge": 10171, "Edge": _FakeEdge},
    )
    assert calls == [("/Dev1/PFI0", _FakeEdge.FALLING)]


def test_start_trigger_with_the_edge_unwired_is_a_gap_as_nis_page_gives_no_default(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    _trigger(tmp_path, gaps, edge_wired=False)
    assert [g.kind for g in gaps] == ["unknown_default"]


def test_ni_fgen_is_done_reads_the_session(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="niFgen Is Done.vi",
        path=f"{FGEN}/niFgen Is Done.vi",
        selector=None,
        terminals=[
            (0, "output", "error out", _CLUSTER, ""),
            (2, "output", "Done", _BOOL, ""),
            (3, "output", "Instrument Handle Out", _REF, ""),
            (8, "input", "error in (no error)", _CLUSTER, "error in"),
            (11, "input", "Instrument Handle", _REF, "session"),
        ],
        gaps=gaps,
    )
    assert gaps == []

    asked: list[bool] = []

    class FakeSession:
        def is_done(self) -> bool:
            asked.append(True)
            return True

    _run_call(source, ".is_done()", {"session": FakeSession()})
    assert asked == [True]


def test_ni_fgen_close_closes_the_session(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="niFgen Close.vi",
        path=f"{FGEN}/niFgen Close.vi",
        selector=None,
        terminals=[
            (0, "output", "error out", _CLUSTER, ""),
            (8, "input", "error in (no error)", _CLUSTER, "error in"),
            (11, "input", "Instrument Handle", _REF, "session"),
        ],
        gaps=gaps,
    )
    assert gaps == []
    closed: list[bool] = []

    class FakeSession:
        def close(self) -> None:
            closed.append(True)

    _run_call(source, ".close()", {"session": FakeSession()})
    assert closed == [True]


def test_ni_digital_load_pattern_loads_the_file(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="niDigital Load Pattern.vi",
        path="<instrlib>/niDigital/niDigital.llb/niDigital Load Pattern.vi",
        selector=None,
        terminals=[
            (0, "input", "instrument handle", _REF, "session"),
            (4, "output", "instrument handle out", _REF, ""),
            (5, "input", "pattern file", _PATH, "pattern"),
            (11, "input", "error in", _CLUSTER, "error in"),
            (15, "output", "error out", _CLUSTER, ""),
        ],
        gaps=gaps,
    )
    assert gaps == []
    loaded: list[object] = []

    class FakeSession:
        def load_pattern(self, file_path: object) -> None:
            loaded.append(file_path)

    _run_call(
        source, ".load_pattern(", {"session": FakeSession(), "pattern": "p.digipat"}
    )
    assert loaded == ["p.digipat"]


def test_ni_digital_load_pin_map_loads_the_file(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="niDigital Load Pin Map.vi",
        path="<instrlib>/niDigital/niDigital.llb/niDigital Load Pin Map.vi",
        selector=None,
        terminals=[
            (0, "input", "instrument handle", _REF, "session"),
            (4, "output", "instrument handle out", _REF, ""),
            (5, "input", "pin map file", _PATH, "pin_map"),
            (11, "input", "error in", _CLUSTER, "error in"),
            (15, "output", "error out", _CLUSTER, ""),
        ],
        gaps=gaps,
    )
    assert gaps == []
    loaded: list[object] = []

    class FakeSession:
        def load_pin_map(self, file_path: object) -> None:
            loaded.append(file_path)

    _run_call(
        source, ".load_pin_map(", {"session": FakeSession(), "pin_map": "m.pinmap"}
    )
    assert loaded == ["m.pinmap"]
