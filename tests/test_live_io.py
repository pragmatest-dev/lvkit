"""The ``live_io`` module a generated package holds and the function that takes it
(``codegen/live_io_module.py``, ``codegen/panel_hooks.py``): a function called
without one runs as it is without a front panel, and a subclass of the package's
own ``LiveIO`` sees every write and answers every read."""

from __future__ import annotations

import ast
import importlib
import inspect
import queue
from typing import Any

from lvkit.codegen.live_io_module import LIVE_IO_MODULE_SOURCE
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.event_diagrams import event_loop_vi
from tests.test_live_hooks import NAME, _counting_loop, _stop_loop


def _live_io(package: Any) -> Any:
    return importlib.import_module(f"{package.module.__package__}.live_io")


def test_the_package_holds_the_live_io_module_the_function_imports(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _counting_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert (package.root / "live_io.py").read_text(encoding="utf-8") == (
        LIVE_IO_MODULE_SOURCE
    )
    assert "from .live_io import LiveIO, NO_LIVE_IO" in package.source
    parameter = inspect.signature(package.function).parameters["live_io"]
    assert parameter.default is _live_io(package).NO_LIVE_IO
    # Called with nothing, or with the base class, the function gives its result.
    assert package.function().count == 3
    assert package.function(live_io=_live_io(package).LiveIO()).count == 3


def test_a_vi_without_a_panel_access_has_no_live_io_module(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    vi.wire(x, out)
    project.add(vi)
    package = project.generate(NAME)
    assert not (package.root / "live_io.py").exists()
    assert "live_io" not in package.source


def test_a_subclass_of_the_generated_class_sees_each_write_and_ends_the_loop(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _stop_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    base = _live_io(package).LiveIO
    seen: list[tuple[str, Any]] = []
    reads = 0

    class Scripted(base):  # type: ignore[misc, valid-type]
        def write(self, name: Any, value: Any) -> None:
            seen.append((name.value, value))

        def read(self, name: Any, value: Any) -> Any:
            nonlocal reads
            reads += 1
            return reads >= 4

    result = package.function(stop=False, live_io=Scripted())
    assert result.count == 4
    assert seen == [("count", 1), ("count", 2), ("count", 3), ("count", 4)]
    assert reads == 4


def test_a_subclass_that_sets_no_events_leaves_the_timeout_frame_to_run(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=10))
    package = project.generate("Events.vi")
    assert "= live_io.events or Queue()" in package.source
    function = package.function
    live_io = _live_io(package).LiveIO()
    assert live_io.events is None
    assert function(0, 0, live_io=live_io).timed_out is True
    given: queue.Queue = queue.Queue()
    live_io.events = given
    kinds = package.module.EventsEvent
    given.put((package.module.EventsValue.GAIN, 2, kinds.VALUE_CHANGE, 1))
    assert function(0, 0, live_io=live_io).last_new == 2


def test_a_control_labeled_events_keeps_its_name(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    events = vi.control("events", BOOL, REQUIRED)
    count = vi.indicator("count", I32)
    loop = vi.loop("whileLoop")
    vi.place_inside(events, loop.scopes[0])
    vi.place_inside(count, loop.scopes[0])
    vi.wire(vi.int_constant(1), count)
    vi.wire(events, loop.faces[0])
    project.add(vi)
    package = project.generate(NAME)
    assert list(inspect.signature(package.function).parameters) == [
        "events",
        "live_io",
    ]
    assert package.function(events=True).count == 1


def test_the_generated_package_imports_nothing_from_lvkit(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _counting_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    for module in package.root.rglob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "lvkit" for a in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "lvkit"
