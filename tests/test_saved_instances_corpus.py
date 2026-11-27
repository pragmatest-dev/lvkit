"""A call of a malleable VI or Express VI runs the instance LabVIEW saved in the
caller's own file: the loader makes it a dependency of the caller (key
``<caller>/<instance name>``) with the caller's adapted types and each Type
Specialization Structure's own frame, and every view names the call as a call of
the ``.vim``. Real callers from the sample corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit import embedded_instances
from lvkit.graph import InMemoryVIGraph, load_vi_by_path
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import DisableStructureNode, VINode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.load_mode import LoadMode
from lvkit.models import LVTypeKind, TerminalDirection
from lvkit.pipeline import generate_python
from lvkit.run_report import ProblemKind
from tests.corpus_graphs import OPENG_USERLIB, load_corpus_vi

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples")
_GET_CONNECTIONS = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Tests/Tests.Runtime/Measurement Server"
    / "Get Connections"
)
_ONE_CONNECTION = (
    _GET_CONNECTIONS / "test_OneSessTwoPinOneInstrOneSiteReservation_"
    "CallNiDcPowerGetConnections1Pin1Connection_ReturnsOneConnection.vi"
)
_M_CONNECTIONS = (
    _GET_CONNECTIONS / "test_OneSessTwoPinOneInstrOneSiteReservation_"
    "CallNiDcPowerGetConnections1PinMConnections_ReturnsTwoConnections.vi"
)
_TEMPLATES = (
    _SAMPLES
    / "actor-framework/Providers/MessageMakerProvider/_Message Maker/_templates"
)
_CASTING_CALLERS = [
    _TEMPLATES / "Message Template/Do.vi",
    _TEMPLATES / "Concrete Message Template/Do.vi",
]
_VILIB_VIM_CALLER = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Runtime/Clients/Session Management V1"
    / "Connection Map/Is Site in Map.vi"
)
_JSON_EXAMPLE = _SAMPLES / "JSONtext/Examples/Example of JOBJ Tag.vi"
_PICO_EXAMPLE = (
    _SAMPLES / "picosdk-ni-labview-examples/usbtc08/USBTC08StreamingExample.vi"
)


def _graph(path: Path) -> tuple[InMemoryVIGraph, str]:
    return load_corpus_vi(path.resolve())


def _instance_calls(graph: InMemoryVIGraph, key: str) -> dict[str, VINode]:
    """The calls of the VI that run a saved instance, by the call's name."""
    calls: dict[str, VINode] = {}
    for node in graph.iter_nodes(key):
        if isinstance(node, VINode) and node.runs_saved_instance:
            calls.setdefault(node.name or "", node)
    return calls


def _active_frames(graph: InMemoryVIGraph, instance_key: str) -> list[int | None]:
    return [
        node.active_frame
        for node in graph.iter_nodes(instance_key)
        if isinstance(node, DisableStructureNode)
    ]


def test_instances_of_one_vim_load_as_two_dependencies_with_their_own_frames() -> None:
    graph_one, key_one = _graph(_ONE_CONNECTION)
    graph_m, key_m = _graph(_M_CONNECTIONS)

    call_one = _instance_calls(graph_one, key_one)["Assert Connections.vim"]
    call_m = _instance_calls(graph_m, key_m)["Assert Connections.vim"]

    # Each caller runs its own copy, a VI saved inside the caller with no file.
    assert call_one.callee_key != call_m.callee_key
    assert call_one.callee_key is not None and call_m.callee_key is not None
    assert graph_one.embedding_vi(call_one.callee_key) == key_one
    assert graph_m.embedding_vi(call_m.callee_key) == key_m
    assert Path(call_one.callee_key).parent == Path(key_one)
    assert graph_one.get_vi_source_path(call_one.callee_key) is None
    # The Type Specialization Structure keeps the frame LabVIEW chose for the
    # types wired to the call: 1 (Build Array) for the scalar, 0 for the array.
    assert _active_frames(graph_one, call_one.callee_key) == [1]
    assert _active_frames(graph_m, call_m.callee_key) == [0]
    # The call is named, listed and catalogued as a call of the ``.vim`` file.
    assert call_one.instance_of_key is not None
    assert Path(call_one.instance_of_key).name == "Assert Connections.vim"
    assert call_one.instance_of_key == call_m.instance_of_key
    assert (
        graph_one.call_qualified_name(call_one)
        == "MeasurementServerTests.lvlib:Assert Connections.vim"
    )
    assert call_one.instance_of_key in graph_one.get_vi_dependencies(key_one)
    assert call_one.callee_key not in graph_one.get_vi_dependencies(key_one)
    # An instance is not a file: no index entry, no document page.
    assert call_one.callee_key not in graph_one.list_file_vis()
    assert call_one.callee_key in graph_one.list_vis()


def test_instance_places_under_its_callers_path() -> None:
    graph, key = _graph(_ONE_CONNECTION)
    instance_key = _instance_calls(graph, key)["Assert Connections.vim"].callee_key
    assert instance_key is not None

    # The caller is no library file, so neither it nor its instance has an
    # installation path; the instance is placed by the caller's.
    assert graph.recorded_path(key) is None
    assert graph.recorded_path(instance_key) is None
    assert graph.instance_vim(instance_key) == (
        "MeasurementServerTests.lvlib:Assert Connections.vim"
    )


def test_casting_utility_instance_is_loaded_though_its_vim_is_not_found() -> None:
    for caller in _CASTING_CALLERS:
        graph, key = _graph(caller)
        call = _instance_calls(graph, key)["Casting Utility For Actors.vim"]

        assert call.callee_key is not None
        # LabVIEW saved frame 0 (``activeDiag`` absent); frame 1 is the one it
        # shows as "Ignored".
        assert _active_frames(graph, call.callee_key) == [0]
        # The ``.vim`` is a ``<vilib>`` file that is not here: the call still names
        # it, and it is the one listed dependency.
        assert call.instance_of_key is None
        assert graph.call_qualified_name(call) == "Casting Utility For Actors.vim"
        listed = [Path(dep).name for dep in graph.get_vi_dependencies(key)]
        assert "Casting Utility For Actors.vim" in listed
        assert not any(name.endswith(".vi") and "-" in name[:9] for name in listed)


def test_a_vilib_vim_with_catalog_python_is_not_loaded_as_an_instance() -> None:
    graph, key = _graph(_VILIB_VIM_CALLER)

    [call] = [
        node
        for node in graph.iter_nodes(key)
        if isinstance(node, VINode)
        and node.name == "Search Unsorted 1D Array.vim"
    ]

    assert call.is_embedded_instance
    assert not call.runs_saved_instance
    assert call.callee_key is None
    assert call.instance_of_key is None
    assert not [k for k in graph.list_vis() if graph.embedding_vi(k) == key]


def test_from_json_text_instance_has_the_callers_cluster_type() -> None:
    graph, key = _graph(_JSON_EXAMPLE)
    call = _instance_calls(graph, key)["From JSON Text.vim"]
    assert call.callee_key is not None

    [data_type] = [
        t
        for t in graph.pane_terminals(call.callee_key, TerminalDirection.INPUT)
        if t.name == "Data Type and Default Value"
    ]

    assert data_type.lv_type is not None
    assert data_type.lv_type.kind is LVTypeKind.CLUSTER
    assert [f.name for f in data_type.lv_type.fields] == ["<JOBJ>Config", "Other Stuff"]
    # The ``.vim`` itself, shared by every caller, declares a Variant there.
    assert call.instance_of_key is not None
    [declared] = [
        t
        for t in graph.pane_terminals(call.instance_of_key, TerminalDirection.INPUT)
        if t.name == "Data Type and Default Value"
    ]
    assert declared.lv_type is not None
    assert declared.lv_type.type_descriptor() == "Variant"


def test_express_vi_instances_load_with_their_own_pane() -> None:
    graph, key = _graph(_PICO_EXAMPLE)
    instance_keys = [k for k in graph.list_vis() if graph.embedding_vi(k) == key]

    assert len(instance_keys) == 9
    for instance_key in instance_keys:
        assert graph.is_loaded_vi(instance_key)
        assert graph.instance_vim(instance_key) is None
        assert [
            t.name for t in graph.pane_terminals(instance_key, TerminalDirection.INPUT)
        ] == ["Array"]
        assert [
            t.name for t in graph.pane_terminals(instance_key, TerminalDirection.OUTPUT)
        ] == ["Dynamic Data Type"]
    # The calls keep the names the caller records, and the manifest the stored path.
    text = render_lvnet(
        build_netlist_from_graph(graph, key), display_name="x", verbose=True
    )
    assert "    subVI Instance:0 " in text
    assert "(missing)" not in "\n".join(
        line for line in text.splitlines() if "Instance:" in line
    )


def test_every_view_names_a_saved_instance_call_as_a_call_of_the_vim() -> None:
    graph, key = _graph(_ONE_CONNECTION)

    describe = describe_vi(graph, key)
    lvnet = render_lvnet(
        build_netlist_from_graph(graph, key), display_name="x", verbose=True
    )

    vim = "MeasurementServerTests.lvlib:Assert Connections.vim"
    # The call's pane is the instance's: the scalar the caller wires.
    assert (
        f"  {vim}: (expected connections: Connection with Session{{connection, "
        "session}, actual connections: Connection with Session{connection, "
        "session}, error in: Error) -> (pass: TF, result: String, error out: Error)"
    ) in describe.splitlines()
    assert f"    subVI Assert_Connections_292 : {vim}" in lvnet
    assert f"    subVI   {vim} " in lvnet
    assert "Instance:" not in describe
    assert "Instance:" not in lvnet


_RESERVATION = (
    _SAMPLES
    / "measurement-plugin-labview/Source/Runtime/Clients/Session Management V1"
    / "Session Reservation"
)
_CALLER = _RESERVATION / "Create Reservation Annotations.vi"
_INSTANCE_NAME = "96ebd3b6-2171-43d4-b6b2-fee63be2a44d.vi"


@pytest.fixture
def unreadable_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Create Reservation Annotations.vi``'s saved copy of Create Annotations.vim
    cannot be parsed."""
    parse_vi = embedded_instances.parse_vi

    def failing(*args: object, **kwargs: object):
        override = kwargs.get("source_override")
        if override is not None and Path(str(override)).name == _INSTANCE_NAME:
            raise ValueError("block diagram heap is damaged")
        return parse_vi(*args, **kwargs)

    monkeypatch.setattr(embedded_instances, "parse_vi", failing)


def test_unreadable_instance_is_a_problem_and_its_call_a_gap(
    unreadable_instance: None, tmp_path: Path
) -> None:
    graph, key = load_vi_by_path(
        _CALLER.resolve(), LoadMode.MINIMAL, userlib_root=OPENG_USERLIB, layout=False
    )

    [call] = [
        n
        for n in graph.iter_nodes(key)
        if isinstance(n, VINode) and (n.name or "").startswith("Create Annotations")
    ]
    reason = (
        "the copy of the VI saved in this VI's own file for this call "
        f"(instance {_INSTANCE_NAME} of Create Annotations.vim) could not be read: "
        "ValueError: block diagram heap is damaged"
    )
    assert call.instance_unreadable == reason
    assert not call.runs_saved_instance
    assert call.callee_key is not None
    assert Path(call.callee_key).name == "Create Annotations.vim"
    [problem] = [p for p in graph.problems() if p.kind is ProblemKind.LVKIT_ERROR]
    assert problem.reason == call.instance_unreadable
    assert problem.vi.endswith("Create Reservation Annotations.vi")

    summary = generate_python(_CALLER, tmp_path)

    [caller] = tmp_path.rglob("create_reservation_annotations.py")
    text = caller.read_text(encoding="utf-8")
    assert "raise NotImplementedError(" in text
    assert "could not be read: ValueError: block diagram heap is damaged" in text
    assert [gap.reason for gap in summary["gaps"]] == [reason]
