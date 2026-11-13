"""A Property Node bound to an INDICATOR of the VI whose ``Value`` row writes
assigns the indicator's variable, the same as a wire or a Local Variable writing
it (``codegen/nodes/property_node.py``, ``codegen/local_indicators.py``);
``Value (Signaling)`` on an indicator, and a ``Value`` read of an indicator the
function does not hold, stay gaps. Each case is a tiny diagram, generated and
called."""

from __future__ import annotations

from tests.error_diagrams import (
    I32,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.live_io_recorder import Recorder
from tests.panel_diagrams import value_property

NAME = "Prop.vi"


def _string_hex(text: str) -> str:
    return f"{len(text):08X}" + text.encode().hex().upper()


def _text(vi: Vi, text: str) -> int:
    return vi.constant(STR, _string_hex(text))


def _writes_the_control(vi: Vi) -> None:
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString")
    out = vi.indicator("out", STR, ddo_class="stdString")
    (value,) = value_property(vi, out, ["Value"])
    vi.wire(s, value)


def test_a_value_write_is_the_only_writer_of_a_pane_indicator(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _writes_the_control(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(s="hello").out == "hello"
    assert package.function(s="again").out == "again"


def test_a_value_write_after_a_wire_write_wins(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    out = vi.indicator("out", STR, ddo_class="stdString")
    vi.wire(_text(vi, "a"), out)
    (value,) = value_property(vi, out, ["Value"])
    vi.wire(_text(vi, "b"), value)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function().out == "b"


def test_the_write_is_reported_to_the_panel_hooks(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _writes_the_control(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert "live_io.write(PropValue.OUT, out)" in package.source
    recorder = Recorder()
    package.function(s="hi", live_io=recorder)
    assert ("out", "hi") in recorder.calls


def test_a_value_write_of_an_indicator_is_kept_when_a_local_variable_reads_it(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    count = vi.indicator("count", I32)
    seen = vi.indicator("seen", I32)
    (value,) = value_property(vi, count, ["Value"])
    vi.wire(n, value)
    vi.wire(vi.local_variable(count), seen)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(n=5).count == 5
    kept = [k for k in vars(package.module) if k.startswith("_lv_indicator_")]
    assert len(kept) == 1
    assert getattr(package.module, kept[0]) == 5


def test_value_signaling_on_an_indicator_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    out = vi.indicator("out", STR, ddo_class="stdString")
    (value,) = value_property(vi, out, ["Value (Signaling)"])
    vi.wire(_text(vi, "b"), value)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "binding model" in str(package.gaps[0])


def test_a_value_read_of_a_held_indicator_is_what_was_written(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    out = vi.indicator("out", STR, ddo_class="stdString")
    seen = vi.indicator("seen", STR, ddo_class="stdString")
    write, read = value_property(vi, out, ["Value", "Value"])
    vi.wire(_text(vi, "b"), write)
    vi.wire(read, seen)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function().seen == "b"


def test_a_value_read_of_an_unwritten_indicator_is_its_default_and_is_kept(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    count = vi.indicator("count", I32)
    seen = vi.indicator("seen", I32)
    (read,) = value_property(vi, count, ["Value"])
    vi.wire(read, seen)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function().seen == 0
    assert package.function().seen == 0
