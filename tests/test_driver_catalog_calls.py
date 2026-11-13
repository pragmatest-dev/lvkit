"""A call of an NI driver VI the catalog maps becomes the catalog's Python even
when no vi.lib folder is configured (the call has no file of its own): a call whose
terminal names differ from the catalog's still gets the entry's template, and a
polymorphic call records the file of the variant it runs. A vi.lib VI the catalog
does not list is still the missing-VI gap."""

from __future__ import annotations

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
    ``terminals`` are ``(index, direction, label, type, caller control name)``."""
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
        if direction == "input"
    ]
    wires = [
        Wire.from_terminals(from_terminal_id=f"fp:{i}", to_terminal_id=f"k:{i}")
        for i, direction, _, _, _ in terminals
        if direction == "input"
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
