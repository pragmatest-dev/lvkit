"""The ``user_events`` module a generated package holds: a user event is a
reference, so a cluster or class holding one copies by value around the same event;
and the rule that the module is written only when a generated module imports it."""

from __future__ import annotations

import copy
import queue
import types
from pathlib import Path

from lvkit.codegen.user_events_module import USER_EVENTS_MODULE_SOURCE
from lvkit.pipeline import _write_user_events_module
from tests.error_diagrams import (
    Project,
    project,  # noqa: F401
)
from tests.test_user_event_codegen import lifecycle_vi


def helper() -> types.ModuleType:
    """A fresh copy of the generated ``user_events`` module."""
    module = types.ModuleType("user_events")
    exec(compile(USER_EVENTS_MODULE_SOURCE, "user_events.py", "exec"), module.__dict__)  # noqa: S102
    return module


def generate_on(event: list, value: object) -> None:
    """What Generate User Event writes: a message to each registered queue."""
    for registered in event:
        registered.put((event, value, "User Event", None))


def test_a_cluster_holding_a_user_event_copies_around_the_same_event() -> None:
    event = helper().UserEvent()
    cluster = {"event": event, "count": 1, "items": [1]}
    other = copy.deepcopy(cluster)
    other["count"] = 2
    other["items"].append(2)
    assert other["event"] is event
    assert (cluster["count"], cluster["items"]) == (1, [1])
    registered: queue.Queue = queue.Queue()
    other["event"].append(registered)
    generate_on(cluster["event"], 5)
    assert registered.get(block=False) == (event, 5, "User Event", None)


def test_a_class_holding_a_user_event_copies_around_the_same_event() -> None:
    class Holder:
        def __init__(self, event: list) -> None:
            self.event = event
            self.count = 1
            self.items = [1]

    held = Holder(helper().UserEvent())
    other = copy.deepcopy(held)
    other.count = 2
    other.items.append(2)
    assert other.event is held.event
    assert (held.count, held.items) == (1, [1])
    registered: queue.Queue = queue.Queue()
    other.event.append(registered)
    generate_on(held.event, "x")
    assert registered.get(block=False)[1] == "x"


def test_a_user_event_is_still_a_list() -> None:
    event = helper().UserEvent()
    event.append(1)
    assert isinstance(event, list)
    event.clear()
    assert event == []


def test_create_user_event_makes_the_packages_user_event(
    project: Project,  # noqa: F811
) -> None:
    project.add(lifecycle_vi())
    package = project.generate("Stopper.vi")
    assert package.gaps == []
    assert "= UserEvent()" in package.source
    assert "import UserEvent" in package.source
    assert package.function().stop_seen is True
    assert "lvkit" not in package.source


def test_user_events_py_is_written_only_when_a_module_imports_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _write_user_events_module(tmp_path)
    assert not (tmp_path / "user_events.py").exists()
    (tmp_path / "uses.py").write_text(
        "from .user_events import UserEvent\n", encoding="utf-8"
    )
    _write_user_events_module(tmp_path)
    assert (tmp_path / "user_events.py").read_text(
        encoding="utf-8"
    ) == USER_EVENTS_MODULE_SOURCE
