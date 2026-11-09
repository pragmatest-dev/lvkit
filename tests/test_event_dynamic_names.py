"""An Event Structure frame that handles a dynamic User Event is named after the
registered row, from the type of the registration on the structure's dynamic
terminal: ``<Name>`` for a registration, ``<Field.Name>`` for a cluster of
registrations. Every view prints the same label."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph.event_registration import (
    dynamic_terminal_type,
    registration_rows,
    with_dynamic_names,
)
from lvkit.graph.models import EventStructureNode
from tests.corpus_graphs import load_corpus_vi
from tests.error_diagrams import Project, project  # noqa: F401
from tests.event_diagrams import (
    USER_EVENT,
    Vi,
    array_type,
    cluster_type,
    control_reference_type,
    dynamic_events_vi,
    registration_of,
    registration_type,
    registrations_cluster,
    user_event_frame,
    user_event_type,
)

_SAMPLES = Path(".lvkit/cache/samples")
_START_STOP = [("Start", USER_EVENT), ("Stop", USER_EVENT)]


def _node(graph, key) -> EventStructureNode:
    return next(n for n in graph.iter_nodes(key) if isinstance(n, EventStructureNode))


def _labels(views) -> list[str]:
    return [f.event_label for f in _node(views.graph, views.vi).frames]


def _assert_every_view_shows(views, labels: list[str]) -> None:
    assert _labels(views) == labels
    for label in labels:
        assert f"Frame {label}:" in views.describe
        assert f"frame {json.dumps(label)} :" in views.lvnet
        assert f"frame {json.dumps(label)} :" in views.terse
        assert json.dumps(label)[1:-1] in json.dumps(views.json)


def test_registered_rows_name_their_frames_in_every_view(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, _START_STOP),
            [user_event_frame(1), user_event_frame(2)],
        )
    )
    views = project.views("Dynamic.vi")
    _assert_every_view_shows(
        views,
        ["[0] Timeout", "[1] <Start>: User Event", "[2] <Stop>: User Event"],
    )
    assert (
        '"event_rows": [{"name": "Start", "event_type": 1000}, '
        '{"name": "Stop", "event_type": 1000}]'
    ) in json.dumps(views.json)
    events = [f.events[0] for f in _node(views.graph, views.vi).frames[1:]]
    assert [(e.dyn_index, e.dynamic_name) for e in events] == [
        (1, "Start"),
        (2, "Stop"),
    ]


def test_frame_order_need_not_match_row_order(project: Project) -> None:  # noqa: F811
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, _START_STOP),
            [user_event_frame(2), user_event_frame(1)],
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        ["[0] Timeout", "[1] <Stop>: User Event", "[2] <Start>: User Event"],
    )


def test_a_registration_through_a_loop_tunnel_names_the_frames(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, _START_STOP),
            [user_event_frame(1), user_event_frame(2)],
            in_loop=True,
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        ["[0] Timeout", "[1] <Start>: User Event", "[2] <Stop>: User Event"],
    )


def test_a_cluster_of_registrations_numbers_rows_across_its_fields(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registrations_cluster(vi, [("A", ["X"]), ("B", ["Y", "Z"])]),
            [user_event_frame(3), user_event_frame(1), user_event_frame(2)],
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        [
            "[0] Timeout",
            "[1] <B.Z>: User Event",
            "[2] <A.X>: User Event",
            "[3] <B.Y>: User Event",
        ],
    )


def test_a_row_that_is_not_a_user_event_keeps_the_label_it_had(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, [("Start", 0x4002), ("Stop", USER_EVENT)]),
            [user_event_frame(1), user_event_frame(2)],
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        [
            "[0] Timeout",
            "[1] <unknown event 0x000003E8>",
            "[2] <Stop>: User Event",
        ],
    )


def test_a_registration_with_no_rows_keeps_the_label_it_had(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, []),
            [user_event_frame(1)],
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        ["[0] Timeout", "[1] <unknown event 0x000003E8>"],
    )


def test_a_dynindex_with_no_row_keeps_the_label_it_had(
    project: Project,  # noqa: F811
) -> None:
    project.add(
        dynamic_events_vi(
            lambda vi: registration_type(vi, _START_STOP),
            [user_event_frame(3)],
        )
    )
    _assert_every_view_shows(
        project.views("Dynamic.vi"),
        ["[0] Timeout", "[1] <unknown event 0x000003E8>"],
    )


def _shows(rows_of, frames: list[int], expected: list[str], target: Project) -> None:
    target.add(dynamic_events_vi(rows_of, [user_event_frame(k) for k in frames]))
    _assert_every_view_shows(target.views("Dynamic.vi"), ["[0] Timeout", *expected])


def test_a_cluster_row_takes_an_index_and_so_does_each_of_its_elements(
    project: Project,  # noqa: F811
) -> None:
    def rows(vi: Vi) -> int:
        inner = [user_event_type(vi, "A"), user_event_type(vi, "B")]
        return registration_of(
            vi,
            [
                (user_event_type(vi, "Stop"), USER_EVENT),
                (cluster_type(vi, "Rpc", inner), USER_EVENT),
            ],
        )

    _shows(
        rows,
        [1, 2, 3, 4],
        [
            "[1] <Stop>: User Event",
            "[2] <unknown event 0x000003E8>",
            "[3] <Rpc.A>: User Event",
            "[4] <Rpc.B>: User Event",
        ],
        project,
    )


def test_a_nested_cluster_joins_every_level_of_names(
    project: Project,  # noqa: F811
) -> None:
    def rows(vi: Vi) -> int:
        inner = cluster_type(vi, "Inner", [user_event_type(vi, "Y")])
        outer = cluster_type(vi, "Outer", [user_event_type(vi, "X"), inner])
        return registration_of(vi, [(outer, USER_EVENT)])

    _shows(
        rows,
        [2, 4],
        ["[1] <Outer.X>: User Event", "[2] <Outer.Inner.Y>: User Event"],
        project,
    )


def test_an_array_row_is_one_index_named_by_its_number(
    project: Project,  # noqa: F811
) -> None:
    def rows(vi: Vi) -> int:
        return registration_of(
            vi,
            [
                (array_type(vi, user_event_type(vi, "E")), USER_EVENT),
                (user_event_type(vi, "Go"), USER_EVENT),
            ],
        )

    _shows(rows, [1, 2], ["[1] <#1>: User Event", "[2] <Go>: User Event"], project)


def test_a_control_row_takes_an_index(project: Project) -> None:  # noqa: F811
    def rows(vi: Vi) -> int:
        return registration_of(
            vi,
            [
                (user_event_type(vi, "Go"), USER_EVENT),
                (control_reference_type(vi, "ValChg"), 0x3C00),
                (user_event_type(vi, "Stop"), USER_EVENT),
            ],
        )

    _shows(
        rows,
        [3, 2],
        ["[1] <Stop>: User Event", "[2] <unknown event 0x000003E8>"],
        project,
    )


@pytest.mark.slow
@pytest.mark.needs_samples
@pytest.mark.parametrize(
    ("vi", "expected"),
    [
        (
            "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/"
            "Graphical Test Runner Support/Results Status Update Process__core.vi",
            [
                "[0] Timeout",
                "[1] <TestStartedEventRef>: User Event",
                "[2] <Exit>: User Event",
                "[3] <SuiteStatusChanged>: User Event",
                "[4] <ResultStatusChanged>: User Event",
            ],
        ),
        (
            "lv-flex-channel-examples/WaveGen/WaveGen.vi",
            [
                "[0] Timeout",
                "[1] <Params.Event Data>: User Event",
                "[2] <Action.Event Data>: User Event",
                "[3] <Generate.Generate Data>: User Event",
            ],
        ),
    ],
)
def test_real_vis_name_their_user_event_frames(vi: str, expected: list[str]) -> None:
    path = (_SAMPLES / vi).resolve()
    if not path.exists():
        pytest.skip(f"{path} not present")
    graph, key = load_corpus_vi(path)
    assert [f.event_label for f in _node(graph, key).frames] == expected


_SERVER_API_START_SYNC = (
    "measurement-plugin-labview/Source/gRPC/Generated APIs/ni/measurementlink/"
    "measurement/v2/RPC Service/MeasurementService/Server API/Start Sync.vi"
)

_DISPLAYED_FRAME_VIS = [
    "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/"
    "Graphical Test Runner Support/Results Status Update Process__core.vi",
    "JKI-VI-Tester/source/Classes/TestSuite/run.vi",
    "lv-flex-channel-examples/WaveGen/WaveGen.vi",
    "lv-flex-channel-examples/DAQmx AO/DAQ AO.vi",
    "measurement-plugin-labview/Source/gRPC/Generated APIs/ni/measurementlink/"
    "measurement/v2/Monitor for Stop.vi",
    "measurement-plugin-labview/Source/gRPC/Generated APIs/ni/measurementlink/"
    "measurement/v2/RPC Service/MeasurementService/RPC Methods/"
    "RPC Method MeasurementService GetMetadata.vi",
    "measurement-plugin-labview/Source/Runtime/Measurements/"
    "Measurement Service V2/Start Async.vi",
    "measurement-plugin-labview/Source/Runtime/Measurements/"
    "IMeasurement Service/Stop Handler Impl.vi",
]


@pytest.mark.slow
@pytest.mark.needs_samples
@pytest.mark.parametrize("vi", _DISPLAYED_FRAME_VIS)
def test_the_name_made_for_the_displayed_frame_is_the_label_the_file_stores(
    vi: str,
) -> None:
    """The frame LabVIEW last displayed keeps its stored label; the name this
    code makes for it must be the same text."""
    path = (_SAMPLES / vi).resolve()
    if not path.exists():
        pytest.skip(f"{path} not present")
    graph, key = load_corpus_vi(path)
    checked = 0
    for node in graph.iter_nodes(key):
        if not isinstance(node, EventStructureNode) or node.displayed_frame is None:
            continue
        frame = node.frames[node.displayed_frame]
        event = frame.events[-1] if frame.events else None
        if event is None or not event.dyn_index or event.type_code != USER_EVENT:
            continue
        rows = registration_rows(dynamic_terminal_type(node.terminals))
        made = with_dynamic_names(frame, frame.events, rows, None)
        assert made.event_label == frame.event_label
        checked += 1
    assert checked >= 1


@pytest.mark.slow
@pytest.mark.needs_samples
def test_a_registration_of_a_user_event_and_two_clusters_numbers_through_the_clusters():
    """Rows: Server Stop, the cluster Server RPC Methods {GetMetadata, Measure},
    the cluster Server Internal UEs: dynIndex 1 is the first row, 3 and 4 are the
    first cluster's elements."""
    path = (_SAMPLES / _SERVER_API_START_SYNC).resolve()
    if not path.exists():
        pytest.skip(f"{path} not present")
    graph, key = load_corpus_vi(path)
    assert [f.event_label for f in _node(graph, key).frames] == [
        "[0] Timeout",
        "[1] <Server Stop>: User Event",
        "[2] <Server RPC Methods.MeasurementService GetMetadata gRPC UE>: User Event",
        "[3] <Server RPC Methods.MeasurementService Measure gRPC UE>: User Event",
    ]
