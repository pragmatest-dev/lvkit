"""An Event Structure frame that handles a dynamic User Event is named after the
registered row, from the type of the registration on the structure's dynamic
terminal: ``<Name>`` for a registration, ``<Field.Name>`` for a cluster of
registrations. Every view prints the same label."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit.graph.models import EventStructureNode
from tests.corpus_graphs import load_corpus_vi
from tests.error_diagrams import Project, project  # noqa: F401
from tests.event_diagrams import (
    USER_EVENT,
    dynamic_events_vi,
    registration_type,
    registrations_cluster,
    user_event_frame,
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
